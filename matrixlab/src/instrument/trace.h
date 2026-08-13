#ifndef MATRIXLAB_INSTRUMENT_TRACE_H
#define MATRIXLAB_INSTRUMENT_TRACE_H

/*
 * Compile-time function instrumentation runtime.
 *
 * Built into the `instrument` profile (see Makefile): all other sources are
 * compiled with -finstrument-functions, which makes GCC emit calls to the
 * two __cyg_profile_* hooks below at every function entry/exit. This file is
 * compiled WITHOUT the flag, so the hooks cannot recurse.
 *
 * Collection is inert unless enabled at runtime:
 *   MATRIXLAB_TRACE=1        enable trace collection (default: off)
 *   MATRIXLAB_TRACE_DIR=dir  output directory (default: ./traces)
 *   MATRIXLAB_TRACE_MAX=N    stop after N events globally (default: 0 = unlimited)
 *
 * Output: one binary file per thread, <dir>/trace.<pid>.<tid>.bin, resolved
 * offline with tools/trace_analyze.py.
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

#endif /* MATRIXLAB_INSTRUMENT_TRACE_H */
