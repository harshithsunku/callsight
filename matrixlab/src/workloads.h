#ifndef MATRIXLAB_WORKLOADS_H
#define MATRIXLAB_WORKLOADS_H

#include "threads/thread_pool.h"

/* Workload entry points - one per thread role */
void workload_matrix(thread_info_t *info);
void workload_stats(thread_info_t *info);
void workload_signal(thread_info_t *info);
void workload_sort(thread_info_t *info);
void workload_graph(thread_info_t *info);
void workload_crypto(thread_info_t *info);
void workload_compress(thread_info_t *info);
void workload_memory(thread_info_t *info);
void workload_contention(thread_info_t *info);
void workload_mixed(thread_info_t *info);

/* Global configuration */
extern volatile int g_throttle_us;

/* Phase management */
typedef enum {
    PHASE_HIGH_LOAD = 0,
    PHASE_NORMAL = 1,
    PHASE_LOW_LOAD = 2,
    PHASE_IDLE = 3
} load_phase_t;

extern volatile load_phase_t g_current_phase;

/* Phase controller (runs in main thread) */
void phase_controller_step(void);
const char *phase_name(load_phase_t phase);

#endif
