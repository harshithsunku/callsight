#ifndef INSTRUMENT_TRACE_H
#define INSTRUMENT_TRACE_H

/*
 * Portable compile-time function instrumentation runtime.
 *
 * Drop-in design: this file is self-contained and has no project
 * dependencies. The host project compiles its sources with
 * -finstrument-functions (see gen_flags.py for selective coverage), which
 * makes GCC/Clang emit calls to the two __cyg_profile_* hooks below at
 * every function entry/exit. The runtime itself is compiled WITHOUT the
 * flag, so the hooks cannot recurse.
 *
 * Collection is inert unless enabled at runtime:
 *   TRACE_ENABLE=1     enable trace collection (default: off)
 *   TRACE_DIR=dir      output directory (default: ./traces)
 *   TRACE_MAX=N        stop after N events globally (default: 0 = unlimited)
 *   TRACE_SHM=/name    stream events to a shared-memory ring instead of
 *                      files (drained by a trace_stream client; see
 *                      trace_shm.h). Falls back to files if unavailable.
 *   TRACE_SHM_SIZE=N   ring capacity in bytes (default: 16 MiB)
 *   TRACE_THREADS=globs  trace only threads whose name matches one of the
 *                      comma-separated globs (e.g. "sort-*,worker-1");
 *                      default: all threads
 *
 * Output: one binary file per thread, <dir>/trace.<pid>.<tid>.bin, resolved
 * offline with trace_analyze.py. In streaming mode the server side writes
 * equivalent trace.stream.*.bin files.
 */

#include <stdint.h>

#define TRACE_FILE_MAGIC   "MLTRACE\0"
#define TRACE_FILE_VERSION 1u

/* On-disk file header (16 bytes) */
typedef struct {
    char     magic[8];   /* TRACE_FILE_MAGIC */
    uint32_t version;    /* TRACE_FILE_VERSION */
    uint32_t event_size; /* sizeof(trace_event_t), sanity check */
} trace_file_header_t;

/* On-disk event record (32 bytes, fixed layout, little-endian) */
typedef struct {
    uint64_t ts_ns;       /* CLOCK_MONOTONIC_RAW nanoseconds */
    uint64_t func_addr;   /* address of the entered/exited function */
    uint64_t caller_addr; /* return address in the caller */
    uint32_t tid;         /* kernel thread id */
    uint8_t  kind;        /* 0 = enter, 1 = exit */
    uint8_t  _pad[3];
} trace_event_t;

#define TRACE_EVENT_ENTER 0u
#define TRACE_EVENT_EXIT  1u

/* Compiler-inserted hooks (called for every instrumented function) */
void __cyg_profile_func_enter(void *this_fn, void *call_site)
    __attribute__((no_instrument_function));
void __cyg_profile_func_exit(void *this_fn, void *call_site)
    __attribute__((no_instrument_function));

/* Flush the calling thread's pending events (mainly for tests) */
void trace_flush(void) __attribute__((no_instrument_function));

#endif /* INSTRUMENT_TRACE_H */
