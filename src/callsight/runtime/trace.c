/*
 * Portable compile-time function instrumentation runtime.
 *
 * The host project's sources are compiled with -finstrument-functions, so
 * each function entry/exit calls the __cyg_profile_* hooks below. This file
 * is compiled WITHOUT the flag and every function here carries
 * no_instrument_function, so hook code can never trigger itself.
 *
 * Design notes:
 *  - All state is per-thread (TLS); there are no locks in the hot path.
 *  - Events are raw addresses + timestamps; symbol resolution happens
 *    offline (`callsight analyze` + addr2line), keeping hook cost minimal.
 *  - Hooks stay inert (one predictable branch) unless TRACE_ENABLE=1.
 *  - No project dependencies: this file is meant to be dropped into any
 *    C/C++ codebase as-is.
 *
 * Streaming mode (TRACE_SHM=/name): flushes go to a POSIX shared-memory
 * ring (see trace_shm.h) instead of files, so a traced device accumulates
 * nothing on disk. A separate trace_stream client drains the ring and
 * forwards events over TCP. If the ring fills faster than the client
 * drains, events are DROPPED (counted in the ring header) — profiling must
 * never stall the workload. If the shm segment cannot be attached, the
 * runtime warns and falls back to file mode.
 *
 * Thread filter (TRACE_THREADS="sort-*,worker-1"): comma-separated glob
 * patterns matched against the thread name (see pthread_setname_np) once,
 * at the thread's first hook call. Non-matching threads record nothing.
 * Unset = all threads.
 */

/* pthread_getname_np (TRACE_THREADS filter) is a GNU extension; define it
 * before any libc header so this file stays drop-in for projects that
 * don't set it. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

#include "trace.h"
#include "trace_shm.h"

#include <fnmatch.h>

#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#define NOINSTR __attribute__((no_instrument_function))

/* Events buffered per thread before an fwrite flush */
#define TRACE_BUF_CAPACITY 8192u

/* --- Global configuration (initialized once) --- */

static pthread_once_t  g_trace_once = PTHREAD_ONCE_INIT;
static pthread_key_t   g_trace_key;          /* TLS cleanup on thread exit */
static int             g_trace_enabled = 0;
static char            g_trace_dir[512] = "traces";
static uint64_t        g_trace_max = 0;      /* 0 = unlimited */
static atomic_uint_fast64_t g_trace_count = 0;
static int             g_trace_stopped = 0;  /* set once cap is reached */
static trace_shm_header_t *g_shm = NULL;     /* non-NULL: streaming mode */
static char            g_thread_patterns[512] = ""; /* TRACE_THREADS globs */

/* --- Per-thread state --- */

typedef struct {
    trace_event_t buf[TRACE_BUF_CAPACITY];
    uint32_t      len;
    FILE         *out;
    uint32_t      tid;
    int           in_hook;    /* reentrancy guard */
    int           skip;       /* thread filtered out by TRACE_THREADS */
    uint32_t      skip_checks; /* recheck cadence while skipped */
} trace_tls_t;

static __thread trace_tls_t *tl_trace = NULL;

/* --- Time --- */

NOINSTR static uint64_t trace_now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* --- Flush --- */

/* Streaming flush: copy the thread's batch into the shm ring. Events that
 * don't fit are dropped and counted — see the file header comment. */
NOINSTR static void trace_shm_flush(trace_tls_t *tls) {
    trace_shm_header_t *h = g_shm;
    uint64_t bytes = (uint64_t)tls->len * sizeof(trace_event_t);
    trace_shm_lock(h);
    uint64_t avail = h->capacity - (h->head - h->tail);
    if (bytes <= avail) {
        h->head = trace_shm_put(h, tls->buf, bytes);
    } else {
        uint64_t fits = avail / sizeof(trace_event_t);
        if (fits > 0)
            h->head = trace_shm_put(h, tls->buf,
                                    fits * sizeof(trace_event_t));
        h->dropped += tls->len - fits;
    }
    trace_shm_unlock(h);
    tls->len = 0;
}

NOINSTR static void trace_tls_flush(trace_tls_t *tls) {
    if (tls->len == 0) return;
    if (g_shm) {
        trace_shm_flush(tls);
    } else if (tls->out) {
        fwrite(tls->buf, sizeof(trace_event_t), tls->len, tls->out);
        tls->len = 0;
    }
}

NOINSTR void trace_flush(void) {
    if (tl_trace) trace_tls_flush(tl_trace);
}

/* pthread key destructor: flush + close when a thread exits */
NOINSTR static void trace_tls_destroy(void *ptr) {
    trace_tls_t *tls = (trace_tls_t *)ptr;
    if (!tls) return;
    trace_tls_flush(tls);
    if (tls->out) fclose(tls->out);
    free(tls);
}

/* atexit handler: flush the main thread's buffer, detach from the ring */
NOINSTR static void trace_atexit(void) {
    if (tl_trace) trace_tls_flush(tl_trace);
    if (g_shm) __sync_fetch_and_sub(&g_shm->writers, 1);
}

/* --- One-time global init --- */

