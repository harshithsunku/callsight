/*
 * Compile-time function instrumentation runtime.
 *
 * This file is linked into the `instrument` build profile. Every other
 * translation unit is compiled with -finstrument-functions, so each function
 * entry/exit calls the __cyg_profile_* hooks below. This file itself is
 * compiled WITHOUT the flag (see Makefile) and every function here carries
 * no_instrument_function, so hook code can never trigger itself.
 *
 * Design notes:
 *  - All state is per-thread (TLS); there are no locks in the hot path.
 *  - Events are raw addresses + timestamps; symbol resolution happens offline
 *    (tools/trace_analyze.py + addr2line), keeping hook cost minimal.
 *  - Hooks stay inert (one predictable branch) unless MATRIXLAB_TRACE=1.
 */

#include "trace.h"

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

NOINSTR static void trace_tls_flush(trace_tls_t *tls) {
    if (tls->out && tls->len > 0) {
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

/* atexit handler: make sure the main thread's buffer is flushed */
NOINSTR static void trace_atexit(void) {
    if (tl_trace) trace_tls_flush(tl_trace);
}

/* --- One-time global init --- */

NOINSTR static void trace_global_init(void) {
    const char *env = getenv("MATRIXLAB_TRACE");
    g_trace_enabled = (env && env[0] == '1');

    env = getenv("MATRIXLAB_TRACE_DIR");
    if (env && env[0] != '\0') {
        snprintf(g_trace_dir, sizeof(g_trace_dir), "%s", env);
    }

    env = getenv("MATRIXLAB_TRACE_MAX");
    if (env) {
        uint64_t v = strtoull(env, NULL, 10);
        g_trace_max = v;
    }

    if (!g_trace_enabled) return;

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
