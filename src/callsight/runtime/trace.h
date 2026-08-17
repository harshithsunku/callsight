#ifndef INSTRUMENT_TRACE_H
#define INSTRUMENT_TRACE_H

/*
 * Portable compile-time function instrumentation runtime.
 *
 * Drop-in design: this file is self-contained and has no project
 * dependencies. The host project compiles its sources with
 * -finstrument-functions (see `callsight flags` for selective coverage),
 * which makes GCC emit calls to the two __cyg_profile_* hooks below at every
 * function entry/exit. The runtime itself is compiled WITHOUT the flag, so
 * the hooks cannot recurse.
 *
 * Collection is inert unless enabled at runtime:
 *   TRACE_ENABLE=1     enable trace collection (default: off)
 *   TRACE_DIR=dir      output directory (default: ./traces, resolved to an
 *                      absolute path at startup so a chdir() cannot scatter
 *                      segments)
 *   TRACE_MODE=mode    events (default) writes every entry/exit;
 *                      summary aggregates in-process and writes only
 *                      per-function totals at exit — constant memory, so a
 *                      run of any length costs nothing on disk
 *
 * Capture limits (event mode; profiling must never fill the device):
 *   TRACE_MAX_MB=N     total on-disk budget for this process, default 512.
 *                      0 = unlimited, and it has to be asked for.
 *   TRACE_FULL=policy  what to do when the budget is reached:
 *                      stop (default) keeps the beginning of the run;
 *                      wrap keeps the most recent TRACE_MAX_MB — a flight
 *                      recorder for "what happened just before the hang"
 *   TRACE_SEG_MB=N     segment size, i.e. rotation granularity (default 32)
 *   TRACE_MIN_FREE_MB=N  stop if the filesystem falls below this much free
 *                      space (default 64; 0 disables the check)
 *   TRACE_MAX=N        stop after N events globally (default 0 = unlimited)
 *
 * Clock and filtering:
 *   TRACE_CLOCK=src    auto (default) uses the invariant CPU cycle counter
 *                      when the hardware has one and falls back to
 *                      CLOCK_MONOTONIC; mono, raw and tsc force the choice
 *   TRACE_THREADS=globs  trace only threads whose name matches one of the
 *                      comma-separated globs (e.g. "sort-*,worker-1");
 *                      default: all threads
 *
 * Streaming (no files at all, for constrained devices):
 *   TRACE_SHM=/name    flush into a POSIX shared-memory ring instead of
 *                      files, drained by a trace_stream client (see
 *                      trace_shm.h). Falls back to files if unavailable.
 *   TRACE_SHM_SIZE=N   ring capacity in bytes (default: 16 MiB)
 *
 * Output: <dir>/trace.<pid>.<tid>.<seq>.bin in event mode,
 * <dir>/trace.summary.<pid>.<tid>.bin in summary mode, both read by
 * `callsight analyze`. In streaming mode the server side writes equivalent
 * trace.stream.*.bin files.
 *
 * Byte order: the agent writes its NATIVE order and the reader detects it.
 * The device is the constrained side of this system and the analysis host is
 * not, so the host does the swapping. Detection needs no extra field: every
 * header opens with a byte-string magic (order-neutral) followed by a small
 * u32 version, so a version that does not fit in 16 bits means the producer's
 * order is the opposite of the reader's. TRACE_HF_BIGENDIAN records it
 * explicitly as well, which costs nothing and makes a hexdump readable.
 */

#include <stdint.h>

/*
 * On-disk layout is an ABI. These structs are written to files and sockets
 * and read by a host that may be a different architecture entirely, so their
 * sizes are asserted rather than assumed: a target whose padding rules differ
 * must fail here, at compile time, not silently produce files the analyzer
 * misreads. (All of them are padding-clean on both 32- and 64-bit ABIs by
 * construction — every 64-bit field lands on an 8-byte boundary — and these
 * assertions are what keeps that true as fields are added.)
 */
