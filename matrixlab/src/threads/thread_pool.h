#ifndef MATRIXLAB_THREAD_POOL_H
#define MATRIXLAB_THREAD_POOL_H

#include <pthread.h>
#include <stddef.h>
#include <stdint.h>

/* Forward declarations */
typedef struct thread_pool thread_pool_t;
typedef struct work_item work_item_t;

/* Thread role enumeration */
typedef enum {
    ROLE_MATRIX_WORKER,
    ROLE_STATS_ANALYZER,
    ROLE_SIGNAL_FFT,
    ROLE_SORT_ENGINE,
    ROLE_GRAPH_TRAVERSAL,
    ROLE_CRYPTO_HASH,
    ROLE_COMPRESS_RLE,
    ROLE_MEMORY_STRESS,
    ROLE_CONTENTION_SIM,
    ROLE_MIXED_WORKER,
    ROLE_COUNT
} thread_role_t;

/* Thread info */
typedef struct {
    pthread_t thread;
    int id;
    thread_role_t role;
    char name[64];
    volatile int running;
    volatile int paused;
    volatile uint64_t iterations;
    volatile double last_latency_ms;
    void *pool;
} thread_info_t;

/* Work function type */
typedef void (*work_fn)(thread_info_t *info);

/* Create thread pool with given size */
thread_pool_t *threadpool_create(int num_threads);

/* Destroy thread pool */
void threadpool_destroy(thread_pool_t *pool);

/* Start all threads */
void threadpool_start(thread_pool_t *pool);

/* Stop all threads */
void threadpool_stop(thread_pool_t *pool);

/* Get thread info by index */
thread_info_t *threadpool_get_thread(thread_pool_t *pool, int idx);

/* Get a thread count */
int threadpool_get_size(thread_pool_t *pool);

/* Print thread statistics */
void threadpool_print_stats(const thread_pool_t *pool);

/* Get total iterations across all threads */
uint64_t threadpool_total_iterations(const thread_pool_t *pool);

#endif
