#ifndef MATRIXLAB_SYNC_H
#define MATRIXLAB_SYNC_H

#include <pthread.h>
#include <stdint.h>

/* Global contention metrics */
typedef struct {
    volatile uint64_t lock_acquisitions;
    volatile uint64_t lock_contentions;
    volatile uint64_t barrier_waits;
    volatile uint64_t cond_signals;
} contention_metrics_t;

extern contention_metrics_t g_contention;

/* Shared data for false sharing simulation */
typedef struct {
    volatile int counter;    /* Each on the same cache line */
    char pad[60];            /* Intentional false sharing without padding */
} false_share_slot_t;

/* Padded version (no false sharing) */
typedef struct {
    volatile int counter;
    char pad[124];           /* Pad to 128 bytes (2 cache lines) */
} padded_slot_t;

/* Global shared resources for contention */
extern pthread_mutex_t g_shared_mutex;
extern pthread_rwlock_t g_shared_rwlock;
extern pthread_barrier_t g_barrier;

/* Contention simulation functions */
__attribute__((noinline))
void sync_contention_heavy(int iterations);

__attribute__((noinline))
void sync_contention_light(int iterations);

/* False sharing simulation */
__attribute__((noinline))
void sync_false_sharing_test(false_share_slot_t *slots, int slot_idx, int iterations);

/* Reader-writer lock simulation */
__attribute__((noinline))
void sync_rwlock_read(int iterations);

__attribute__((noinline))
void sync_rwlock_write(int iterations);

/* Initialize synchronization primitives */
void sync_init(int num_threads);

/* Destroy synchronization primitives */
void sync_destroy(void);

/* Get contention metrics */
void sync_get_metrics(contention_metrics_t *out);

/* Reset contention metrics */
void sync_reset_metrics(void);

#endif
