/*
 * MatrixLab — Multi-threaded numerical computation engine
 *
 * A realistic, long-running system workload designed for profiling
 * with tools like Linux perf. Runs continuously until SIGINT/SIGTERM.
 */

#include "core/logging.h"
#include "core/errors.h"
#include "crypto/crc32.h"
#include "threads/thread_pool.h"
#include "threads/sync.h"
#include "utils/rng.h"
#include "utils/timer.h"
#include "workloads.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>

/* Global shutdown flag */
static volatile int g_shutdown = 0;

/* Global thread pool */
static thread_pool_t *g_pool = NULL;

/* Start time */
static double g_start_time;

/* Signal handler for clean shutdown */
static void signal_handler(int sig) {
    (void)sig;
    g_shutdown = 1;
}

/* Read configuration from environment variables */
static int config_get_threads(void) {
    const char *env = getenv("MATRIXLAB_THREADS");
    if (env) {
        int val = atoi(env);
        if (val >= 1 && val <= 256) return val;
    }
    return 25; /* default */
}

/* Read throttle from environment */
static int config_get_throttle(void) {
    const char *env = getenv("MATRIXLAB_THROTTLE_US");
    if (env) {
        int val = atoi(env);
        if (val >= 0 && val <= 10000000) return val;
    }
    return 1000; /* default: 1ms */
}

/* Print system status */
__attribute__((noinline))
static void print_status(void) {
    double now = timer_now_ms();
    double elapsed = (now - g_start_time) / 1000.0;
    uint64_t total_iters = threadpool_total_iterations(g_pool);
    int queue_depth = 0; /* simplified */

    contention_metrics_t metrics;
    sync_get_metrics(&metrics);

    printf("[system] uptime=%.1fs iterations=%lu contention=%lu queue=%d phase=%s\n",
           elapsed, (unsigned long)total_iters,
           (unsigned long)metrics.lock_acquisitions, queue_depth,
           phase_name(g_current_phase));
    fflush(stdout);
}

/* Print final summary */
static void print_summary(void) {
    double now = timer_now_ms();
    double elapsed = (now - g_start_time) / 1000.0;

    printf("\n========================================\n");
    printf("  MatrixLab — Shutdown Summary\n");
    printf("========================================\n");
    printf("  Total runtime:    %.2f seconds\n", elapsed);
    printf("  Total iterations: %lu\n", (unsigned long)threadpool_total_iterations(g_pool));

    contention_metrics_t m;
    sync_get_metrics(&m);
    printf("  Lock acquisitions: %lu\n", (unsigned long)m.lock_acquisitions);

    printf("\n");
    threadpool_print_stats(g_pool);
    printf("========================================\n");

    /* Error counts */
    int has_errors = 0;
    for (int i = 1; i <= (int)ERR_EMPTY; i++) {
        uint64_t c = error_get_count((error_code_t)i);
        if (c > 0) {
            if (!has_errors) { printf("\nError counts:\n"); has_errors = 1; }
            printf("  %s: %lu\n", error_code_str((error_code_t)i), (unsigned long)c);
        }
    }
}

int main(void) {
    /* Banner */
    printf("========================================\n");
    printf("  MatrixLab v1.0 — Profiling Workload\n");
    printf("========================================\n");

    /* Configuration */
    int num_threads = config_get_threads();
    int throttle_us = config_get_throttle();
    g_throttle_us = throttle_us;

    printf("  Threads:   %d\n", num_threads);
    printf("  Throttle:  %d us\n", throttle_us);
    printf("  PID:       %d\n", (int)getpid());
    printf("========================================\n\n");

    /* Initialize subsystems */
    logging_init();
    rng_init((uint64_t)time(NULL) ^ (uint64_t)getpid());
    crc32_init_table();
    sync_init(num_threads);

    /* Install signal handlers */
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* Record start time */
    g_start_time = timer_now_ms();

    /* Create and start thread pool */
    g_pool = threadpool_create(num_threads);
    if (!g_pool) {
        fprintf(stderr, "Failed to create thread pool\n");
        return 1;
    }

    printf("[system] Starting %d threads...\n", num_threads);
    threadpool_start(g_pool);
    printf("[system] All threads running. Send SIGINT to stop.\n\n");

    /* Main loop: status reporting + phase control */
    int tick = 0;
    while (!g_shutdown) {
        timer_sleep_ms(1000); /* 1 second tick */
        tick++;

        /* Phase controller */
        phase_controller_step();

        /* Periodic status output */
        if (tick % 5 == 0) {
            print_status();
        }
    }

    /* Shutdown */
    printf("\n[system] Shutting down...\n");
    threadpool_stop(g_pool);

    /* Print summary */
    print_summary();

    /* Cleanup */
    threadpool_destroy(g_pool);
    sync_destroy();
    logging_flush();

    printf("\n[system] Goodbye.\n");
    return 0;
}
