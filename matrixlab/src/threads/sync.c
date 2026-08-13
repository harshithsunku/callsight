#include "sync.h"
#include "../utils/rng.h"
#include <string.h>
#include <unistd.h>

/* Global contention metrics */
contention_metrics_t g_contention = {0};

/* Global shared resources */
pthread_mutex_t g_shared_mutex = PTHREAD_MUTEX_INITIALIZER;
pthread_rwlock_t g_shared_rwlock = PTHREAD_RWLOCK_INITIALIZER;
pthread_barrier_t g_barrier;

/* Shared counter for contention */
static volatile int g_shared_counter = 0;
static volatile double g_shared_value = 0.0;

/* Initialize synchronization primitives */
void sync_init(int num_threads) {
    pthread_mutex_init(&g_shared_mutex, NULL);
    pthread_rwlock_init(&g_shared_rwlock, NULL);
    if (num_threads > 1) {
        pthread_barrier_init(&g_barrier, NULL, (unsigned)num_threads);
    }
    memset((void *)&g_contention, 0, sizeof(g_contention));
}

/* Destroy synchronization primitives */
void sync_destroy(void) {
    pthread_mutex_destroy(&g_shared_mutex);
    pthread_rwlock_destroy(&g_shared_rwlock);
    pthread_barrier_destroy(&g_barrier);
}

/* Heavy contention: many threads fighting for one mutex */
__attribute__((noinline))
void sync_contention_heavy(int iterations) {
    for (int i = 0; i < iterations; i++) {
        pthread_mutex_lock(&g_shared_mutex);
        __sync_fetch_and_add(&g_contention.lock_acquisitions, 1);

        /* Do some work while holding the lock */
        g_shared_counter++;
        g_shared_value += 0.001;
        volatile double dummy = g_shared_value * 1.001;
        (void)dummy;

        pthread_mutex_unlock(&g_shared_mutex);
    }
}

/* Light contention: short critical sections */
__attribute__((noinline))
void sync_contention_light(int iterations) {
    for (int i = 0; i < iterations; i++) {
        pthread_mutex_lock(&g_shared_mutex);
        __sync_fetch_and_add(&g_contention.lock_acquisitions, 1);
        g_shared_counter++;
        pthread_mutex_unlock(&g_shared_mutex);

        /* More work outside the lock */
        volatile double x = 0.0;
        for (int j = 0; j < 100; j++) {
            x += (double)j * 0.01;
        }
        (void)x;
    }
}

/* False sharing simulation */
__attribute__((noinline))
void sync_false_sharing_test(false_share_slot_t *slots, int slot_idx, int iterations) {
    for (int i = 0; i < iterations; i++) {
        slots[slot_idx].counter++;
        /* This causes cache line bouncing when adjacent slots are
           updated by different threads */
    }
}

/* Reader-writer lock - read side */
__attribute__((noinline))
void sync_rwlock_read(int iterations) {
    for (int i = 0; i < iterations; i++) {
        pthread_rwlock_rdlock(&g_shared_rwlock);
        volatile double val = g_shared_value;
        (void)val;
        pthread_rwlock_unlock(&g_shared_rwlock);
    }
}

/* Reader-writer lock - write side */
__attribute__((noinline))
void sync_rwlock_write(int iterations) {
    for (int i = 0; i < iterations; i++) {
        pthread_rwlock_wrlock(&g_shared_rwlock);
        g_shared_value += 0.1;
        pthread_rwlock_unlock(&g_shared_rwlock);
    }
}

/* Get contention metrics */
void sync_get_metrics(contention_metrics_t *out) {
    if (out) {
        memcpy(out, (void *)&g_contention, sizeof(contention_metrics_t));
    }
}

/* Reset contention metrics */
void sync_reset_metrics(void) {
    memset((void *)&g_contention, 0, sizeof(g_contention));
}
