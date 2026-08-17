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
 *  - Process-wide counters are charged per flush (once per 8192 events),
 *    never per event: a shared atomic on the hot path would serialize every
 *    thread on one cache line exactly when tracing is heaviest.
 *  - Output goes through raw file descriptors, not stdio. We already batch,
 *    so stdio would only add a second buffer — one whose contents fork()
 *    duplicates into the child.
 *  - No project dependencies: this file is meant to be dropped into any
 *    C/C++ codebase as-is.
 *
 * Capture is bounded by default (TRACE_MAX_MB, see trace.h). A profiler
 * that can fill the device it is profiling is not a profiling tool, so the
 * budget, the free-space floor and every write error are enforced and
 * reported in-band as marker events rather than left to the operator.
 *
 * Summary mode (TRACE_MODE=summary) aggregates per function inside the
 * process — counts, inclusive/self time and a duration histogram over a TLS
 * shadow stack — and writes only totals at exit. Memory is proportional to
 * the number of instrumented functions, not to the number of calls, so a
 * run can last for hours and cost nothing on disk.
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

/* pthread_getname_np (TRACE_THREADS filter) and dl_iterate_phdr (PIE load
 * bias) are GNU extensions; define this before any libc header so the file
 * stays drop-in for projects that don't set it. */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

/*
 * Large-file support, for 32-bit agents. Without it off_t is 32 bits and
 * ftruncate() (used to trim a partial record after a short write) fails past
 * 2 GB, while statvfs() can return EOVERFLOW on a large filesystem and take
 * the free-space floor with it. This affects only our own calls — no off_t
 * crosses into the host program — so it cannot create an ABI mismatch with
 * the code this file is linked into.
 *
 * _TIME_BITS=64 is deliberately NOT set for exactly that reason: it would
 * change struct timespec's layout in this translation unit only, and this
 * file is linked into someone else's program. Nothing here stores a time_t
 * on disk, so 32-bit time is a 2038 problem for the host libc to solve, not
 * a correctness problem for the trace format.
 */
#ifndef _FILE_OFFSET_BITS
#define _FILE_OFFSET_BITS 64
#endif

#include "trace.h"
#include "trace_pmu.h"
#include "trace_shm.h"

#include <errno.h>
#include <fcntl.h>
#include <fnmatch.h>
#include <link.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#if defined(__x86_64__) || defined(__i386__)
#include <cpuid.h>
#endif

#define NOINSTR __attribute__((no_instrument_function))

/* Events buffered per thread before a write() flush */
#define TRACE_BUF_CAPACITY 8192u

/* Shadow-stack depth in summary mode; deeper calls are counted as truncated
 * rather than growing without bound. */
#define TRACE_SUM_DEPTH 256u

/* Initial slots in a thread's summary table (power of two). */
#define TRACE_SUM_INIT 64u

/* Bytes written between free-space checks. statvfs is a syscall, so this
 * runs about once per 130k events rather than per flush. */
#define TRACE_SPACE_EVERY (4u * 1024u * 1024u)

/* Floor on the adaptive segment size under TRACE_FULL=wrap; below this,
 * rotation would cost more than the data it bounds. */
#define TRACE_SEG_MIN (64u * 1024u)

#define TRACE_PATH_MAX 640

#define TRACE_MODE_EVENTS  0
#define TRACE_MODE_SUMMARY 1

#define TRACE_POLICY_STOP 0
#define TRACE_POLICY_WRAP 1

/* --- Global configuration (initialized once) --- */

static pthread_once_t  g_trace_once = PTHREAD_ONCE_INIT;
static pthread_key_t   g_trace_key;          /* TLS cleanup on thread exit */
static int             g_trace_enabled = 0;
static char            g_trace_dir[512] = "traces";
static int             g_mode = TRACE_MODE_EVENTS;
static int             g_policy = TRACE_POLICY_STOP;
static uint64_t        g_max_bytes = 0;      /* 0 = unlimited */
static uint64_t        g_seg_bytes = 0;      /* 0 = never rotate */
static uint64_t        g_min_free = 0;       /* 0 = no free-space floor */
static uint64_t        g_max_events = 0;     /* 0 = unlimited */
static atomic_uint_fast64_t g_bytes = 0;     /* bytes charged to the budget */
static atomic_uint_fast64_t g_events_used = 0; /* events reserved so far */
static atomic_int      g_stopped = 0;        /* capture finished early */
static atomic_int      g_wrapped = 0;        /* any segment was discarded */
static atomic_uint     g_threads = 0;        /* threads that opened output */
static trace_shm_header_t *g_shm = NULL;     /* non-NULL: streaming mode */
static char            g_thread_patterns[512] = ""; /* TRACE_THREADS globs */

/* Clock and symbolization anchors, all fixed before the first event. */
static int             g_use_ticks = 0;      /* raw cycle counter in use */
static clockid_t       g_clock_id = CLOCK_MONOTONIC;
static uint64_t        g_tick_hz = 0;
static uint64_t        g_t0_ticks = 0;
static uint64_t        g_t0_ns = 0;
static uint64_t        g_load_bias = 0;
static uint64_t        g_hook_ns = 0;

/* --- Hardware counters (TRACE_COUNTERS) --- */

/*
 * Which functions get counters is decided by the host and delivered as a
 * list of addresses, because the hooks only ever see an address. Everything
 * here is written once at init and read-only afterwards, except the
 * per-function demotion state below.
 */
static unsigned g_pmu_n = 0;                 /* events configured */
static uint32_t g_pmu_type[TRACE_COUNTER_MAX];
static uint64_t g_pmu_config[TRACE_COUNTER_MAX];
static uint64_t g_pmu_self[TRACE_COUNTER_MAX];  /* the hooks' own count */
static uint64_t g_pmu_read_ns = 0;
static uint64_t g_pmu_floor_ns = 0;  /* the guard rail, in nanoseconds */
static int      g_pmu_floor_auto = 1;/* derive it from the read cost */
static uint64_t g_pmu_floor = 0;     /* the same, in the capture's time
                                      * unit, so the hot path just compares */
static atomic_uint_fast64_t g_pmu_skipped = 0;
static atomic_uint_fast64_t g_pmu_mux = 0;

/*
 * The selected set: open-addressed, power-of-two, never resized.
 *
 * `calls`/`dur` exist only for the guard rail and stop being touched once a
 * function's fate is decided, so the steady state costs one load and one
 * compare. The writes race benignly between threads: every thread is
 * computing the same verdict from the same kind of evidence, and the verdict
 * is idempotent.
 */
typedef struct {
    uint64_t              addr;    /* runtime address, load bias applied */
    atomic_uint_fast64_t  calls;   /* samples used to judge it */
    atomic_uint_fast64_t  dur;     /* summed duration of those samples */
    atomic_int            demoted; /* 1 = too short to measure; stop reading */
} trace_sel_t;

static trace_sel_t *g_sel = NULL;
static uint32_t     g_sel_mask = 0;     /* slots - 1 */
static uint32_t     g_sel_count = 0;

/* Samples to gather before judging a function too short to be worth
 * counting. Small enough to stop wasting reads quickly, large enough not to
 * demote something on a cold first call. */
#define TRACE_PMU_JUDGE_AFTER 64u
/* A call must be this many times the cost of the reads it pays for, or the
 * measurement says more about the instrument than about the code. */
#define TRACE_PMU_FLOOR_RATIO 20u
/* Counted calls nested this deep are not counted. Counter selections are
 * small and shallow by design; going deeper is reported, not guessed at. */
#define TRACE_PMU_DEPTH 32u

/* --- Per-thread state --- */

typedef struct trace_seg {
    struct trace_seg *next;
    uint64_t          bytes;   /* file size, header included */
    uint32_t          seq;
} trace_seg_t;

typedef struct {
    uint64_t fn;
    uint64_t t0;
    uint64_t child;   /* time already attributed to callees */
} trace_frame_t;

/*
 * Shadow stack for counted calls only, used by both modes.
 *
 * Separate from trace_frame_t because event mode has no shadow stack at all
 * and should not grow one for every call just because a handful are counted:
 * this pushes only when a selected function is entered.
 */
typedef struct {
    uint64_t fn;
    uint64_t t0;                        /* for the guard rail's duration */
    uint64_t in[TRACE_COUNTER_MAX];     /* counter values at entry */
    uint64_t child[TRACE_COUNTER_MAX];  /* counted by nested counted calls */
    uint32_t reads;                     /* reads made inside this call */
} trace_cframe_t;

