#ifndef CALLSIGHT_TRACE_PMU_H
#define CALLSIGHT_TRACE_PMU_H

/*
 * Hardware performance counters for selected functions.
 *
 * Reading the counter at a function's entry and exit gives that call's exact
 * instruction count (or cache misses, or whatever event was configured).
 * Unlike wall time, that number is deterministic: the same work reports the
 * same figure run after run, which is what makes it a regression signal you
 * can gate on at 1% rather than eyeball at 10%.
 *
 * Reading is the easy part. Most of this file exists to refuse to report a
 * number that is not real:
 *
 *   - perf_event_open can succeed while the event is never scheduled onto
 *     hardware. Inside a container the host PMU is usually not exposed at
 *     all, and a naive implementation then reports zero instructions for
 *     every function and looks perfectly healthy doing it. time_running == 0
 *     is the tell, and trace_pmu_prove() is the check.
 *   - If more events are requested than the PMU has registers, the kernel
 *     multiplexes them and scales the values by time_enabled/time_running. A
 *     scaled value is an estimate; this project sells exactness, so
 *     multiplexing is counted and reported rather than quietly corrected.
 *   - Reading costs wildly different amounts by platform. On x86-64 the
 *     rdpmc instruction reads a counter from userspace in a few ns. On arm64
 *     Linux does not enable userspace counter access at all, so every read
 *     is a syscall of one to several microseconds. That difference decides
 *     which functions are worth counting, so it is measured at startup,
 *     published in the trace, and used to skip functions too short to
 *     measure.
 *
 * All events are opened as one group with PERF_FORMAT_GROUP, so a single
 * read returns every value. Where reads are syscalls — arm64, and every
 * 32-bit target — three events then cost the same as one, which is what
 * makes counting more than one event viable off x86 at all.
 *
 * Counters are per thread: each thread opens its own group, because the
 * kernel saves and restores a per-thread counter across context switches.
 * That is precisely the property wall-clock time does not have.
 */

#include <errno.h>
#include <linux/perf_event.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#ifndef NOINSTR
#define NOINSTR __attribute__((no_instrument_function))
#endif

/* Kept in step with COUNTER_MAX_EVENTS in flags.py. Three fits every PMU
 * callsight is likely to meet and leaves a register spare. */
#define TRACE_PMU_MAX_EVENTS 3

/* How this thread reads its counters. */
#define TRACE_PMU_OFF     0  /* unavailable, or proven not to count */
#define TRACE_PMU_RDPMC   1  /* userspace register read (x86-64) */
#define TRACE_PMU_SYSCALL 2  /* read(2) per sample (arm64, and the fallback) */

typedef struct {
    int      fd[TRACE_PMU_MAX_EVENTS];   /* fd[0] is the group leader */
    unsigned n;                          /* events actually open */
    struct perf_event_mmap_page *page;   /* leader's page, for rdpmc */
    int      mode;
    uint64_t multiplexed;    /* reads taken while the PMU was time-slicing */
    /* Scratch for the grouped read: nr, then one value per event. */
    uint64_t buf[TRACE_PMU_MAX_EVENTS + 1];
} trace_pmu_t;

/* ------------------------------------------------------------------ */
/* Platform read primitives                                            */
/* ------------------------------------------------------------------ */

#if defined(__x86_64__) || defined(__i386__)
NOINSTR static inline uint64_t trace_rdpmc(uint32_t counter) {
    uint32_t lo, hi;
    __asm__ __volatile__("rdpmc" : "=a"(lo), "=d"(hi) : "c"(counter));
    return ((uint64_t)hi << 32) | lo;
}
#define TRACE_PMU_HAVE_RDPMC 1
#else
/* arm64 has no userspace equivalent: PMUSERENR_EL0 stays off, because
 * enabling it would leak counter state between processes. */
NOINSTR static inline uint64_t trace_rdpmc(uint32_t c) { (void)c; return 0; }
#define TRACE_PMU_HAVE_RDPMC 0
#endif

