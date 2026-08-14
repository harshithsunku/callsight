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
 *    offline (trace_analyze.py + addr2line), keeping hook cost minimal.
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
 */

#include "trace.h"
#include "trace_shm.h"

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

/* --- Per-thread state --- */

typedef struct {
    trace_event_t buf[TRACE_BUF_CAPACITY];
    uint32_t      len;
    FILE         *out;
    uint32_t      tid;
    int           in_hook;    /* reentrancy guard */
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
            fprintf(stderr, "tracekit: cannot attach shm %s, "
                            "falling back to trace files\n", env);
        }
    }

    if (!g_shm)
        mkdir(g_trace_dir, 0755); /* ignore EEXIST */
    pthread_key_create(&g_trace_key, trace_tls_destroy);
    atexit(trace_atexit);
}

/* --- Per-thread lazy init --- */

NOINSTR static trace_tls_t *trace_tls_get(void) {
    trace_tls_t *tls = tl_trace;
    if (tls) return tls;

    tls = calloc(1, sizeof(*tls));
    if (!tls) return NULL;

    tls->tid = (uint32_t)syscall(SYS_gettid);

    if (g_shm) {
        /* streaming mode: no per-thread file, events go to the shm ring */
        tl_trace = tls;
        pthread_setspecific(g_trace_key, tls);
        return tls;
    }

    char path[600];
    snprintf(path, sizeof(path), "%s/trace.%d.%u.bin",
             g_trace_dir, (int)getpid(), tls->tid);
    tls->out = fopen(path, "ab");
    if (!tls->out) {
        free(tls);
        return NULL;
    }

    trace_file_header_t hdr;
    memset(&hdr, 0, sizeof(hdr));
    memcpy(hdr.magic, TRACE_FILE_MAGIC, sizeof(hdr.magic));
    hdr.version = TRACE_FILE_VERSION;
    hdr.event_size = (uint32_t)sizeof(trace_event_t);
    fwrite(&hdr, sizeof(hdr), 1, tls->out);

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