NOINSTR static void trace_global_init(void) {
    const char *env = getenv("TRACE_ENABLE");
    g_trace_enabled = (env && env[0] == '1');

    env = getenv("TRACE_DIR");
    if (env && env[0] != '\0') {
        snprintf(g_trace_dir, sizeof(g_trace_dir), "%s", env);
    }

    env = getenv("TRACE_MAX");
    if (env) {
        g_trace_max = strtoull(env, NULL, 10);
    }

    env = getenv("TRACE_THREADS");
    if (env) {
        snprintf(g_thread_patterns, sizeof(g_thread_patterns), "%s", env);
    }

    if (!g_trace_enabled) return;

    env = getenv("TRACE_SHM");
    if (env && env[0] != '\0') {
        uint32_t size = TRACE_SHM_DEF_SIZE;
        const char *sz = getenv("TRACE_SHM_SIZE");
        if (sz) {
            uint64_t v = strtoull(sz, NULL, 10);
            if (v >= 4096 && v <= UINT32_MAX) size = (uint32_t)v;
        }
        g_shm = trace_shm_attach(env, size);
        if (g_shm) {
            __sync_fetch_and_add(&g_shm->writers, 1);
        } else {
            fprintf(stderr, "callsight: cannot attach shm %s, "
                            "falling back to trace files\n", env);
        }
    }

    if (!g_shm)
        mkdir(g_trace_dir, 0755); /* ignore EEXIST */
    pthread_key_create(&g_trace_key, trace_tls_destroy);
    atexit(trace_atexit);
}

/* --- Per-thread lazy init --- */

/* Match the calling thread's name against the TRACE_THREADS glob list.
 * Called at thread start and periodically while the thread is skipped
 * (threads set their name after their first hooks fire). */
NOINSTR static int trace_thread_filtered(void) {
    if (g_thread_patterns[0] == '\0') return 0;

    char name[16] = "";
    pthread_getname_np(pthread_self(), name, sizeof(name));

    /* comma-separated globs, matched without strtok (not thread-safe) */
    const char *p = g_thread_patterns;
    while (*p) {
        const char *comma = strchr(p, ',');
        size_t len = comma ? (size_t)(comma - p) : strlen(p);
        char pat[64];
        if (len >= sizeof(pat)) len = sizeof(pat) - 1;
        memcpy(pat, p, len);
        pat[len] = '\0';
        if (fnmatch(pat[0] == ' ' ? pat + 1 : pat, name, 0) == 0)
            return 0;
        if (!comma) break;
        p = comma + 1;
    }
    return 1;
}

/* Open this thread's output (file mode) or confirm streaming (shm mode).
 * Returns 1 when the thread is ready to record. Split out of tls_get so a
 * thread that matched TRACE_THREADS late can complete its setup then. */
NOINSTR static int trace_tls_activate(trace_tls_t *tls) {
    if (g_shm)
        return 1;  /* streaming: nothing per-thread to open */

    char path[600];
    snprintf(path, sizeof(path), "%s/trace.%d.%u.bin",
             g_trace_dir, (int)getpid(), tls->tid);
    tls->out = fopen(path, "ab");
    if (!tls->out)
        return 0;

    trace_file_header_t hdr;
    memset(&hdr, 0, sizeof(hdr));
    memcpy(hdr.magic, TRACE_FILE_MAGIC, sizeof(hdr.magic));
    hdr.version = TRACE_FILE_VERSION;
    hdr.event_size = (uint32_t)sizeof(trace_event_t);
    fwrite(&hdr, sizeof(hdr), 1, tls->out);
    return 1;
}

NOINSTR static trace_tls_t *trace_tls_get(void) {
    trace_tls_t *tls = tl_trace;
    if (tls) return tls;

    tls = calloc(1, sizeof(*tls));
    if (!tls) return NULL;

    tls->tid = (uint32_t)syscall(SYS_gettid);
    tls->skip = trace_thread_filtered();
    if (!tls->skip && !trace_tls_activate(tls)) {
        free(tls);
        return NULL;
    }

    tl_trace = tls;
    pthread_setspecific(g_trace_key, tls);
    return tls;
}

/* --- Hooks --- */

NOINSTR static void trace_record(void *this_fn, void *call_site, uint8_t kind) {
    pthread_once(&g_trace_once, trace_global_init);
    if (!g_trace_enabled || g_trace_stopped) return;

    trace_tls_t *tls = trace_tls_get();
    if (!tls || tls->in_hook) return;
    if (tls->skip) {
        /* Threads commonly set their name AFTER the first hook fires, so
         * a one-time check would filter them out under their inherited
         * default name. Re-check every 64 hook calls until matched; on a
         * match the thread completes the setup it skipped. */
        if (tls->skip == 1 && (++tls->skip_checks & 63u) == 0 &&
            !trace_thread_filtered())
            tls->skip = trace_tls_activate(tls) ? 0 : 2; /* 2 = dead */
        return;
    }
    tls->in_hook = 1;

    if (g_trace_max > 0) {
        uint64_t n = atomic_fetch_add(&g_trace_count, 1);
        if (n >= g_trace_max) {
            g_trace_stopped = 1;
            trace_tls_flush(tls);
            tls->in_hook = 0;
            return;
        }
    }

    trace_event_t *ev = &tls->buf[tls->len++];
    ev->ts_ns = trace_now_ns();
    ev->func_addr = (uint64_t)(uintptr_t)this_fn;
    ev->caller_addr = (uint64_t)(uintptr_t)call_site;
    ev->tid = tls->tid;
    ev->kind = kind;
    ev->_pad[0] = ev->_pad[1] = ev->_pad[2] = 0;

    if (tls->len >= TRACE_BUF_CAPACITY) {
        trace_tls_flush(tls);
    }

    tls->in_hook = 0;
}

NOINSTR void __cyg_profile_func_enter(void *this_fn, void *call_site) {
    trace_record(this_fn, call_site, TRACE_EVENT_ENTER);
}

NOINSTR void __cyg_profile_func_exit(void *this_fn, void *call_site) {
    trace_record(this_fn, call_site, TRACE_EVENT_EXIT);
}