NOINSTR static inline uint64_t trace_pmu_now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* ------------------------------------------------------------------ */
/* Reading                                                             */
/* ------------------------------------------------------------------ */

/*
 * Read every event into out[].
 *
 * The rdpmc path only handles a single event: reading a group through the
 * mmap page requires one page per event and a seqlock dance per read, and
 * the whole point of the group is that the syscall path gets all values for
 * one syscall. One event on x86 is the case that benefits from rdpmc, and
 * that is the case it covers.
 *
 * index == 0 in the mmap page means the event is not on a hardware counter
 * right now — the multiplexing case. There is nothing to read, so it falls
 * back to the syscall rather than returning a plausible-looking zero.
 */
NOINSTR static inline void trace_pmu_read(trace_pmu_t *p, uint64_t *out) {
    unsigned i;

#if TRACE_PMU_HAVE_RDPMC
    if (p->mode == TRACE_PMU_RDPMC && p->n == 1) {
        struct perf_event_mmap_page *pc = p->page;
        uint32_t seq, idx;
        uint64_t count, offset;
        do {
            seq = pc->lock;
            __atomic_thread_fence(__ATOMIC_ACQUIRE);
            idx = pc->index;
            offset = pc->offset;
            if (idx == 0) {
                p->multiplexed++;
                goto syscall_path;
            }
            count = trace_rdpmc(idx - 1);
            __atomic_thread_fence(__ATOMIC_ACQUIRE);
        } while (pc->lock != seq);
        out[0] = count + offset;
        return;
    }
syscall_path:
#endif
    /* PERF_FORMAT_GROUP: buf[0] is the event count, then one value each. */
    if (read(p->fd[0], p->buf, sizeof(p->buf[0]) * (p->n + 1))
            != (ssize_t)(sizeof(p->buf[0]) * (p->n + 1))) {
        for (i = 0; i < p->n; i++)
            out[i] = 0;
        return;
    }
    for (i = 0; i < p->n; i++)
        out[i] = p->buf[i + 1];
}

/* ------------------------------------------------------------------ */
/* Setup and validation                                                */
/* ------------------------------------------------------------------ */

NOINSTR static inline int trace_pmu_open_fd(uint32_t type, uint64_t config,
                                     int group_fd, int grouped) {
    struct perf_event_attr attr;

    memset(&attr, 0, sizeof(attr));
    attr.type = type;
    attr.size = sizeof(attr);
    attr.config = config;
    /* perf_event_paranoid >= 1 requires this, and only our own userspace
     * work is wanted anyway. */
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;
    if (grouped)
        attr.read_format = PERF_FORMAT_GROUP;
    else
        attr.read_format = PERF_FORMAT_TOTAL_TIME_ENABLED
                         | PERF_FORMAT_TOTAL_TIME_RUNNING;
    /* pid 0, cpu -1: this thread, followed across cores, so the kernel
     * saves and restores the counter on every context switch. */
    return (int)syscall(SYS_perf_event_open, &attr, 0, -1, group_fd, 0);
}

/*
 * Prove the counter actually counts before trusting it.
 *
 * A loop of known size runs and the delta is checked for plausibility. This
 * is the most important function in the file: without it, the failure mode
 * is a full report of zeros that looks exactly like a report of a program
 * that does nothing.
 *
 * Done once per process on a throwaway single event with the timing fields
 * enabled, so the check does not depend on the grouped layout it validates.
 */
NOINSTR static inline int trace_pmu_prove(uint32_t type, uint64_t config) {
    volatile uint64_t sink = 0;
    uint64_t before[3], after[3];
    int fd, i, ok = 0;

    fd = trace_pmu_open_fd(type, config, -1, 0);
    if (fd < 0)
        return 0;
    if (read(fd, before, sizeof(before)) != (ssize_t)sizeof(before))
        goto out;
    for (i = 0; i < 200000; i++)
        sink += (uint64_t)i;
    if (read(fd, after, sizeof(after)) != (ssize_t)sizeof(after))
        goto out;

    /* after[2] is time_running: zero means the event never reached
     * hardware, which is exactly the container case. */
    if (after[2] == 0)
        goto out;
    if (after[0] <= before[0])
        goto out;               /* opened, ran, did not count */
    if (after[0] - before[0] < 10000)
        goto out;               /* implausibly low for 200k iterations */
    ok = 1;
out:
    close(fd);
    return ok;
}