typedef struct {
    uint64_t addr;
    uint64_t calls;
    uint64_t incl;
    uint64_t self;
    uint64_t min;
    uint64_t max;
    uint32_t hist[TRACE_HIST_BUCKETS];
    uint64_t counter_calls;
    uint64_t counter_incl[TRACE_COUNTER_MAX];
    uint64_t counter_self[TRACE_COUNTER_MAX];
} trace_sum_entry_t;

typedef struct {
    trace_event_t *buf;        /* event mode only */
    uint32_t       len;
    int            out_fd;
    uint32_t       tid;
    uint32_t       pid;
    int            in_hook;    /* reentrancy guard */
    int            skip;       /* thread filtered out by TRACE_THREADS */
    uint32_t       skip_checks; /* recheck cadence while skipped */
    int            active;     /* output is open and usable */
    int            discard;    /* calibration: throw flushes away */

    uint32_t       seq;        /* next segment number */
    uint64_t       seg_bytes;  /* bytes in the current segment */
    uint64_t       since_space; /* bytes since the last free-space check */
    trace_seg_t   *segs;       /* live segments, oldest first */
    trace_seg_t   *segs_tail;  /* the one currently open */
    uint64_t       reserved;   /* events left in this thread's TRACE_MAX slice */

    trace_frame_t *stack;      /* summary mode: shadow stack */
    uint32_t       depth;
    uint64_t       truncated;  /* calls past TRACE_SUM_DEPTH */
    trace_sum_entry_t *tab;    /* summary mode: open-addressed table */
    uint32_t       tab_mask;
    uint32_t       tab_count;
    uint64_t       first_ts;
    uint64_t       last_ts;

    trace_pmu_t     pmu;                          /* this thread's counters */
    int             pmu_ready;                    /* group opened and usable */
    trace_cframe_t  cstack[TRACE_PMU_DEPTH];      /* counted calls in flight */
    uint32_t        cdepth;
} trace_tls_t;

static __thread trace_tls_t *tl_trace = NULL;

/* Counter hooks live down with the rest of the hot path, but the output and
 * lifecycle code above needs them. */
NOINSTR static void trace_counter_markers(trace_tls_t *tls);
NOINSTR static void trace_counter_enter(trace_tls_t *tls, uint64_t fn,
                                        uint64_t now);
NOINSTR static int trace_counter_exit(trace_tls_t *tls, uint64_t fn,
                                      uint64_t now, uint64_t *incl,
                                      uint64_t *child);

/* --- Time --- */

/*
 * Raw cycle counters. These are what the vDSO itself reads, minus the
 * scaling work: on x86-64 the invariant TSC, on aarch64 the virtual count
 * register. Both are usable from userspace with no syscall.
 *
 * rdtsc is not serializing, so a timestamp can drift a few cycles against
 * the surrounding code. At the scale this measures (tens of nanoseconds per
 * hook) that is noise; TRACE_CLOCK=mono is there for anyone who wants the
 * stricter ordering instead.
 */
