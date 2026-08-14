#include "thread_pool.h"
#include "sync.h"
#include "../core/logging.h"
#include "../utils/rng.h"
#include "../utils/timer.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* Thread pool internals */
struct thread_pool {
    thread_info_t *threads;
    int num_threads;
    volatile int running;
};

/* Role names for thread naming */
static const char *role_prefixes[] = {
    "matrix-worker",
    "stats-analyzer",
    "signal-fft",
    "sort-engine",
    "graph-traversal",
    "crypto-hash",
    "compress-rle",
    "memory-stress",
    "contention-sim",
    "mixed-worker"
};

/* Forward declarations - thread work functions defined in workloads.c */
extern void workload_matrix(thread_info_t *info);
extern void workload_stats(thread_info_t *info);
extern void workload_signal(thread_info_t *info);
extern void workload_sort(thread_info_t *info);
extern void workload_graph(thread_info_t *info);
extern void workload_crypto(thread_info_t *info);
extern void workload_compress(thread_info_t *info);
extern void workload_memory(thread_info_t *info);
extern void workload_contention(thread_info_t *info);
extern void workload_mixed(thread_info_t *info);

/* Work function dispatch table */
static work_fn work_functions[] = {
    workload_matrix,
    workload_stats,
    workload_signal,
    workload_sort,
    workload_graph,
    workload_crypto,
    workload_compress,
    workload_memory,
    workload_contention,
    workload_mixed
};

/* Thread main function */
static void *thread_main(void *arg) {
    thread_info_t *info = (thread_info_t *)arg;

    /* Set thread name for perf */
    pthread_setname_np(pthread_self(), info->name);

    /* Set thread-local name for logging */
    strncpy(tl_thread_name, info->name, sizeof(tl_thread_name) - 1);
    tl_thread_name[sizeof(tl_thread_name) - 1] = '\0';

    /* Seed thread-local RNG */
    rng_seed_thread((uint64_t)info->id * 12345 + 67890);

    LOG_INFO("started (role=%s)", role_prefixes[info->role]);

    /* Get the work function for this role */
    work_fn fn = work_functions[info->role];

    while (info->running) {
        timer_t_ml t;
        timer_start(&t);

        /* Do work */
        fn(info);
        info->iterations++;

        timer_stop(&t);
        info->last_latency_ms = timer_elapsed_ms(&t);
    }

    LOG_INFO("stopped after %lu iterations", (unsigned long)info->iterations);
    return NULL;
}

/* Create thread pool */
thread_pool_t *threadpool_create(int num_threads) {
    thread_pool_t *pool = (thread_pool_t *)malloc(sizeof(thread_pool_t));
    if (!pool) return NULL;

    pool->num_threads = num_threads;
    pool->running = 0;
    pool->threads = (thread_info_t *)calloc((size_t)num_threads, sizeof(thread_info_t));
    if (!pool->threads) { free(pool); return NULL; }

    /* Assign roles in a balanced distribution */
    for (int i = 0; i < num_threads; i++) {
        thread_info_t *ti = &pool->threads[i];
        ti->id = i;
        ti->running = 0;
        ti->paused = 0;
        ti->iterations = 0;
        ti->last_latency_ms = 0.0;
        ti->pool = pool;

        /* Distribute roles */
        if (i < 4) {
            ti->role = ROLE_MATRIX_WORKER;
        } else if (i < 7) {
            ti->role = ROLE_STATS_ANALYZER;
        } else if (i < 10) {
            ti->role = ROLE_SIGNAL_FFT;
        } else if (i < 13) {
            ti->role = ROLE_SORT_ENGINE;
        } else if (i < 15) {
            ti->role = ROLE_GRAPH_TRAVERSAL;
        } else if (i < 18) {
            ti->role = ROLE_CRYPTO_HASH;
        } else if (i < 20) {
            ti->role = ROLE_COMPRESS_RLE;
        } else if (i < 22) {
            ti->role = ROLE_MEMORY_STRESS;
        } else if (i < 24) {
            ti->role = ROLE_CONTENTION_SIM;
        } else {
            ti->role = ROLE_MIXED_WORKER;
        }

        /* Generate unique thread name */
        int role_count = 0;
        for (int j = 0; j < i; j++) {
            if (pool->threads[j].role == ti->role) role_count++;
        }
        snprintf(ti->name, sizeof(ti->name), "%s-%d", role_prefixes[ti->role], role_count + 1);
    }

    return pool;
}

/* Start all threads */
void threadpool_start(thread_pool_t *pool) {
    if (!pool || pool->running) return;
    pool->running = 1;

    for (int i = 0; i < pool->num_threads; i++) {
        pool->threads[i].running = 1;
        pthread_create(&pool->threads[i].thread, NULL, thread_main, &pool->threads[i]);
    }
}

/* Stop all threads */
void threadpool_stop(thread_pool_t *pool) {
    if (!pool || !pool->running) return;

    pool->running = 0;
    for (int i = 0; i < pool->num_threads; i++) {
        pool->threads[i].running = 0;
    }

    for (int i = 0; i < pool->num_threads; i++) {
        pthread_join(pool->threads[i].thread, NULL);
    }
}

/* Destroy thread pool */
void threadpool_destroy(thread_pool_t *pool) {
    if (!pool) return;
    if (pool->running) threadpool_stop(pool);
    free(pool->threads);
    free(pool);
}

/* Get thread info */
thread_info_t *threadpool_get_thread(thread_pool_t *pool, int idx) {
    if (!pool || idx < 0 || idx >= pool->num_threads) return NULL;
    return &pool->threads[idx];
}

/* Get thread count */
int threadpool_get_size(thread_pool_t *pool) {
    return pool ? pool->num_threads : 0;
}

/* Print thread statistics */
void threadpool_print_stats(const thread_pool_t *pool) {
    if (!pool) return;
    printf("\n=== Thread Statistics ===\n");
    printf("%-25s %12s %12s\n", "Thread", "Iterations", "Last Lat(ms)");
    printf("%-25s %12s %12s\n", "------", "----------", "------------");
    for (int i = 0; i < pool->num_threads; i++) {
        const thread_info_t *ti = &pool->threads[i];
        printf("%-25s %12lu %12.2f\n", ti->name,
               (unsigned long)ti->iterations, ti->last_latency_ms);
    }
}

/* Get total iterations */
uint64_t threadpool_total_iterations(const thread_pool_t *pool) {
    uint64_t total = 0;
    for (int i = 0; i < pool->num_threads; i++) {
        total += pool->threads[i].iterations;
    }
    return total;
}