NOINSTR static inline void trace_pmu_close(trace_pmu_t *p) {
    unsigned i;
    if (p->page) {
        munmap(p->page, 2 * (size_t)getpagesize());
        p->page = NULL;
    }
    for (i = 0; i < p->n; i++) {
        if (p->fd[i] >= 0)
            close(p->fd[i]);
        p->fd[i] = -1;
    }
    p->n = 0;
    p->mode = TRACE_PMU_OFF;
}

/*
 * Open this thread's counter group. Returns the usable mode.
 *
 * events[] is {type, config} pairs resolved by the host from the counter
 * map, so the runtime needs no event-name table of its own.
 */
NOINSTR static inline int trace_pmu_open(trace_pmu_t *p, const uint32_t *types,
                                  const uint64_t *configs, unsigned n) {
    unsigned i;

    memset(p, 0, sizeof(*p));
    for (i = 0; i < TRACE_PMU_MAX_EVENTS; i++)
        p->fd[i] = -1;
    p->mode = TRACE_PMU_OFF;
    if (n == 0 || n > TRACE_PMU_MAX_EVENTS)
        return TRACE_PMU_OFF;

    for (i = 0; i < n; i++) {
        int fd = trace_pmu_open_fd(types[i], configs[i],
                                   i == 0 ? -1 : p->fd[0], 1);
        if (fd < 0) {
            trace_pmu_close(p);
            return TRACE_PMU_OFF;
        }
        p->fd[i] = fd;
        p->n = i + 1;
    }

    /* rdpmc is only worth taking for a lone event; a group has to go
     * through the syscall to come back consistent. */
    if (TRACE_PMU_HAVE_RDPMC && n == 1) {
        void *m = mmap(NULL, 2 * (size_t)getpagesize(), PROT_READ,
                       MAP_SHARED, p->fd[0], 0);
        if (m != MAP_FAILED) {
            struct perf_event_mmap_page *pc = m;
            if (pc->cap_user_rdpmc && pc->index != 0) {
                p->page = pc;
                p->mode = TRACE_PMU_RDPMC;
            } else {
                munmap(m, 2 * (size_t)getpagesize());
            }
        }
    }
    if (p->mode == TRACE_PMU_OFF)
        p->mode = TRACE_PMU_SYSCALL;
    return p->mode;
}

/*
 * What one read costs, and what the instrumentation's own reads add to the
 * count.
 *
 * read_ns decides which functions are worth counting at all; self[] comes
 * off every measured call, the same correction the timing path already
 * makes with hook_ns.
 */
NOINSTR static inline void trace_pmu_calibrate(trace_pmu_t *p, uint64_t *read_ns,
                                        uint64_t *self) {
    enum { N = 512 };
    uint64_t a[TRACE_PMU_MAX_EVENTS], b[TRACE_PMU_MAX_EVENTS];
    uint64_t t0, t1;
    unsigned i;
    int k;

    t0 = trace_pmu_now_ns();
    for (k = 0; k < N; k++)
        trace_pmu_read(p, a);
    t1 = trace_pmu_now_ns();
    *read_ns = (t1 - t0) / N;

    /* Two reads back to back with nothing between them: whatever they count
     * is what an empty instrumented call would report. */
    trace_pmu_read(p, a);
    trace_pmu_read(p, b);
    for (i = 0; i < p->n; i++)
        self[i] = b[i] > a[i] ? b[i] - a[i] : 0;
}

NOINSTR static inline const char *trace_pmu_mode_name(int mode) {
    switch (mode) {
    case TRACE_PMU_RDPMC:   return "rdpmc";
    case TRACE_PMU_SYSCALL: return "read";
    default:                return "off";
    }
}

#endif /* CALLSIGHT_TRACE_PMU_H */