#if defined(__x86_64__) || defined(__i386__)
#define TRACE_HAVE_TICKS 1
NOINSTR static inline uint64_t trace_ticks(void) {
    uint32_t lo, hi;
    __asm__ __volatile__("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | (uint64_t)lo;
}
/* Only an invariant TSC ticks at a fixed rate regardless of frequency
 * scaling and deep sleep states; anything else would silently distort every
 * duration we report. */
NOINSTR static int trace_ticks_usable(void) {
    uint32_t a, b, c, d;
    if (__get_cpuid_max(0x80000000u, NULL) < 0x80000007u)
        return 0;
    if (!__get_cpuid(0x80000007u, &a, &b, &c, &d))
        return 0;
    return (d & (1u << 8)) != 0;
}
NOINSTR static uint64_t trace_ticks_hz_hint(void) { return 0; }
#elif defined(__aarch64__)
#define TRACE_HAVE_TICKS 1
NOINSTR static inline uint64_t trace_ticks(void) {
    uint64_t v;
    __asm__ __volatile__("mrs %0, cntvct_el0" : "=r"(v));
    return v;
}
NOINSTR static int trace_ticks_usable(void) { return 1; }
/* The generic timer publishes its own frequency, so no calibration needed. */
NOINSTR static uint64_t trace_ticks_hz_hint(void) {
    uint64_t v;
    __asm__ __volatile__("mrs %0, cntfrq_el0" : "=r"(v));
    return v;
}
#else
/*
 * Everything else, including 32-bit ARM, uses clock_gettime.
 *
 * 32-bit ARM does have the same generic timer as aarch64, reachable through
 * CP15 (mrrc p15, 1, .., c14 for CNTVCT; mrc p15, 0, .., c14, c0, 0 for
 * CNTFRQ), and it was tried here. It is deliberately not shipped, for a
 * reason worth writing down so nobody re-adds it:
 *
 * The generic timer is optional on ARMv7 and those instructions raise
 * SIGILL where it is absent — and a runtime check cannot avoid that,
 * because the check IS the instruction. Reading CNTFRQ to find out whether
 * CNTFRQ is readable kills the host process at startup, which is the worst
 * failure this tool could have; qemu-arm demonstrated it immediately.
 * AT_HWCAP's event-stream bit would be a sound proxy, but that path could
 * not be exercised on any hardware or emulator available here, and an
 * untested fast path guarding against a SIGILL is not a trade worth making
 * for a clock that is already in the vDSO on ARMv7.
 */
#define TRACE_HAVE_TICKS 0
NOINSTR static inline uint64_t trace_ticks(void) { return 0; }
NOINSTR static int trace_ticks_usable(void) { return 0; }
NOINSTR static uint64_t trace_ticks_hz_hint(void) { return 0; }
#endif

NOINSTR static uint64_t trace_clock_ns(void) {
    struct timespec ts;
    clock_gettime(g_clock_id, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

NOINSTR static uint64_t trace_mono_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

NOINSTR static inline uint64_t trace_now(void) {
    if (g_use_ticks)
        return trace_ticks();
    return trace_clock_ns();
}

/* Ticks per second, measured against CLOCK_MONOTONIC. Only a coarse
 * fallback: the exit anchor lets the analyzer derive the rate over the whole
 * run instead, which is far more accurate than any startup window. */
NOINSTR static uint64_t trace_calibrate_hz(void) {
    struct timespec req;
    uint64_t c0, c1, n0, n1;

    req.tv_sec = 0;
    req.tv_nsec = 2000000; /* 2 ms */
    n0 = trace_mono_ns();
    c0 = trace_ticks();
    nanosleep(&req, NULL);
    c1 = trace_ticks();
    n1 = trace_mono_ns();
    if (n1 <= n0 || c1 <= c0)
        return 0;
    return (uint64_t)((double)(c1 - c0) * 1e9 / (double)(n1 - n0));
}

/* --- Free space --- */

/* Remaining space on the filesystem holding the trace directory, or
 * UINT64_MAX when it cannot be determined (never block on a failed check). */
NOINSTR static uint64_t trace_free_bytes(void) {
    struct statvfs vfs;
    if (statvfs(g_trace_dir, &vfs) != 0)
        return UINT64_MAX;
    return (uint64_t)vfs.f_bavail * (uint64_t)vfs.f_frsize;
}

/* --- Output --- */

/* Write everything or report how far it got. A short write is normal on a
 * signal and terminal when the filesystem is full; callers need the count
 * either way, because stopping mid-record would leave the file malformed. */
NOINSTR static int trace_write_all(int fd, const void *buf, size_t len,
                                   size_t *done) {
    const uint8_t *p = (const uint8_t *)buf;
    size_t written = 0;
    int rc = 0;

    while (len > 0) {
        ssize_t n = write(fd, p, len);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            rc = -1;
            break;
        }
        if (n == 0) {
            rc = -1;
            break;
        }
        p += (size_t)n;
        written += (size_t)n;
        len -= (size_t)n;
    }
    if (done)
        *done = written;
    return rc;
}

/*
 * Which way round this agent writes its integers.
 *
 * Recorded in every header so the analysis host does not have to infer it.
 * The host can infer it anyway — a byte-swapped `version` is unmistakable —
 * but a flag makes a hexdump and an error message legible, and costs one
 * predictable branch per file, not per event.
 */
NOINSTR static uint32_t trace_endian_flag(void) {
#if defined(__BYTE_ORDER__) && defined(__ORDER_BIG_ENDIAN__)
    return __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__ ? TRACE_HF_BIGENDIAN : 0u;
#else
    /* No compiler macro: ask the machine rather than assume the common case. */
    const uint32_t one = 1u;
    return ((const unsigned char *)&one)[0] == 0u ? TRACE_HF_BIGENDIAN : 0u;
#endif
}

NOINSTR static void trace_fill_header(trace_file_header_t *hdr, uint32_t pid,
                                      uint32_t seq) {
    memset(hdr, 0, sizeof(*hdr));
    memcpy(hdr->magic, TRACE_FILE_MAGIC, sizeof(hdr->magic));
    hdr->version = TRACE_FILE_VERSION;
    hdr->event_size = (uint32_t)sizeof(trace_event_t);
    hdr->header_size = (uint32_t)sizeof(trace_file_header_t);
    hdr->flags = (g_use_ticks ? TRACE_HF_TICKS : 0u)
               | (atomic_load(&g_wrapped) ? TRACE_HF_WRAPPED : 0u)
               | trace_endian_flag();
    hdr->load_bias = g_load_bias;
    hdr->tick_hz = g_use_ticks ? g_tick_hz : 0;
    hdr->t0_ticks = g_t0_ticks;
    hdr->t0_ns = g_t0_ns;
    hdr->hook_ns = g_hook_ns;
    hdr->pid = pid;
    hdr->seq = seq;
}

/*
 * Open the next segment for this thread.
 *
 * O_EXCL, never append: the kernel recycles thread ids, so a long-running
 * pool eventually hands a new thread a retired tid. Appending there would
 * drop a second file header into the middle of an existing capture and shift
 * every following record off the 32-byte grid — corruption the reader cannot
 * even detect, since it only validates the magic at offset zero. Bumping the
 * sequence number until the name is free costs nothing and makes that
 * unrepresentable.
 */
NOINSTR static int trace_open_segment(trace_tls_t *tls) {
    char path[TRACE_PATH_MAX];
    trace_file_header_t hdr;
    trace_seg_t *seg;
    int fd = -1;
    uint32_t tries;

    for (tries = 0; tries < 100000u; tries++) {
        snprintf(path, sizeof(path), "%s/trace.%u.%u.%u.bin",
                 g_trace_dir, tls->pid, tls->tid, tls->seq);
        fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);
        if (fd >= 0)
            break;
        if (errno != EEXIST)
            return 0;
        tls->seq++;
    }
    if (fd < 0)
        return 0;

    trace_fill_header(&hdr, tls->pid, tls->seq);
    if (trace_write_all(fd, &hdr, sizeof(hdr), NULL) != 0) {
        close(fd);
        return 0;
    }

    seg = (trace_seg_t *)calloc(1, sizeof(*seg));
    if (!seg) {
        close(fd);
        return 0;
    }
    seg->seq = tls->seq;
    seg->bytes = sizeof(hdr);

    if (tls->segs_tail)
        tls->segs_tail->next = seg;
    else
        tls->segs = seg;
    tls->segs_tail = seg;

    tls->out_fd = fd;
    tls->seg_bytes = sizeof(hdr);
    tls->seq++;
    atomic_fetch_add(&g_bytes, (uint_fast64_t)sizeof(hdr));
    trace_counter_markers(tls);
    return 1;
}

NOINSTR static void trace_free_segs(trace_tls_t *tls) {
    trace_seg_t *s = tls->segs;
    while (s) {
        trace_seg_t *next = s->next;
        free(s);
        s = next;
    }
    tls->segs = tls->segs_tail = NULL;
}

/* Emit a marker straight to the segment, bypassing the buffer: markers are
 * rare and must survive even when the buffer has just been flushed. */
NOINSTR static void trace_marker_at(trace_tls_t *tls, uint64_t ts,
                                    uint64_t code, uint64_t payload) {
    trace_event_t ev;

    if (tls->discard)
        return;
    memset(&ev, 0, sizeof(ev));
    ev.ts_ns = ts;
    ev.func_addr = code;
    ev.caller_addr = payload;
    ev.tid = tls->tid;
    ev.kind = (uint8_t)TRACE_EVENT_MARKER;

    if (g_shm) {
        trace_shm_header_t *h = g_shm;
        if (!trace_shm_lock(h))
            return;
        if (h->capacity - (h->head - h->tail) >= sizeof(ev))
            h->head = trace_shm_put(h, &ev, sizeof(ev));
        trace_shm_unlock(h);
        return;
    }
    if (tls->out_fd >= 0
        && trace_write_all(tls->out_fd, &ev, sizeof(ev), NULL) == 0) {
        tls->seg_bytes += sizeof(ev);
        if (tls->segs_tail)
            tls->segs_tail->bytes += sizeof(ev);
    }
}

NOINSTR static void trace_marker(trace_tls_t *tls, uint64_t code,
                                 uint64_t payload) {
    trace_marker_at(tls, trace_now(), code, payload);
}

/*
 * Describe this capture's counter columns, in band.
 *
 * Written at the head of every segment rather than in the file header: the
 * header is version 2 and readers of it are in the wild, whereas markers are
 * already defined as notes from the runtime that a reader may skip. It also
 * means a rotated capture whose first segment was discarded still describes
 * itself, and streaming gets it for free.
 */
NOINSTR static void trace_counter_markers(trace_tls_t *tls) {
    unsigned i;

    if (g_pmu_n == 0 || g_mode != TRACE_MODE_EVENTS)
        return;
    for (i = 0; i < g_pmu_n; i++) {
        trace_marker_at(tls, ((uint64_t)i << 32) | g_pmu_type[i],
                        TRACE_MARK_CEVENT, g_pmu_config[i]);
        trace_marker_at(tls, i, TRACE_MARK_CCOST, g_pmu_self[i]);
    }
    trace_marker_at(tls, 0, TRACE_MARK_CREAD, g_pmu_read_ns);
}

/* End the capture for the whole process, recording why in the caller's
 * segment so the report can say what happened instead of just looking
 * short. */
NOINSTR static void trace_stop(trace_tls_t *tls, uint64_t code,
                               uint64_t payload) {
    if (atomic_exchange(&g_stopped, 1) == 0)
        trace_marker(tls, code, payload);
}

/*
 * Wrap policy: discard this thread's oldest segment to make room.
 *
 * A thread only ever unlinks files it wrote itself, so no coordination
 * between threads is needed beyond the byte counter. The current segment is
 * never dropped, which means a thread holding a single segment cannot free
 * anything yet — the overshoot that allows is bounded by one segment per
 * thread and is documented as such.
 */
NOINSTR static int trace_drop_oldest(trace_tls_t *tls) {
    char path[TRACE_PATH_MAX];
    trace_seg_t *old = tls->segs;
    uint64_t lost;

    if (!old || old == tls->segs_tail)
        return 0;

    snprintf(path, sizeof(path), "%s/trace.%u.%u.%u.bin",
             g_trace_dir, tls->pid, tls->tid, old->seq);
    unlink(path);

    lost = old->bytes > sizeof(trace_file_header_t)
         ? (old->bytes - sizeof(trace_file_header_t)) / sizeof(trace_event_t)
         : 0;
    atomic_fetch_sub(&g_bytes, (uint_fast64_t)old->bytes);
    tls->segs = old->next;
    free(old);

    atomic_store(&g_wrapped, 1);
    trace_marker(tls, TRACE_MARK_WRAP, lost);
    return 1;
}

/*
 * Segment size to rotate at.
 *
 * Under wrap, a thread can never discard the segment it is writing, so the
 * steady state holds up to two segments per thread — and with a couple of
 * dozen threads a fixed 32 MB segment would blow past a small budget by an
 * order of magnitude. Sizing the segment against the budget and the number
 * of participating threads keeps the total near what was asked for. There is
 * still a floor: a tiny budget spread over many threads cannot be honoured
 * exactly, and rotating every few KB would cost more than it saves.
 */
NOINSTR static uint64_t trace_seg_limit(void) {
    uint64_t seg = g_seg_bytes;
    uint64_t share;
    unsigned n;

    if (g_max_bytes == 0 || g_policy != TRACE_POLICY_WRAP)
        return seg;
    n = atomic_load_explicit(&g_threads, memory_order_relaxed);
    if (n < 1)
        n = 1;
    share = g_max_bytes / ((uint64_t)n * 2u);
    if (share < TRACE_SEG_MIN)
        share = TRACE_SEG_MIN;
    if (seg == 0 || share < seg)
        seg = share;
    return seg;
}

/* Charge a batch against the process-wide disk budget. Returns 0 when the
 * capture must stop. */
NOINSTR static int trace_charge(trace_tls_t *tls, uint64_t bytes) {
    uint64_t used;

    if (g_max_bytes == 0)
        return 1;
    used = (uint64_t)atomic_fetch_add(&g_bytes, (uint_fast64_t)bytes) + bytes;
    if (used <= g_max_bytes)
        return 1;
    if (g_policy == TRACE_POLICY_WRAP) {
        trace_drop_oldest(tls);
        return 1;
    }
    trace_stop(tls, TRACE_MARK_BUDGET, g_max_bytes);
    return 0;
}

/* Streaming flush: copy the thread's batch into the shm ring. Events that
 * don't fit are dropped and counted — see the file header comment. */
NOINSTR static void trace_shm_flush(trace_tls_t *tls) {
    trace_shm_header_t *h = g_shm;
    uint64_t bytes = (uint64_t)tls->len * sizeof(trace_event_t);
    if (!trace_shm_lock(h)) {
        /* The holder is gone or wedged; dropping beats stalling. */
        h->dropped += tls->len;
        tls->len = 0;
        return;
    }
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

/* File flush: rotate if the segment is full, honour the budget and the free
 * space floor, then write — checking that the write actually happened. */
NOINSTR static void trace_file_flush(trace_tls_t *tls) {
    uint64_t bytes = (uint64_t)tls->len * sizeof(trace_event_t);

    if (tls->out_fd < 0) {
        tls->len = 0;
        return;
    }

    uint64_t seg_limit = trace_seg_limit();
    if (seg_limit && tls->seg_bytes + bytes > seg_limit) {
        close(tls->out_fd);
        tls->out_fd = -1;
        if (!trace_open_segment(tls)) {
            trace_stop(tls, TRACE_MARK_WRITE_ERR, (uint64_t)errno);
            tls->len = 0;
            return;
        }
    }

    if (g_min_free) {
        tls->since_space += bytes;
        if (tls->since_space >= TRACE_SPACE_EVERY) {
            tls->since_space = 0;
            if (trace_free_bytes() < g_min_free) {
                trace_stop(tls, TRACE_MARK_NOSPACE, g_min_free);
                tls->len = 0;
                return;
            }
        }
    }

    if (!trace_charge(tls, bytes)) {
        tls->len = 0;
        return;
    }

    size_t done = 0;
    if (trace_write_all(tls->out_fd, tls->buf, (size_t)bytes, &done) != 0) {
        int err = errno;
        /* Drop the partial record the failed write left behind, so the file
         * stays a header plus whole events rather than something every
         * reader has to special-case. */
        size_t keep = done - (done % sizeof(trace_event_t));
        if (ftruncate(tls->out_fd, (off_t)(tls->seg_bytes + keep)) == 0) {
            tls->seg_bytes += keep;
            if (tls->segs_tail)
                tls->segs_tail->bytes += keep;
        }
        /* A failed write is silent data loss unless we say so: the report
         * would look clean and simply be missing everything after here.
         * The marker may not fit either — that is exactly when the message
         * on stderr is the only thing left to say it. */
        fprintf(stderr, "callsight: write to trace segment failed (%s); "
                        "capture stopped\n", strerror(err));
        trace_stop(tls, TRACE_MARK_WRITE_ERR, (uint64_t)err);
        tls->len = 0;
        return;
    }

    tls->seg_bytes += bytes;
    if (tls->segs_tail)
        tls->segs_tail->bytes += bytes;
    tls->len = 0;
}

NOINSTR static void trace_tls_flush(trace_tls_t *tls) {
    if (tls->len == 0)
        return;
    if (tls->discard) {
        tls->len = 0;
        return;
    }
    if (g_shm)
        trace_shm_flush(tls);
    else
        trace_file_flush(tls);
}

NOINSTR void trace_flush(void) {
    if (tl_trace && g_mode == TRACE_MODE_EVENTS)
        trace_tls_flush(tl_trace);
}

/* --- Summary mode --- */

/*
 * Bucket a duration: four sub-buckets per octave above 8, exact below it.
 * Index 0..7 covers 0..7 directly; every octave after that contributes four
 * buckets, so bucket width stays under ~19% of the value it holds.
 */
NOINSTR static inline uint32_t trace_hist_bucket(uint64_t d) {
    uint32_t msb, sub, idx;

    if (d < 8)
        return (uint32_t)d;
    msb = 63u - (uint32_t)__builtin_clzll(d);
    sub = (uint32_t)((d >> (msb - 2u)) & 3u);
    idx = (msb - 3u) * 4u + sub + 8u;
    return idx < TRACE_HIST_BUCKETS ? idx : TRACE_HIST_BUCKETS - 1u;
}

NOINSTR static int trace_sum_grow(trace_tls_t *tls);

/* Find or create the entry for one function address (open addressing). */
NOINSTR static trace_sum_entry_t *trace_sum_slot(trace_tls_t *tls,
                                                 uint64_t addr) {
    uint64_t h;
    uint32_t i;

    if (!tls->tab)
        return NULL;
    /* splitmix64 finalizer: function addresses cluster hard on their low
     * bits, which a plain mask would turn into one long probe chain. */
    h = addr;
    h ^= h >> 30;
    h *= 0xbf58476d1ce4e5b9ull;
    h ^= h >> 27;
    h *= 0x94d049bb133111ebull;
    h ^= h >> 31;

    i = (uint32_t)h & tls->tab_mask;
    for (;;) {
        trace_sum_entry_t *e = &tls->tab[i];
        if (e->calls == 0 && e->addr == 0) {
            if ((tls->tab_count + 1u) * 10u > (tls->tab_mask + 1u) * 7u) {
                if (!trace_sum_grow(tls))
                    return NULL;
                return trace_sum_slot(tls, addr);
            }
            e->addr = addr;
            e->min = UINT64_MAX;
            tls->tab_count++;
            return e;
        }
        if (e->addr == addr)
            return e;
        i = (i + 1u) & tls->tab_mask;
    }
}

NOINSTR static int trace_sum_grow(trace_tls_t *tls) {
    uint32_t old_slots = tls->tab_mask + 1u;
    uint32_t new_slots = old_slots * 2u;
    trace_sum_entry_t *old = tls->tab;
    trace_sum_entry_t *fresh;
    uint32_t i;

    fresh = (trace_sum_entry_t *)calloc(new_slots, sizeof(*fresh));
    if (!fresh)
        return 0;
    tls->tab = fresh;
    tls->tab_mask = new_slots - 1u;
    tls->tab_count = 0;
    for (i = 0; i < old_slots; i++) {
        if (old[i].addr != 0 || old[i].calls != 0) {
            trace_sum_entry_t *e = trace_sum_slot(tls, old[i].addr);
            if (e) {
                uint64_t addr = e->addr;
                *e = old[i];
                e->addr = addr;
            }
        }
    }
    free(old);
    return 1;
}

NOINSTR static int trace_sum_init(trace_tls_t *tls) {
    tls->stack = (trace_frame_t *)calloc(TRACE_SUM_DEPTH, sizeof(trace_frame_t));
    tls->tab = (trace_sum_entry_t *)calloc(TRACE_SUM_INIT, sizeof(trace_sum_entry_t));
    if (!tls->stack || !tls->tab) {
        free(tls->stack);
        free(tls->tab);
        tls->stack = NULL;
        tls->tab = NULL;
        return 0;
    }
    tls->tab_mask = TRACE_SUM_INIT - 1u;
    return 1;
}

/* Aggregate one event in-process: push on enter, close and account on exit.
 * Nothing here grows with the call count. */
NOINSTR static void trace_sum_record(trace_tls_t *tls, void *this_fn,
                                     uint8_t kind) {
    uint64_t fn = (uint64_t)(uintptr_t)this_fn;
    uint64_t now = trace_now();
    trace_sum_entry_t *e;
    trace_frame_t *fr;
    uint64_t dur;
    uint32_t i;
    uint64_t cin[TRACE_COUNTER_MAX], cch[TRACE_COUNTER_MAX];
    int counted = 0;

    if (tls->first_ts == 0)
        tls->first_ts = now;
    tls->last_ts = now;

    if (kind == (uint8_t)TRACE_EVENT_ENTER) {
        if (tls->depth >= TRACE_SUM_DEPTH) {
            tls->truncated++;
            tls->depth++;
            if (g_pmu_n)
                trace_counter_enter(tls, fn, now);
            return;
        }
        fr = &tls->stack[tls->depth++];
        fr->fn = fn;
        fr->t0 = now;
        fr->child = 0;
        /* Last thing before returning into the function, so none of the
         * bookkeeping above lands inside the measured window. */
        if (g_pmu_n)
            trace_counter_enter(tls, fn, now);
        return;
    }

    /* Read the counter before any of the exit bookkeeping, for the same
     * reason the enter path reads it last: whatever runs between the two
     * reads is counted as if the function had done it. */
    if (g_pmu_n)
        counted = trace_counter_exit(tls, fn, now, cin, cch);

    if (tls->depth > TRACE_SUM_DEPTH) {
        tls->depth--;   /* unwinding the part we never recorded */
        return;
    }
    if (tls->depth == 0)
        return;         /* exit without a matching enter */

    /* Nearest unmatched enter for this function; frames above it were left
     * dangling by a longjmp or an exception and are dropped, matching what
     * the offline analyzer does. */
    i = tls->depth;
    while (i > 0 && tls->stack[i - 1].fn != fn)
        i--;
    if (i == 0)
        return;
    tls->depth = i - 1;
    fr = &tls->stack[tls->depth];

    dur = now - fr->t0;
    e = trace_sum_slot(tls, fn);
    if (e) {
        e->calls++;
        e->incl += dur;
        e->self += dur - fr->child;
        if (dur < e->min)
            e->min = dur;
        if (dur > e->max)
            e->max = dur;
        e->hist[trace_hist_bucket(dur)]++;
        if (counted) {
            unsigned k;
            e->counter_calls++;
            for (k = 0; k < g_pmu_n; k++) {
                e->counter_incl[k] += cin[k];
                e->counter_self[k] += cin[k] - cch[k];
            }
        }
    }
    if (tls->depth > 0)
        tls->stack[tls->depth - 1].child += dur;
}

/* Write this thread's totals. One small file, whatever the run length. */
NOINSTR static void trace_sum_write(trace_tls_t *tls) {
    char path[TRACE_PATH_MAX];
    trace_sum_header_t hdr;
    uint32_t i, slots;
    int fd;

    if (!tls->tab || tls->tab_count == 0)
        return;

    snprintf(path, sizeof(path), "%s/trace.summary.%u.%u.bin",
             g_trace_dir, tls->pid, tls->tid);
    fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0)
        return;

    memset(&hdr, 0, sizeof(hdr));
    memcpy(hdr.magic, TRACE_SUM_MAGIC, sizeof(hdr.magic));
    hdr.version = TRACE_SUM_VERSION;
    hdr.record_size = (uint32_t)sizeof(trace_sum_record_t);
    hdr.header_size = (uint32_t)sizeof(trace_sum_header_t);
    hdr.flags = (g_use_ticks ? TRACE_HF_TICKS : 0u) | trace_endian_flag();
    hdr.load_bias = g_load_bias;
    hdr.tick_hz = g_use_ticks ? g_tick_hz : 0;
    hdr.t0_ticks = g_t0_ticks;
    hdr.t0_ns = g_t0_ns;
    hdr.hook_ns = g_hook_ns;
    hdr.pid = tls->pid;
    hdr.tid = tls->tid;
    hdr.records = tls->tab_count;
    hdr.span = tls->last_ts - tls->first_ts;
    hdr.truncated = tls->truncated;
    hdr.counter_n = g_pmu_n;
    for (i = 0; i < g_pmu_n; i++) {
        hdr.counter_type[i] = g_pmu_type[i];
        hdr.counter_config[i] = g_pmu_config[i];
        hdr.counter_self[i] = g_pmu_self[i];
    }
    hdr.counter_read_ns = g_pmu_read_ns;
    hdr.counter_skipped = (uint64_t)atomic_load(&g_pmu_skipped);
    hdr.counter_mux = (uint64_t)atomic_load(&g_pmu_mux)
                    + tls->pmu.multiplexed;
    if (trace_write_all(fd, &hdr, sizeof(hdr), NULL) != 0) {
        close(fd);
        return;
    }

    slots = tls->tab_mask + 1u;
    for (i = 0; i < slots; i++) {
        trace_sum_record_t rec;
        trace_sum_entry_t *e = &tls->tab[i];
        if (e->calls == 0)
            continue;
        rec.func_addr = e->addr;
        rec.calls = e->calls;
        rec.incl = e->incl;
        rec.self = e->self;
        rec.min = e->min == UINT64_MAX ? 0 : e->min;
        rec.max = e->max;
        memcpy(rec.hist, e->hist, sizeof(rec.hist));
        rec.counter_calls = e->counter_calls;
        memcpy(rec.counter_incl, e->counter_incl, sizeof(rec.counter_incl));
        memcpy(rec.counter_self, e->counter_self, sizeof(rec.counter_self));
        if (trace_write_all(fd, &rec, sizeof(rec), NULL) != 0)
            break;
    }
    close(fd);
}

/* --- Lifecycle --- */

NOINSTR static void trace_tls_close(trace_tls_t *tls) {
    if (tls->pmu_ready) {
        atomic_fetch_add(&g_pmu_mux, (uint_fast64_t)tls->pmu.multiplexed);
        trace_pmu_close(&tls->pmu);
        tls->pmu_ready = 0;
    }
    if (g_mode == TRACE_MODE_SUMMARY) {
        trace_sum_write(tls);
        return;
    }
    trace_tls_flush(tls);
    if (tls->out_fd >= 0) {
        close(tls->out_fd);
        tls->out_fd = -1;
    }
}

/* pthread key destructor: write out and release when a thread exits */
NOINSTR static void trace_tls_destroy(void *ptr) {
    trace_tls_t *tls = (trace_tls_t *)ptr;
    if (!tls)
        return;
    trace_tls_close(tls);
    trace_free_segs(tls);
    free(tls->buf);
    free(tls->stack);
    free(tls->tab);
    free(tls);
    tl_trace = NULL;
}

/* atexit handler: finish the calling thread, leave the clock anchor, detach
 * from the ring */
NOINSTR static void trace_atexit(void) {
    if (tl_trace) {
        /* Second half of the clock calibration pair. Deriving the tick rate
         * across the whole run beats any startup measurement, and costs one
         * event. */
        if (g_mode == TRACE_MODE_EVENTS && g_pmu_n) {
            /* What the guard rail refused, and whether the PMU was
             * time-slicing — both make a report say less than it appears
             * to, so both are said out loud. */
            uint32_t i;
            trace_tls_flush(tl_trace);
            for (i = 0; g_sel && i <= g_sel_mask; i++) {
                trace_sel_t *s = &g_sel[i];
                uint64_t n;
                if (s->addr == 0
                        || !atomic_load_explicit(&s->demoted,
                                                 memory_order_relaxed))
                    continue;
                n = (uint64_t)atomic_load(&s->calls);
                trace_marker_at(tl_trace, s->addr - g_load_bias,
                                TRACE_MARK_CSKIP,
                                n ? (uint64_t)atomic_load(&s->dur) / n : 0);
            }
            if (atomic_load(&g_pmu_mux) || tl_trace->pmu.multiplexed)
                trace_marker_at(tl_trace, 0, TRACE_MARK_CMUX,
                                (uint64_t)atomic_load(&g_pmu_mux)
                                + tl_trace->pmu.multiplexed);
        }
        if (g_use_ticks && g_mode == TRACE_MODE_EVENTS) {
            trace_tls_flush(tl_trace);
            trace_marker(tl_trace, TRACE_MARK_CLOCK, trace_mono_ns());
        }
        trace_tls_close(tl_trace);
    }
    if (g_shm)
        __sync_fetch_and_sub(&g_shm->writers, 1);
}

/*
 * fork() child handler.
 *
 * The child inherits our descriptors, our buffered events and a path built
 * from the parent's pid — writing any of it would interleave two processes
 * into one file. Drop all of it without flushing (those events belong to the
 * parent, which will write them itself) and let the next hook start a fresh
 * capture under the new pid.
 */
NOINSTR static void trace_atfork_child(void) {
    trace_tls_t *tls = tl_trace;

    if (tls) {
        if (tls->out_fd >= 0)
            close(tls->out_fd);
        tls->out_fd = -1;
        tls->len = 0;
        tls->active = 0;
        tls->seq = 0;
        tls->seg_bytes = 0;
        tls->since_space = 0;
        tls->reserved = 0;
        tls->pid = (uint32_t)getpid();
        tls->tid = (uint32_t)syscall(SYS_gettid);
        /* The segment list describes files the parent owns: forget it
         * rather than freeing or unlinking anything. Blocks belonging to
         * threads that did not survive the fork are unreachable and leak by
         * design — there is no safe way to walk them here. */
        tls->segs = tls->segs_tail = NULL;
        tls->depth = 0;
        tls->truncated = 0;
        tls->first_ts = tls->last_ts = 0;
        if (tls->tab) {
            memset(tls->tab, 0, (size_t)(tls->tab_mask + 1u) * sizeof(*tls->tab));
            tls->tab_count = 0;
        }
    }
    atomic_store(&g_bytes, 0);
    atomic_store(&g_events_used, 0);
    atomic_store(&g_stopped, 0);
    atomic_store(&g_wrapped, 0);
}

/* --- One-time global init --- */

/* The main executable's relocation offset: 0 for a -no-pie link, the load
 * base for a PIE. Recording it is what lets the analyzer map runtime
 * addresses back to link addresses without forcing -no-pie on the project. */
NOINSTR static int trace_phdr_cb(struct dl_phdr_info *info, size_t size,
                                 void *data) {
    (void)size;
    *(uint64_t *)data = (uint64_t)info->dlpi_addr;
    return 1;  /* first entry is the main object; stop there */
}

/* --- Hardware counters: selection --- */

/* splitmix64 finalizer: function addresses are dense and 16-byte aligned,
 * so the low bits alone would collide in every slot. */
NOINSTR static inline uint32_t trace_sel_hash(uint64_t a) {
    a ^= a >> 33;
    a *= 0xff51afd7ed558ccdull;
    a ^= a >> 33;
    return (uint32_t)a;
}

/*
 * The one lookup on the hot path.
 *
 * Returns the slot for a selected, not-yet-demoted function, or NULL. Every
 * instrumented function pays this when counters are on; the table is sized
 * to at least twice the selection so probes stay short, and the selection is
 * expected to be tens of functions, which is what makes that affordable.
 */
NOINSTR static inline trace_sel_t *trace_sel_find(uint64_t addr) {
    uint32_t i = trace_sel_hash(addr) & g_sel_mask;
    uint32_t probes = 0;
    while (probes++ <= g_sel_mask) {
        trace_sel_t *s = &g_sel[i];
        if (s->addr == addr)
            return atomic_load_explicit(&s->demoted, memory_order_relaxed)
                   ? NULL : s;
        if (s->addr == 0)
            return NULL;
        i = (i + 1) & g_sel_mask;
    }
    return NULL;
}

NOINSTR static void trace_sel_insert(uint64_t addr) {
    uint32_t i = trace_sel_hash(addr) & g_sel_mask;
    while (g_sel[i].addr != 0) {
        if (g_sel[i].addr == addr)
            return;
        i = (i + 1) & g_sel_mask;
    }
    g_sel[i].addr = addr;
    g_sel_count++;
}

/*
 * Load the counter map written by `callsight counters`.
 *
 * Text, because it has to be the same file for a 32-bit big-endian agent as
 * for an x86-64 one, and because a profiler's configuration should be
 * readable by the person configuring it. Addresses are link-time, so the
 * load bias goes on here — once, at init, rather than on every hook.
 */
NOINSTR static void trace_load_counters(const char *path) {
    enum { MAX_MAP = 1u << 20 };   /* ~30k functions; far past useful */
    char *buf, *p;
    long size;
    uint64_t floor_ns = 0;
    int floor_auto = 1;
    uint32_t addrs = 0, slots = 8;
    FILE *f = fopen(path, "rb");

    if (!f)
        return;                    /* no map is simply "no counters" */
    if (fseek(f, 0, SEEK_END) != 0 || (size = ftell(f)) < 0
            || size > (long)MAX_MAP || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return;
    }
    buf = (char *)malloc((size_t)size + 1);
    if (!buf) {
        fclose(f);
        return;
    }
    if (fread(buf, 1, (size_t)size, f) != (size_t)size) {
        free(buf);
        fclose(f);
        return;
    }
    buf[size] = '\0';
    fclose(f);

    /* First pass: events, floor, and how many addresses to make room for. */
    for (p = buf; *p; ) {
        char *line = p;
        char *nl = strchr(p, '\n');
        p = nl ? nl + 1 : p + strlen(p);
        if (nl)
            *nl = '\0';
        if (line[0] == '\0' || line[0] == '#')
            continue;
        if (strncmp(line, "event ", 6) == 0) {
            unsigned long type = 0;
            unsigned long long config = 0;
            char *sp = strchr(line + 6, ' ');
            if (sp && g_pmu_n < TRACE_COUNTER_MAX
                    && sscanf(sp, "%lu %llu", &type, &config) == 2) {
                g_pmu_type[g_pmu_n] = (uint32_t)type;
                g_pmu_config[g_pmu_n] = (uint64_t)config;
                g_pmu_n++;
            }
        } else if (strncmp(line, "min ", 4) == 0) {
            if (strcmp(line + 4, "auto") != 0) {
                floor_auto = 0;
                floor_ns = strtoull(line + 4, NULL, 10);
            }
        } else if ((line[0] >= '0' && line[0] <= '9')
                   || (line[0] >= 'a' && line[0] <= 'f')) {
            addrs++;
        }
    }
    if (addrs == 0 || g_pmu_n == 0) {
        free(buf);
        g_pmu_n = 0;
        return;
    }
    while (slots < addrs * 2u)
        slots <<= 1;
    g_sel = (trace_sel_t *)calloc(slots, sizeof(*g_sel));
    if (!g_sel) {
        free(buf);
        g_pmu_n = 0;
        return;
    }
    g_sel_mask = slots - 1u;

    /* Second pass: the addresses. The buffer's newlines are now NULs, so
     * walk it as a sequence of strings. */
    {
        char *end = buf + size;
        for (p = buf; p < end; p += strlen(p) + 1) {
            char *sp;
            uint64_t a;
            if (*p == '\0' || *p == '#')
                continue;
            if (strncmp(p, "event ", 6) == 0 || strncmp(p, "min ", 4) == 0
                    || strncmp(p, "exe ", 4) == 0
                    || strncmp(p, "build-id ", 9) == 0
                    || strncmp(p, "CALLSIGHT-COUNTERS", 18) == 0)
                continue;
            a = strtoull(p, &sp, 16);
            if (sp == p || a == 0)
                continue;
            trace_sel_insert(a + g_load_bias);
        }
    }
    free(buf);

    /* 'auto' cannot be resolved yet: it is a multiple of the read cost, and
     * nothing has measured that. trace_pmu_setup() finishes the job. */
    g_pmu_floor_auto = floor_auto;
    g_pmu_floor_ns = floor_auto ? 0 : floor_ns;
}

/*
 * Decide whether counters are usable at all, and what they cost.
 *
 * Called once, from init, after the clock is chosen — the floor is compared
 * against durations in the capture's own time unit, so it has to be
 * converted into that unit here.
 */
NOINSTR static void trace_pmu_setup(void) {
    trace_pmu_t probe;
    uint64_t self[TRACE_COUNTER_MAX];
    uint64_t read_ns = 0;
    unsigned i;

    if (g_pmu_n == 0)
        return;

    /* The check that matters: perf_event_open succeeding proves nothing.
     * Without this, a container reports zero for every function and looks
     * perfectly healthy doing it. */
    if (!trace_pmu_prove(g_pmu_type[0], g_pmu_config[0])) {
        fprintf(stderr, "callsight: hardware counters are not usable here "
                        "(the event opens but never reaches hardware — "
                        "typical in a container); continuing without them\n");
        g_pmu_n = 0;
        return;
    }
    if (trace_pmu_open(&probe, g_pmu_type, g_pmu_config, g_pmu_n)
            == TRACE_PMU_OFF) {
        fprintf(stderr, "callsight: could not open the counter group; "
                        "continuing without counters\n");
        g_pmu_n = 0;
        return;
    }
    trace_pmu_calibrate(&probe, &read_ns, self);
    trace_pmu_close(&probe);

    g_pmu_read_ns = read_ns;
    for (i = 0; i < g_pmu_n; i++)
        g_pmu_self[i] = self[i];

    if (g_pmu_floor_auto)
        g_pmu_floor_ns = read_ns * 2u * TRACE_PMU_FLOOR_RATIO;

    /* Into the capture's time unit, so the hot path compares two plain
     * integers rather than converting on every call. */
    if (g_use_ticks && g_tick_hz)
        g_pmu_floor = (uint64_t)((double)g_pmu_floor_ns
                                 * (double)g_tick_hz / 1e9);
    else
        g_pmu_floor = g_pmu_floor_ns;
}

NOINSTR static uint64_t trace_env_u64(const char *name, uint64_t fallback) {
    const char *v = getenv(name);
    char *end;
    unsigned long long parsed;

    if (!v || v[0] == '\0')
        return fallback;
    errno = 0;
    parsed = strtoull(v, &end, 10);
    if (errno != 0 || end == v)
        return fallback;
    return (uint64_t)parsed;
}

NOINSTR static void trace_record_inner(trace_tls_t *tls, void *this_fn,
                                       void *call_site, uint8_t kind);

/* Measure what one hook actually costs, so reported times can be corrected
 * for the instrumentation itself instead of guessed at. Runs the real
 * recording path against a throwaway buffer. */
NOINSTR static uint64_t trace_measure_hook(void) {
    enum { ITERATIONS = 20000 };
    trace_tls_t *probe;
    uint64_t n0, n1;
    int i;

    probe = (trace_tls_t *)calloc(1, sizeof(*probe));
    if (!probe)
        return 0;
    probe->buf = (trace_event_t *)calloc(TRACE_BUF_CAPACITY,
                                         sizeof(trace_event_t));
    if (!probe->buf) {
        free(probe);
        return 0;
    }
    probe->out_fd = -1;
    probe->discard = 1;

    n0 = trace_mono_ns();
    for (i = 0; i < ITERATIONS; i++) {
        trace_record_inner(probe, (void *)(uintptr_t)0x1000,
                           (void *)(uintptr_t)0x2000, TRACE_EVENT_ENTER);
        trace_record_inner(probe, (void *)(uintptr_t)0x1000,
                           (void *)(uintptr_t)0x2000, TRACE_EVENT_EXIT);
    }
    n1 = trace_mono_ns();

    free(probe->buf);
    free(probe);
    return n1 > n0 ? (n1 - n0) / (uint64_t)(2 * ITERATIONS) : 0;
}

NOINSTR static void trace_global_init(void) {
    const char *env = getenv("TRACE_ENABLE");
    g_trace_enabled = (env && env[0] == '1');

    env = getenv("TRACE_DIR");
    if (env && env[0] != '\0')
        snprintf(g_trace_dir, sizeof(g_trace_dir), "%s", env);

    /* Resolve the directory now: an application that chdir()s later would
     * otherwise scatter segments across the filesystem. */
    if (g_trace_dir[0] != '/') {
        char cwd[256];
        if (getcwd(cwd, sizeof(cwd))) {
            size_t n = strlen(cwd), m = strlen(g_trace_dir);
            if (n + 1 + m < sizeof(g_trace_dir)) {
                memmove(g_trace_dir + n + 1, g_trace_dir, m + 1);
                memcpy(g_trace_dir, cwd, n);
                g_trace_dir[n] = '/';
            }
        }
    }

    env = getenv("TRACE_MODE");
    if (env && strcmp(env, "summary") == 0)
        g_mode = TRACE_MODE_SUMMARY;

    env = getenv("TRACE_FULL");
    if (env && strcmp(env, "wrap") == 0)
        g_policy = TRACE_POLICY_WRAP;

    g_max_bytes = trace_env_u64("TRACE_MAX_MB", 512ull) * 1024ull * 1024ull;
    g_seg_bytes = trace_env_u64("TRACE_SEG_MB", 32ull) * 1024ull * 1024ull;
    g_min_free = trace_env_u64("TRACE_MIN_FREE_MB", 64ull) * 1024ull * 1024ull;
    g_max_events = trace_env_u64("TRACE_MAX", 0ull);

    /* A budget below one segment would rotate on every flush; keep the
     * segment inside the budget instead. */
    if (g_max_bytes && g_seg_bytes > g_max_bytes)
        g_seg_bytes = g_max_bytes;

    env = getenv("TRACE_THREADS");
    if (env)
        snprintf(g_thread_patterns, sizeof(g_thread_patterns), "%s", env);

    if (!g_trace_enabled)
        return;

    /* Clock selection. CLOCK_MONOTONIC_RAW is not in the vDSO on older ARM
     * kernels, where it costs a full syscall per event, so it is no longer
     * the default — it stays available for anyone who wants NTP-independent
     * timestamps. */
    env = getenv("TRACE_CLOCK");
    if (env && strcmp(env, "raw") == 0) {
        g_clock_id = CLOCK_MONOTONIC_RAW;
    } else if (env && strcmp(env, "mono") == 0) {
        g_clock_id = CLOCK_MONOTONIC;
    } else if (TRACE_HAVE_TICKS && trace_ticks_usable()
               && (!env || strcmp(env, "tsc") == 0 || strcmp(env, "auto") == 0)) {
        g_use_ticks = 1;
    } else if (env && strcmp(env, "tsc") == 0) {
        fprintf(stderr, "callsight: no invariant cycle counter here, "
                        "using CLOCK_MONOTONIC\n");
    }

    if (g_use_ticks) {
        g_tick_hz = trace_ticks_hz_hint();
        if (g_tick_hz == 0)
            g_tick_hz = trace_calibrate_hz();
        if (g_tick_hz == 0) {   /* calibration failed; don't guess */
            g_use_ticks = 0;
            fprintf(stderr, "callsight: cycle-counter calibration failed, "
                            "using CLOCK_MONOTONIC\n");
        }
    }
    g_t0_ticks = g_use_ticks ? trace_ticks() : 0;
    g_t0_ns = trace_mono_ns();

    dl_iterate_phdr(trace_phdr_cb, &g_load_bias);

    /* Measured before any output is opened: the probe throws its batches
     * away, and every consumer of the value wants it up front. */
    if (g_mode == TRACE_MODE_EVENTS)
        g_hook_ns = trace_measure_hook();

    env = getenv("TRACE_SHM");
    if (env && env[0] != '\0' && g_mode == TRACE_MODE_EVENTS) {
        uint32_t size = TRACE_SHM_DEF_SIZE;
        uint64_t v = trace_env_u64("TRACE_SHM_SIZE", 0);
        if (v >= 4096 && v <= UINT32_MAX)
            size = (uint32_t)v;
        g_shm = trace_shm_attach(env, size);
        if (g_shm) {
            /* The drain client cannot know how to read our timestamps or
             * addresses; publish that with the ring so it can forward it. */
            g_shm->flags = (g_use_ticks ? TRACE_HF_TICKS : 0u)
                         | trace_endian_flag();
            g_shm->load_bias = g_load_bias;
            g_shm->tick_hz = g_use_ticks ? g_tick_hz : 0;
            g_shm->t0_ticks = g_t0_ticks;
            g_shm->t0_ns = g_t0_ns;
            g_shm->hook_ns = g_hook_ns;
            __sync_fetch_and_add(&g_shm->writers, 1);
        } else {
            fprintf(stderr, "callsight: cannot attach shm %s, "
                            "falling back to trace files\n", env);
        }
    }

    if (!g_shm)
        mkdir(g_trace_dir, 0755); /* ignore EEXIST */

    /*
     * Hardware counters. The map defaults to sitting beside the traces,
     * which is the one directory `callsight run` and the web UI already
     * agree on; TRACE_COUNTERS=none turns the feature off without editing
     * a config.
     */
    env = getenv("TRACE_COUNTERS");
    if (!env || env[0] == '\0') {
        char path[TRACE_PATH_MAX];
        snprintf(path, sizeof(path), "%s/callsight.counters", g_trace_dir);
        trace_load_counters(path);
    } else if (strcmp(env, "none") != 0) {
        trace_load_counters(env);
    }
    trace_pmu_setup();

    pthread_key_create(&g_trace_key, trace_tls_destroy);
    pthread_atfork(NULL, NULL, trace_atfork_child);
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

/* Allocate this thread's buffers and open its first segment. Returns 1 when
 * the thread is ready to record. Split out of tls_get so a thread that
 * matched TRACE_THREADS late can complete its setup then. */
NOINSTR static int trace_tls_activate(trace_tls_t *tls) {
    if (tls->active)
        return 1;
    tls->pid = (uint32_t)getpid();

    /* Counters are per thread: the kernel saves and restores a per-thread
     * event across context switches, which is exactly the property wall
     * time does not have. A thread that cannot open its group simply
     * records no counters — the rest of its trace is unaffected. */
    if (g_pmu_n && !tls->pmu_ready) {
        if (trace_pmu_open(&tls->pmu, g_pmu_type, g_pmu_config, g_pmu_n)
                != TRACE_PMU_OFF)
            tls->pmu_ready = 1;
    }

    if (g_mode == TRACE_MODE_SUMMARY) {
        if (!tls->tab && !trace_sum_init(tls))
            return 0;
        tls->active = 1;
        return 1;
    }

    if (!tls->buf) {
        tls->buf = (trace_event_t *)calloc(TRACE_BUF_CAPACITY,
                                           sizeof(trace_event_t));
        if (!tls->buf)
            return 0;
    }
    if (g_shm) {
        tls->active = 1;      /* streaming: nothing per-thread to open */
        trace_counter_markers(tls);
        return 1;
    }
    if (!trace_open_segment(tls))
        return 0;
    tls->active = 1;
    atomic_fetch_add(&g_threads, 1u);
    return 1;
}

NOINSTR static trace_tls_t *trace_tls_get(void) {
    trace_tls_t *tls = tl_trace;
    if (tls) {
        if (!tls->active && !tls->skip && !trace_tls_activate(tls))
            return NULL;   /* post-fork reopen failed */
        return tls;
    }

    tls = (trace_tls_t *)calloc(1, sizeof(*tls));
    if (!tls) return NULL;

    tls->out_fd = -1;
    tls->tid = (uint32_t)syscall(SYS_gettid);
    tls->skip = trace_thread_filtered();
    if (!tls->skip && !trace_tls_activate(tls)) {
        free(tls->buf);
        free(tls);
        return NULL;
    }

    tl_trace = tls;
    pthread_setspecific(g_trace_key, tls);
    return tls;
}

/* --- Hardware counters: the hot path --- */

/*
 * A selected function was entered: stack its counter values.
 *
 * The whole cost of this feature sits behind the g_pmu_n test in the
 * caller. A function that was not selected pays one predictable branch and
 * one hash probe, which is the same bargain compile-time exclusion already
 * makes.
 */
NOINSTR static void trace_counter_enter(trace_tls_t *tls, uint64_t fn,
                                        uint64_t now) {
    trace_cframe_t *cf;

    if (!tls->pmu_ready || tls->cdepth >= TRACE_PMU_DEPTH)
        return;
    if (!trace_sel_find(fn))
        return;

    cf = &tls->cstack[tls->cdepth++];
    cf->fn = fn;
    cf->t0 = now;
    cf->reads = 0;
    memset(cf->child, 0, sizeof(cf->child));
    trace_pmu_read(&tls->pmu, cf->in);
    if (tls->cdepth > 1)
        tls->cstack[tls->cdepth - 2].reads++;
}

/*
 * The matching exit: fill out[] with this call's deltas, or return 0.
 *
 * Two corrections, both plain bookkeeping over counts we already have
 * rather than estimates:
 *   - the instrumentation's own footprint, once for this call's own pair of
 *     reads and once for every read made inside it;
 *   - nothing else. Wall time is left alone deliberately; the reads perturb
 *     it, and the report says so rather than quietly adjusting it.
 */
NOINSTR static int trace_counter_exit(trace_tls_t *tls, uint64_t fn,
                                      uint64_t now, uint64_t *incl,
                                      uint64_t *child) {
    uint64_t vals[TRACE_COUNTER_MAX];
    trace_cframe_t *cf;
    uint64_t dur, cost;
    unsigned i;
    uint32_t d;

    if (!tls->pmu_ready || tls->cdepth == 0)
        return 0;
    /* Nearest unmatched enter for this function; frames above it were left
     * dangling by a longjmp and are dropped, matching what the time path
     * and the offline analyzer both do. */
    d = tls->cdepth;
    while (d > 0 && tls->cstack[d - 1].fn != fn)
        d--;
    if (d == 0)
        return 0;
    tls->cdepth = d - 1;
    cf = &tls->cstack[tls->cdepth];

    trace_pmu_read(&tls->pmu, vals);
    dur = now > cf->t0 ? now - cf->t0 : 0;

    for (i = 0; i < g_pmu_n; i++) {
        uint64_t delta = vals[i] > cf->in[i] ? vals[i] - cf->in[i] : 0;
        /* One pair of reads for this call, plus every read made inside it. */
        cost = (uint64_t)(cf->reads + 1u) * g_pmu_self[i];
        delta = delta > cost ? delta - cost : 0;
        incl[i] = delta;
        child[i] = cf->child[i];
        if (tls->cdepth > 0)
            tls->cstack[tls->cdepth - 1].child[i] += delta;
    }
    for (i = g_pmu_n; i < TRACE_COUNTER_MAX; i++)
        incl[i] = child[i] = 0;
    if (tls->cdepth > 0)
        tls->cstack[tls->cdepth - 1].reads += cf->reads + 1u;

    /*
     * Guard rail. A function far shorter than the reads it pays for is
     * measuring the instrument, so after enough samples to be sure, stop
     * reading it and say which function and why. The evidence is gathered
     * only until the verdict is in.
     */
    if (g_pmu_floor) {
        trace_sel_t *s = trace_sel_find(fn);
        if (s) {
            uint64_t n = atomic_fetch_add_explicit(&s->calls, 1,
                                                   memory_order_relaxed) + 1;
            uint64_t total = atomic_fetch_add_explicit(&s->dur, dur,
                                                       memory_order_relaxed)
                             + dur;
            if (n >= TRACE_PMU_JUDGE_AFTER && total / n < g_pmu_floor) {
                atomic_store_explicit(&s->demoted, 1, memory_order_relaxed);
                atomic_fetch_add_explicit(&g_pmu_skipped, 1,
                                          memory_order_relaxed);
            }
        }
    }
    return 1;
}

/* --- Hooks --- */

/* The hot path proper: timestamp, store, flush when the batch is full. */
NOINSTR static void trace_record_inner(trace_tls_t *tls, void *this_fn,
                                       void *call_site, uint8_t kind) {
    trace_event_t *ev;
    uint64_t now = trace_now();
    uint64_t fn = (uint64_t)(uintptr_t)this_fn;
    uint64_t cin[TRACE_COUNTER_MAX], cch[TRACE_COUNTER_MAX];
    int counted = 0;

    /* Before the record is written, so composing it does not land inside
     * the measured window. */
    if (g_pmu_n && kind == (uint8_t)TRACE_EVENT_EXIT)
        counted = trace_counter_exit(tls, fn, now, cin, cch);

    ev = &tls->buf[tls->len++];
    ev->ts_ns = now;
    ev->func_addr = fn;
    ev->caller_addr = (uint64_t)(uintptr_t)call_site;
    ev->tid = tls->tid;
    ev->kind = kind;
    ev->_pad[0] = ev->_pad[1] = ev->_pad[2] = 0;

    if (counted && tls->len < TRACE_BUF_CAPACITY) {
        /* Straight after the EXIT it belongs to, so the reader can attach it
         * to the call it just closed without carrying any state of its own. */
        trace_event_t *cv = &tls->buf[tls->len++];
        cv->ts_ns = cin[0];
        cv->func_addr = cin[1];
        cv->caller_addr = cin[2];
        cv->tid = tls->tid;
        cv->kind = (uint8_t)TRACE_EVENT_COUNTER;
        cv->_pad[0] = cv->_pad[1] = cv->_pad[2] = 0;
    }

    /* Flush first: a flush is a write syscall, and taking the entry reading
     * before it would charge that syscall to the function about to run. */
    if (tls->len >= TRACE_BUF_CAPACITY)
        trace_tls_flush(tls);

    /* Last, for the same reason: the function body starts right after. */
    if (g_pmu_n && kind == (uint8_t)TRACE_EVENT_ENTER)
        trace_counter_enter(tls, fn, now);
}

/*
 * Claim a slice of the global event cap for this thread.
 *
 * The cap used to be enforced with an atomic increment on every event,
 * which put every thread on the same cache line for the duration of the
 * run. Handing out slices of a full buffer moves that to once per 8192
 * events. Unused slices are not returned, so the cap is an upper bound.
 */
NOINSTR static int trace_reserve(trace_tls_t *tls) {
    uint64_t want = TRACE_BUF_CAPACITY;
    uint64_t prev, avail;

    prev = (uint64_t)atomic_fetch_add(&g_events_used, (uint_fast64_t)want);
    if (prev >= g_max_events) {
        atomic_fetch_sub(&g_events_used, (uint_fast64_t)want);
        return 0;
    }
    avail = g_max_events - prev;
    if (avail < want) {
        atomic_fetch_sub(&g_events_used, (uint_fast64_t)(want - avail));
        tls->reserved = avail;
    } else {
        tls->reserved = want;
    }
    return 1;
}

NOINSTR static void trace_record(void *this_fn, void *call_site, uint8_t kind) {
    pthread_once(&g_trace_once, trace_global_init);
    if (!g_trace_enabled)
        return;
    if (atomic_load_explicit(&g_stopped, memory_order_relaxed))
        return;

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

    if (g_mode == TRACE_MODE_SUMMARY) {
        trace_sum_record(tls, this_fn, kind);
        tls->in_hook = 0;
        return;
    }

    if (g_max_events > 0 && tls->reserved == 0 && !trace_reserve(tls)) {
        trace_stop(tls, TRACE_MARK_MAXEVENTS, g_max_events);
        trace_tls_flush(tls);
        tls->in_hook = 0;
        return;
    }
    if (g_max_events > 0)
        tls->reserved--;

    trace_record_inner(tls, this_fn, call_site, kind);
    tls->in_hook = 0;
}

NOINSTR void __cyg_profile_func_enter(void *this_fn, void *call_site) {
    trace_record(this_fn, call_site, TRACE_EVENT_ENTER);
}

NOINSTR void __cyg_profile_func_exit(void *this_fn, void *call_site) {
    trace_record(this_fn, call_site, TRACE_EVENT_EXIT);
}
