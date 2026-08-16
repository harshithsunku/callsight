/*
 * Overhead benchmark workload.
 *
 * A deliberately cheap function called a great many times: the point is to
 * measure what the instrumentation costs, so the work per call has to be
 * small enough that the hooks dominate. Real code is nowhere near this
 * hostile, which is why the driver also reports the ratio on a realistic
 * workload.
 *
 * The program times only the measured loop, using CLOCK_MONOTONIC, and
 * prints the elapsed nanoseconds — process startup, the runtime's own
 * initialization and the exit-time flush are excluded on purpose, since
 * they are paid once and not per call.
 *
 * Built twice by run_bench.py: once plain, once with
 * -finstrument-functions. The difference between the two, divided by the
 * number of hook calls, is the per-event cost.
 */

#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static volatile uint64_t sink;

/* How much work each call does, in arbitrary mixing rounds. Zero is the
 * hostile case (a call that does nothing, so the hooks are the entire
 * cost); a few dozen rounds is what a function in real code looks like. */
static long g_work;

/* Two levels so the measurement includes a nested call, which is what a
 * real call graph looks like; both get hooks when instrumented. */
static uint64_t bench_leaf(uint64_t x) {
    for (long i = 0; i < g_work; i++)
        x = x * 6364136223846793005ull + 1442695040888963407ull;
    return x * 2654435761u + 1;
}

static uint64_t bench_mid(uint64_t x) {
    return bench_leaf(x) ^ (x >> 3);
}

static uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

int main(int argc, char **argv) {
    long iters = argc > 1 ? atol(argv[1]) : 2000000L;
    uint64_t acc = 0;

    g_work = argc > 2 ? atol(argv[2]) : 0;

    uint64_t t0 = now_ns();
    for (long i = 0; i < iters; i++)
        acc += bench_mid((uint64_t)i);
    uint64_t t1 = now_ns();

    sink = acc;
    /* 2 instrumented functions x 2 hooks (enter+exit) per iteration. */
    printf("elapsed_ns=%llu iters=%ld hooks=%lld\n",
           (unsigned long long)(t1 - t0), iters,
           (long long)iters * 4);
    return 0;
}
