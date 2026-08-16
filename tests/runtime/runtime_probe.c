/*
 * Workload driver for the runtime tests.
 *
 * One instrumented binary with a subcommand per scenario, so each test
 * exercises the real hooks rather than a mock. Everything here is compiled
 * WITH -finstrument-functions; the runtime is linked in as usual.
 *
 * Subcommands (see tests/runtime/test_runtime.py):
 *   spin N            N units of nested work on the main thread
 *   threads T N       T worker threads, N units each
 *   churn T N         T threads created and joined ONE AT A TIME, so the
 *                     kernel recycles thread ids — the case that used to
 *                     append a second file header mid-capture
 *   fork N            parent and child both trace
 *   fsize N BYTES     cap the file size with RLIMIT_FSIZE so writes fail,
 *                     which is how a full disk behaves without needing one
 *   accuracy N USEC   N calls that each sleep USEC: known counts, known time
 */

/* Under a strict -std=c11 the POSIX declarations (nanosleep, fork,
 * setrlimit, pthreads) are hidden; ask for them explicitly so this file
 * builds the same way whatever -std the harness picks. */
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif

#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static unsigned long sink;

int probe_leaf(int x) {
    return (x * 2654435761u) >> 3;
}

int probe_mid(int x) {
    int acc = 0;
    for (int i = 0; i < 8; i++)
        acc += probe_leaf(x + i);
    return acc;
}

int probe_top(int n) {
    int acc = 0;
    for (int i = 0; i < n; i++)
        acc += probe_mid(i);
    return acc;
}

/* Sleep without calling an instrumented function, so the accuracy check
 * measures the sleep and not the harness. */
int probe_wait(long usec) {
    struct timespec req;
    req.tv_sec = usec / 1000000L;
    req.tv_nsec = (usec % 1000000L) * 1000L;
    nanosleep(&req, NULL);
    return 1;
}

/* The accumulator exists only to keep the work from being optimized away.
 * It has to be atomic: a plain += from several threads is a data race, and
 * a race in the harness would drown out any race in the runtime, which is
 * the thing these tests are meant to be watching. */
static void *worker(void *arg) {
    long n = (long)arg;
    __atomic_fetch_add(&sink, (unsigned long)probe_top((int)n),
                       __ATOMIC_RELAXED);
    return NULL;
}

static int run_threads(int nthreads, long n) {
    pthread_t th[64];
    if (nthreads > 64)
        nthreads = 64;
    for (int i = 0; i < nthreads; i++)
        pthread_create(&th[i], NULL, worker, (void *)n);
    for (int i = 0; i < nthreads; i++)
        pthread_join(th[i], NULL);
    return 0;
}

/* Sequential create/join: the kernel hands the next thread the id the last
 * one just released, which is what makes tid reuse easy to reproduce. */
static int run_churn(int rounds, long n) {
    for (int i = 0; i < rounds; i++) {
        pthread_t t;
        pthread_create(&t, NULL, worker, (void *)n);
        pthread_join(t, NULL);
    }
    return 0;
}

static int run_fork(long n) {
    pid_t pid = fork();
    if (pid < 0) {
        perror("fork");
        return 1;
    }
    if (pid == 0) {
        sink += (unsigned long)probe_top((int)n);
        _exit(0);
    }
    sink += (unsigned long)probe_top((int)n);
    int status = 0;
    waitpid(pid, &status, 0);
    printf("parent=%d child=%d\n", (int)getpid(), (int)pid);
    return 0;
}

static int run_fsize(long n, long bytes) {
    struct rlimit rl;
    rl.rlim_cur = (rlim_t)bytes;
    rl.rlim_max = (rlim_t)bytes;
    /* Exceeding RLIMIT_FSIZE raises SIGXFSZ before write() returns EFBIG;
     * ignoring it turns a full "disk" into the error path we want to test. */
    signal(SIGXFSZ, SIG_IGN);
    if (setrlimit(RLIMIT_FSIZE, &rl) != 0) {
        perror("setrlimit");
        return 1;
    }
    sink += (unsigned long)probe_top((int)n);
    return 0;
}

static int run_accuracy(long calls, long usec) {
    for (long i = 0; i < calls; i++)
        sink += (unsigned long)probe_wait(usec);
    printf("calls=%ld usec=%ld\n", calls, usec);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <subcommand> [args]\n", argv[0]);
        return 2;
    }
    const char *cmd = argv[1];
    long a = argc > 2 ? atol(argv[2]) : 0;
    long b = argc > 3 ? atol(argv[3]) : 0;

    if (strcmp(cmd, "spin") == 0) {
        sink += (unsigned long)probe_top((int)a);
        return 0;
    }
    if (strcmp(cmd, "threads") == 0)
        return run_threads((int)a, b);
    if (strcmp(cmd, "churn") == 0)
        return run_churn((int)a, b);
    if (strcmp(cmd, "fork") == 0)
        return run_fork(a);
    if (strcmp(cmd, "fsize") == 0)
        return run_fsize(a, b);
    if (strcmp(cmd, "accuracy") == 0)
        return run_accuracy(a, b);

    fprintf(stderr, "unknown subcommand: %s\n", cmd);
    return 2;
}