#if defined(__cplusplus)
#define TRACE_ASSERT_SIZE(type, bytes) \
    static_assert(sizeof(type) == (bytes), #type " must be " #bytes " bytes")
#else
#define TRACE_ASSERT_SIZE(type, bytes) \
    _Static_assert(sizeof(type) == (bytes), #type " must be " #bytes " bytes")
#endif

#define TRACE_FILE_MAGIC   "MLTRACE\0"
#define TRACE_FILE_VERSION 2u

/*
 * On-disk event-file header.
 *
 * The first 16 bytes are laid out exactly as version 1 so that any reader
 * can identify the file and its version before it knows the rest; readers
 * must then skip `header_size` bytes to reach the first event rather than
 * assuming a size, which is what lets a future version add fields without
 * breaking this one. Version 1 files (a bare 16-byte magic/version/
 * event_size header, no load bias, nanosecond timestamps) still analyze.
 */
typedef struct {
    char     magic[8];    /* TRACE_FILE_MAGIC */
    uint32_t version;     /* TRACE_FILE_VERSION */
    uint32_t event_size;  /* sizeof(trace_event_t), sanity check */
    uint32_t header_size; /* bytes from file start to the first event */
    uint32_t flags;       /* TRACE_HF_* */
    uint64_t load_bias;   /* subtract from event addresses before symbolizing
                           * (nonzero for a PIE; 0 when linked -no-pie) */
    uint64_t tick_hz;     /* timestamp ticks per second; 0 = already ns */
    uint64_t t0_ticks;    /* clock anchor captured at startup ... */
    uint64_t t0_ns;       /* ... and CLOCK_MONOTONIC ns at the same instant */
    uint64_t hook_ns;     /* measured cost of one hook, for overhead
                           * compensation; 0 = not measured */
    uint32_t pid;
    uint32_t seq;         /* segment number within this thread's capture */
    uint64_t _reserved;
} trace_file_header_t;    /* 80 bytes */
TRACE_ASSERT_SIZE(trace_file_header_t, 80);

#define TRACE_HF_TICKS     0x1u  /* ts fields are raw ticks, not nanoseconds */
#define TRACE_HF_WRAPPED   0x2u  /* capture rotated: earlier segments discarded */
#define TRACE_HF_BIGENDIAN 0x4u  /* written by a big-endian agent */

/* On-disk event record (32 bytes, fixed layout, agent byte order) */
typedef struct {
    uint64_t ts_ns;       /* timestamp: ns, or ticks when TRACE_HF_TICKS */
    uint64_t func_addr;   /* address of the entered/exited function */
    uint64_t caller_addr; /* return address in the caller (the call site) */
    uint32_t tid;         /* kernel thread id */
    uint8_t  kind;        /* TRACE_EVENT_* */
    uint8_t  _pad[3];
} trace_event_t;
TRACE_ASSERT_SIZE(trace_event_t, 32);

#define TRACE_EVENT_ENTER  0u
#define TRACE_EVENT_EXIT   1u
/*
 * Hardware counter values for the call whose EXIT record this immediately
 * follows. The three 64-bit slots carry one delta per configured event, in
 * the order the TRACE_MARK_CEVENT markers declared them; unused slots are
 * zero. Deltas already have the instrumentation's own footprint subtracted.
 *
 * Written as its own record rather than by widening trace_event_t: the
 * 32-byte grid is what every reader, the shm ring and the wire protocol are
 * built on, and most calls are not counted, so widening every event to
 * carry three usually-empty fields would cost every user for a feature few
 * enable.
 */
#define TRACE_EVENT_COUNTER 3u
/*
 * Marker: an in-band note from the runtime to the analyzer, so a capture
 * that was cut short says so instead of just looking short. func_addr holds
 * a TRACE_MARK_* code and caller_addr its payload. Readers that do not
 * understand a marker must skip it — never treat it as an exit.
 */
#define TRACE_EVENT_MARKER 2u

#define TRACE_MARK_BUDGET    1u /* stopped: TRACE_MAX_MB reached */
#define TRACE_MARK_NOSPACE   2u /* stopped: free space below TRACE_MIN_FREE_MB */
#define TRACE_MARK_WRITE_ERR 3u /* stopped: write failed; payload = errno */
#define TRACE_MARK_MAXEVENTS 4u /* stopped: TRACE_MAX reached */
#define TRACE_MARK_WRAP      5u /* rotated away a segment; payload = events lost */
#define TRACE_MARK_CLOCK     6u /* exit clock anchor: ts = ticks, payload = ns */
/*
 * Counter setup, written at the head of every segment so an event file
 * describes its own counter columns without a format-version bump — and so
 * a rotated capture whose first segment was discarded still describes
 * itself. As with every marker, func_addr is the code and caller_addr the
 * payload; `ts` carries whatever else each one needs.
 *
 *   CEVENT: ts = (slot << 32) | perf type,  payload = perf config
 *   CCOST:  ts = slot,  payload = the hooks' own count for that event,
 *           already subtracted from every value reported
 *   CREAD:  ts = 0,     payload = measured cost of one read, in nanoseconds
 *   CSKIP:  ts = the function's address,  payload = its mean duration in
 *           the capture's time unit — selected, but too short to measure
 *   CMUX:   ts = 0,     payload = reads taken while the PMU was time-slicing
 */
#define TRACE_MARK_CEVENT    7u
#define TRACE_MARK_CCOST     8u
#define TRACE_MARK_CREAD     9u
#define TRACE_MARK_CSKIP    10u
#define TRACE_MARK_CMUX     11u

/* --- Summary mode (TRACE_MODE=summary) --- */

#define TRACE_SUM_MAGIC   "MLSUMRY\0"
/* Version 2 adds the hardware-counter fields. Readers gate on header_size
 * and record_size, so a version 1 summary still analyzes. */
#define TRACE_SUM_VERSION 2u

/* Counter events per capture; matches TRACE_PMU_MAX_EVENTS and
 * COUNTER_MAX_EVENTS in flags.py. */
#define TRACE_COUNTER_MAX 3u

/*
 * Duration histogram: four sub-buckets per octave (worst case ~19% width,
 * so percentile estimates are within a few percent), 160 buckets covering
 * 1 to 2^40 ticks. Exact min/max are tracked alongside, so the two numbers
 * people quote most are not estimates at all.
 */
#define TRACE_HIST_BUCKETS 160u

typedef struct {
    char     magic[8];    /* TRACE_SUM_MAGIC */
    uint32_t version;     /* TRACE_SUM_VERSION */
    uint32_t record_size; /* sizeof(trace_sum_record_t) */
    uint32_t header_size; /* bytes from file start to the first record */
    uint32_t flags;       /* TRACE_HF_* (same meanings) */
    uint64_t load_bias;
    uint64_t tick_hz;
    uint64_t t0_ticks;
    uint64_t t0_ns;
    uint64_t hook_ns;
    uint32_t pid;
    uint32_t tid;
    uint64_t records;     /* number of records that follow */
    uint64_t span;        /* last minus first timestamp seen on this thread */
    uint64_t truncated;   /* calls dropped past the shadow-stack depth limit */
    /* --- version 2: hardware counters (all zero when none were used) --- */
    uint32_t counter_n;               /* events configured, 0 = none */
    uint32_t _pad0;
    uint32_t counter_type[TRACE_COUNTER_MAX];    /* perf_event_attr.type */
    uint32_t _pad1;
    uint64_t counter_config[TRACE_COUNTER_MAX];  /* perf_event_attr.config */
    uint64_t counter_self[TRACE_COUNTER_MAX];    /* the hooks' own count,
                                                  * already subtracted */
    uint64_t counter_read_ns;         /* measured cost of one read */
    uint64_t counter_skipped;         /* calls not counted: too short */
    uint64_t counter_mux;             /* reads taken while multiplexing */
} trace_sum_header_t;
TRACE_ASSERT_SIZE(trace_sum_header_t, 192);

typedef struct {
    uint64_t func_addr;
    uint64_t calls;
    uint64_t incl;        /* inclusive time (ticks or ns, per flags) */
    uint64_t self;        /* inclusive minus time spent in callees */
    uint64_t min;
    uint64_t max;
    uint32_t hist[TRACE_HIST_BUCKETS];
    /* --- version 2 --- */
    uint64_t counter_calls;                   /* calls that got a sample;
                                               * differs from `calls` when a
                                               * function was demoted */
    uint64_t counter_incl[TRACE_COUNTER_MAX]; /* inclusive, per event */
    uint64_t counter_self[TRACE_COUNTER_MAX]; /* minus counted callees */
} trace_sum_record_t;
TRACE_ASSERT_SIZE(trace_sum_record_t, 744);

/* Compiler-inserted hooks (called for every instrumented function) */
void __cyg_profile_func_enter(void *this_fn, void *call_site)
    __attribute__((no_instrument_function));
void __cyg_profile_func_exit(void *this_fn, void *call_site)
    __attribute__((no_instrument_function));

/* Flush the calling thread's pending events (mainly for tests) */
void trace_flush(void) __attribute__((no_instrument_function));

#endif /* INSTRUMENT_TRACE_H */
