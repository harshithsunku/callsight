#include "workloads.h"

#include "core/logging.h"
#include "core/memory_pool.h"
#include "core/arena.h"
#include "core/errors.h"

#include "matrix/matrix_ops.h"
#include "matrix/matrix_multiply.h"
#include "matrix/matrix_decomp.h"

#include "stats/statistics.h"
#include "stats/monte_carlo.h"
#include "stats/regression.h"

#include "signal/fft.h"
#include "signal/filters.h"
#include "signal/convolution.h"

#include "crypto/sha256.h"
#include "crypto/md5.h"
#include "crypto/crc32.h"
#include "crypto/hmac.h"

#include "sort/quicksort.h"
#include "sort/mergesort.h"
#include "sort/heapsort.h"
#include "sort/radixsort.h"

#include "graph/graph.h"
#include "graph/bfs.h"
#include "graph/dfs.h"
#include "graph/dijkstra.h"

#include "compress/rle.h"
#include "compress/huffman.h"

#include "threads/sync.h"

#include "utils/rng.h"
#include "utils/timer.h"
#include "utils/helpers.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

/* Global configuration */
volatile int g_throttle_us = 1000;
volatile load_phase_t g_current_phase = PHASE_NORMAL;

/* Phase timing */
static int phase_counter = 0;

/* Phase controller - cycles through load phases */
void phase_controller_step(void) {
    phase_counter++;
    /* Change phase every ~10 seconds at 1Hz tick rate */
    if (phase_counter % 10 == 0) {
        int r = rng_next_int(0, 100);
        if (r < 30) g_current_phase = PHASE_HIGH_LOAD;
        else if (r < 65) g_current_phase = PHASE_NORMAL;
        else if (r < 85) g_current_phase = PHASE_LOW_LOAD;
        else g_current_phase = PHASE_IDLE;
    }
}

/* Get phase name */
const char *phase_name(load_phase_t phase) {
    switch (phase) {
        case PHASE_HIGH_LOAD: return "high-load";
        case PHASE_NORMAL:    return "normal";
        case PHASE_LOW_LOAD:  return "low-load";
        case PHASE_IDLE:      return "idle";
    }
    return "unknown";
}

/* Adaptive sleep based on current phase and throttle */
static void workload_sleep(void) {
    timer_adaptive_sleep((int)g_current_phase, g_throttle_us);
}

/* ============================================================
 * MATRIX WORKLOAD - CPU-heavy matrix operations
 * ============================================================ */
__attribute__((noinline))
void workload_matrix(thread_info_t *info) {
    int phase = (int)g_current_phase;

    /* Vary matrix size by phase */
    int sizes[] = {128, 96, 64, 32};
    int size = sizes[phase];

    /* Occasionally do larger matrices */
    if (rng_next_int(0, 10) == 0) size *= 2;

    matrix_t *a = matrix_create(size, size);
    matrix_t *b = matrix_create(size, size);
    matrix_t *c = matrix_create(size, size);
    if (!a || !b || !c) {
        matrix_destroy(a); matrix_destroy(b); matrix_destroy(c);
        workload_sleep();
        return;
    }

    matrix_fill_random(a, -10.0, 10.0);
    matrix_fill_random(b, -10.0, 10.0);

    /* Switch algorithms based on iteration */
    int algo = (int)(info->iterations % 4);
    switch (algo) {
        case 0:
            matrix_multiply_naive(c, a, b);
            break;
        case 1:
            matrix_multiply_blocked(c, a, b, 32);
            break;
        case 2:
            matrix_multiply_auto(c, a, b);
            break;
        case 3: {
            /* Decomposition work */
            matrix_t *pd = matrix_generate_positive_definite(size / 2);
            if (pd) {
                matrix_cholesky(pd);
                matrix_destroy(pd);
            }
            matrix_multiply_naive(c, a, b);
            break;
        }
    }

    double norm = matrix_frobenius_norm(c);

    /* Occasional extra work */
    if (info->iterations % 20 == 0) {
        matrix_t *inv = matrix_inverse(a);
        matrix_destroy(inv);
    }

    /* Periodic log */
    if (info->iterations % 50 == 0) {
        LOG_INFO("iteration=%lu latency=%.0fms norm=%.2f size=%d algo=%d",
                 (unsigned long)info->iterations, info->last_latency_ms, norm, size, algo);
    }

    matrix_destroy(a);
    matrix_destroy(b);
    matrix_destroy(c);

    workload_sleep();
}

/* ============================================================
 * STATS WORKLOAD - Monte Carlo + regression
 * ============================================================ */
__attribute__((noinline))
void workload_stats(thread_info_t *info) {
    int phase = (int)g_current_phase;
    int sample_counts[] = {50000, 20000, 10000, 5000};
    size_t samples = (size_t)sample_counts[phase];

    int algo = (int)(info->iterations % 5);

    switch (algo) {
        case 0: {
            /* Monte Carlo pi estimation */
            mc_result_t res = monte_carlo_pi(samples);
            if (info->iterations % 30 == 0) {
                LOG_INFO("iteration=%lu pi=%.6f error=%.6f samples=%zu",
                         (unsigned long)info->iterations, res.estimate, res.error, samples);
            }
            break;
        }
        case 1: {
            /* Linear regression */
            size_t n = samples / 10;
            double *x = (double *)malloc(n * sizeof(double));
            double *y = (double *)malloc(n * sizeof(double));
            if (x && y) {
                for (size_t i = 0; i < n; i++) {
                    x[i] = rng_next_range(-10.0, 10.0);
                    y[i] = 2.5 * x[i] + 1.3 + rng_next_gaussian(0.0, 0.5);
                }
                regression_result_t res = regression_linear(x, y, n);
                if (info->iterations % 30 == 0) {
                    LOG_INFO("iteration=%lu slope=%.4f r2=%.4f n=%zu",
                             (unsigned long)info->iterations, res.slope, res.r_squared, n);
                }
            }
            free(x); free(y);
            break;
        }
        case 2: {
            /* Running statistics */
            running_stats_t rs;
            stats_running_init(&rs);
            for (size_t i = 0; i < samples; i++) {
                stats_running_push(&rs, rng_next_gaussian(100.0, 15.0));
            }
            if (info->iterations % 30 == 0) {
                LOG_INFO("iteration=%lu mean=%.2f var=%.2f samples=%zu",
                         (unsigned long)info->iterations, stats_running_mean(&rs),
                         stats_running_variance(&rs), samples);
            }
            break;
        }
        case 3: {
            /* Option pricing MC */
            mc_result_t res = monte_carlo_option_price(100.0, 110.0, 0.05, 0.2, 1.0, samples);
            if (info->iterations % 30 == 0) {
                LOG_INFO("iteration=%lu option_price=%.4f error=%.4f",
                         (unsigned long)info->iterations, res.estimate, res.error);
            }
            break;
        }
        case 4: {
            /* Polynomial regression */
            size_t n = 500;
            double *x = (double *)malloc(n * sizeof(double));
            double *y = (double *)malloc(n * sizeof(double));
            double coeffs[4] = {0};
            if (x && y) {
                for (size_t i = 0; i < n; i++) {
                    x[i] = rng_next_range(-5.0, 5.0);
                    y[i] = 0.5 * x[i] * x[i] + 2.0 * x[i] + 1.0 + rng_next_gaussian(0.0, 1.0);
                }
                regression_polynomial(x, y, n, 3, coeffs);
            }
            free(x); free(y);
            break;
        }
    }

    workload_sleep();
}

/* ============================================================
 * SIGNAL WORKLOAD - FFT, filtering, convolution
 * ============================================================ */
__attribute__((noinline))
void workload_signal(thread_info_t *info) {
    int phase = (int)g_current_phase;
    size_t fft_sizes[] = {4096, 2048, 1024, 512};
    size_t n = fft_sizes[phase];

    int algo = (int)(info->iterations % 4);

    switch (algo) {
        case 0: {
            /* Iterative FFT */
            complex_t *data = (complex_t *)malloc(n * sizeof(complex_t));
            double *power = (double *)malloc(n * sizeof(double));
            if (data && power) {
                double freqs[] = {10.0, 50.0, 120.0};
                fft_generate_signal(data, n, freqs, 3);
                fft_transform(data, n, 0);
                fft_power_spectrum(data, power, n);
                fft_transform(data, n, 1); /* inverse */
            }
            free(data); free(power);
            break;
        }
        case 1: {
            /* Recursive FFT (deep stacks) */
            complex_t *data = (complex_t *)malloc(n * sizeof(complex_t));
            if (data) {
                double freqs[] = {5.0, 25.0, 80.0, 200.0};
                fft_generate_signal(data, n, freqs, 4);
                fft_recursive(data, n, 0);
            }
            free(data);
            break;
        }
        case 2: {
            /* FIR filtering */
            double *input = (double *)malloc(n * sizeof(double));
            double *output = (double *)malloc(n * sizeof(double));
            int ntaps = 64;
            double *coeffs = (double *)malloc((size_t)ntaps * sizeof(double));
            if (input && output && coeffs) {
                filter_generate_noisy(input, n, 10.0, 0.5);
                filter_design_lowpass(coeffs, ntaps, 0.1);
                filter_fir(input, output, n, coeffs, ntaps);
            }
            free(input); free(output); free(coeffs);
            break;
        }
        case 3: {
            /* 2D convolution */
            int rows = 64, cols = 64;
            size_t sz = (size_t)(rows * cols);
            double *input = (double *)malloc(sz * sizeof(double));
            double *output = (double *)malloc(sz * sizeof(double));
            double kernel[25];
            if (input && output) {
                for (size_t i = 0; i < sz; i++) input[i] = rng_next_double();
                conv_kernel_gaussian(kernel, 5, 1.0);
                conv_2d(input, rows, cols, kernel, 5, output);
            }
            free(input); free(output);
            break;
        }
    }

    if (info->iterations % 40 == 0) {
        LOG_INFO("iteration=%lu fft_size=%zu algo=%d phase=%s",
                 (unsigned long)info->iterations, n, algo, phase_name(g_current_phase));
    }

    workload_sleep();
}

/* ============================================================
 * SORT WORKLOAD - Various sorting algorithms
 * ============================================================ */
__attribute__((noinline))
void workload_sort(thread_info_t *info) {
    int phase = (int)g_current_phase;
    size_t sizes[] = {100000, 50000, 20000, 5000};
    size_t n = sizes[phase];

    double *arr = (double *)malloc(n * sizeof(double));
    if (!arr) { workload_sleep(); return; }

    /* Generate data */
    for (size_t i = 0; i < n; i++) {
        arr[i] = rng_next_range(-1000.0, 1000.0);
    }

    int algo = (int)(info->iterations % 7);
    const char *algo_name = "unknown";

    switch (algo) {
        case 0: quicksort(arr, n); algo_name = "quicksort"; break;
        case 1: quicksort_3way(arr, n); algo_name = "quicksort-3way"; break;
        case 2: quicksort_hybrid(arr, n); algo_name = "quicksort-hybrid"; break;
        case 3: mergesort_topdown(arr, n); algo_name = "mergesort-td"; break;
        case 4: mergesort_bottomup(arr, n); algo_name = "mergesort-bu"; break;
        case 5: heapsort_sort(arr, n); algo_name = "heapsort"; break;
        case 6: shellsort(arr, n); algo_name = "shellsort"; break;
    }

    if (info->iterations % 30 == 0) {
        int sorted = sort_is_sorted(arr, n);
        LOG_INFO("iteration=%lu algo=%s n=%zu sorted=%s",
                 (unsigned long)info->iterations, algo_name, n,
                 sorted ? "yes" : "NO");
    }

    free(arr);
    workload_sleep();
}

/* ============================================================
 * GRAPH WORKLOAD - Pointer-heavy traversals
 * ============================================================ */
__attribute__((noinline))
void workload_graph(thread_info_t *info) {
    int phase = (int)g_current_phase;
    int vertex_counts[] = {500, 300, 200, 100};
    int verts = vertex_counts[phase];
    int edges = verts * 3;

    graph_t *g = graph_generate_random(verts, edges, 100.0);
    if (!g) { workload_sleep(); return; }

    int *result = (int *)malloc((size_t)verts * sizeof(int));
    double *dists = (double *)malloc((size_t)verts * sizeof(double));
    if (!result || !dists) {
        free(result); free(dists); graph_destroy(g); workload_sleep(); return;
    }

    int algo = (int)(info->iterations % 5);

    switch (algo) {
        case 0: {
            /* BFS traversal */
            int visited = bfs_traverse(g, 0, result);
            if (info->iterations % 25 == 0) {
                LOG_INFO("iteration=%lu bfs visited=%d/%d",
                         (unsigned long)info->iterations, visited, verts);
            }
            break;
        }
        case 1: {
            /* DFS traversal (deep recursion) */
            int visited = dfs_traverse(g, 0, result);
            if (info->iterations % 25 == 0) {
                LOG_INFO("iteration=%lu dfs visited=%d/%d",
                         (unsigned long)info->iterations, visited, verts);
            }
            break;
        }
        case 2: {
            /* Dijkstra shortest path */
            dijkstra_shortest_path(g, 0, dists, result);
            break;
        }
        case 3: {
            /* Connected components */
            int nc = dfs_connected_components(g, result);
            if (info->iterations % 25 == 0) {
                LOG_INFO("iteration=%lu components=%d verts=%d",
                         (unsigned long)info->iterations, nc, verts);
            }
            break;
        }
        case 4: {
            /* BFS distances */
            bfs_distances(g, 0, result);
            break;
        }
    }

    free(result);
    free(dists);
    graph_destroy(g);

    workload_sleep();
}

/* ============================================================
 * CRYPTO WORKLOAD - Hash computations
 * ============================================================ */
__attribute__((noinline))
void workload_crypto(thread_info_t *info) {
    int phase = (int)g_current_phase;
    int buf_sizes[] = {8192, 4096, 2048, 1024};
    int buf_size = buf_sizes[phase];
    int iterations[] = {200, 100, 50, 20};
    int iters = iterations[phase];

    uint8_t *buf = (uint8_t *)malloc((size_t)buf_size);
    if (!buf) { workload_sleep(); return; }
    rng_fill_bytes(buf, (size_t)buf_size);

    int algo = (int)(info->iterations % 5);
    uint8_t digest[32];

    switch (algo) {
        case 0:
            sha256_stress(buf, (size_t)buf_size, iters, digest);
            break;
        case 1: {
            uint8_t md5_digest[16];
            md5_stress(buf, (size_t)buf_size, iters, md5_digest);
            break;
        }
        case 2: {
            uint32_t crc = crc32_stress(buf, (size_t)buf_size, iters);
            (void)crc;
            break;
        }
        case 3:
            hmac_stress(buf, (size_t)buf_size, iters / 10 + 1);
            break;
        case 4: {
            /* PBKDF2 (expensive) */
            uint8_t derived[32];
            pbkdf2_sha256("password", 8, "salt", 4, iters / 5 + 1, derived, 32);
            break;
        }
    }

    if (info->iterations % 40 == 0) {
        char hex[65];
        sha256_to_hex(digest, hex);
        LOG_INFO("iteration=%lu hash=%.16s... buf=%dB iters=%d",
                 (unsigned long)info->iterations, hex, buf_size, iters);
    }

    free(buf);
    workload_sleep();
}

/* ============================================================
 * COMPRESS WORKLOAD - RLE and Huffman
 * ============================================================ */
__attribute__((noinline))
void workload_compress(thread_info_t *info) {
    int phase = (int)g_current_phase;
    int data_sizes[] = {16384, 8192, 4096, 2048};
    int data_size = data_sizes[phase];

    int algo = (int)(info->iterations % 3);

    switch (algo) {
        case 0:
            rle_stress_test(5, data_size);
            break;
        case 1:
            huffman_stress_test(3, data_size);
            break;
        case 2: {
            /* Bit packing + RLE combo */
            uint8_t *data = (uint8_t *)malloc((size_t)data_size);
            uint8_t *packed = (uint8_t *)malloc((size_t)data_size);
            uint8_t *encoded = (uint8_t *)malloc((size_t)data_size * 2);
            if (data && packed && encoded) {
                for (int i = 0; i < data_size; i++) {
                    data[i] = rng_next_u32() % 2;
                }
                size_t pack_size = rle_pack_bits(data, (size_t)data_size, packed);
                rle_encode(packed, pack_size, encoded, (size_t)data_size * 2);
            }
            free(data); free(packed); free(encoded);
            break;
        }
    }

    if (info->iterations % 30 == 0) {
        LOG_INFO("iteration=%lu compress algo=%d size=%d",
                 (unsigned long)info->iterations, algo, data_size);
    }

    workload_sleep();
}

/* ============================================================
 * MEMORY STRESS WORKLOAD - Allocation churn
 * ============================================================ */
__attribute__((noinline))
void workload_memory(thread_info_t *info) {
    int phase = (int)g_current_phase;

    int algo = (int)(info->iterations % 3);

    switch (algo) {
        case 0: {
            /* Pool stress */
            int pool_sizes[] = {1000, 500, 200, 100};
            mem_pool_t *pool = mempool_create(256, (size_t)pool_sizes[phase]);
            if (pool) {
                mempool_stress_test(pool, pool_sizes[phase] / 2);
                mempool_defrag_simulate(pool);
                if (info->iterations % 30 == 0) {
                    LOG_INFO("iteration=%lu pool used=%zu free=%zu total=%zu",
                             (unsigned long)info->iterations,
                             mempool_used_blocks(pool),
                             mempool_free_blocks(pool),
                             mempool_total_blocks(pool));
                }
                mempool_destroy(pool);
            }
            break;
        }
        case 1: {
            /* Arena stress */
            arena_t *arena = arena_create(65536);
            if (arena) {
                int alloc_counts[] = {2000, 1000, 500, 200};
                arena_stress_varied(arena, alloc_counts[phase]);
                if (info->iterations % 30 == 0) {
                    LOG_INFO("iteration=%lu arena used=%zu cap=%zu peak=%zu",
                             (unsigned long)info->iterations,
                             arena_used(arena),
                             arena_capacity(arena),
                             arena_peak(arena));
                }
                arena_destroy(arena);
            }
            break;
        }
        case 2: {
            /* malloc/free churn with mixed sizes */
            int nallocs[] = {500, 200, 100, 50};
            int n = nallocs[phase];
            void **ptrs = (void **)calloc((size_t)n, sizeof(void *));
            if (ptrs) {
                static const size_t alloc_sizes[] = {16, 64, 256, 1024, 4096, 16384};
                int nsizes = (int)(sizeof(alloc_sizes) / sizeof(alloc_sizes[0]));
                for (int i = 0; i < n; i++) {
                    size_t sz = alloc_sizes[rng_next_u32() % (uint32_t)nsizes];
                    ptrs[i] = malloc(sz);
                    if (ptrs[i]) memset(ptrs[i], (int)(i & 0xFF), sz);
                }
                /* Free in random order */
                rng_shuffle_ptrs(ptrs, (size_t)n);
                for (int i = 0; i < n; i++) {
                    free(ptrs[i]);
                }
                free(ptrs);
            }
            break;
        }
    }

    workload_sleep();
}

/* ============================================================
 * CONTENTION WORKLOAD - Lock contention + false sharing
 * ============================================================ */

/* Global false sharing slots */
static false_share_slot_t g_false_share_slots[32];

__attribute__((noinline))
void workload_contention(thread_info_t *info) {
    int phase = (int)g_current_phase;
    int iter_counts[] = {500, 200, 100, 50};
    int iters = iter_counts[phase];

    int algo = (int)(info->iterations % 4);

    switch (algo) {
        case 0:
            /* Heavy lock contention */
            sync_contention_heavy(iters);
            break;
        case 1:
            /* Light contention */
            sync_contention_light(iters);
            break;
        case 2:
            /* False sharing */
            sync_false_sharing_test(g_false_share_slots, info->id % 32, iters * 100);
            break;
        case 3:
            /* Reader-writer lock */
            if (info->id % 3 == 0) {
                sync_rwlock_write(iters);
            } else {
                sync_rwlock_read(iters * 5);
            }
            break;
    }

    if (info->iterations % 30 == 0) {
        contention_metrics_t m;
        sync_get_metrics(&m);
        LOG_INFO("iteration=%lu contention locks=%lu contentions=%lu algo=%d",
                 (unsigned long)info->iterations,
                 (unsigned long)m.lock_acquisitions,
                 (unsigned long)m.lock_contentions, algo);
    }

    workload_sleep();
}

/* ============================================================
 * MIXED WORKLOAD - Combination of everything
 * ============================================================ */

/* Deep call chain helper functions */
__attribute__((noinline)) static double deep_compute_6(double x) { return x * 1.001 + 0.001; }
__attribute__((noinline)) static double deep_compute_5(double x) { return deep_compute_6(x) * 1.01; }
__attribute__((noinline)) static double deep_compute_4(double x) { return deep_compute_5(x) + 0.1; }
__attribute__((noinline)) static double deep_compute_3(double x) { return deep_compute_4(x) * 0.99; }
__attribute__((noinline)) static double deep_compute_2(double x) { return deep_compute_3(x) + 0.5; }
__attribute__((noinline)) static double deep_compute_1(double x) { return deep_compute_2(x) * 1.1; }

/* Macro-generated work functions */
#define DEFINE_WORK_FUNC(name, op) \
    __attribute__((noinline)) \
    static double work_##name(double *data, size_t n) { \
        double result = 0.0; \
        for (size_t i = 0; i < n; i++) result = op; \
        return result; \
    }

DEFINE_WORK_FUNC(sum, result + data[i])
DEFINE_WORK_FUNC(product, result * data[i] + 1e-10)
DEFINE_WORK_FUNC(xor_hash, (double)((uint64_t)result ^ (uint64_t)(data[i] * 1e6)))

/* Function pointer table for dynamic dispatch */
typedef double (*work_func_t)(double *, size_t);
static work_func_t work_table[] = {work_sum, work_product, work_xor_hash};

__attribute__((noinline))
void workload_mixed(thread_info_t *info) {
    int phase = (int)g_current_phase;

    /* Pick a sub-workload based on iteration */
    int sub = (int)(info->iterations % 6);

    switch (sub) {
        case 0: {
            /* Deep call chain */
            double result = 1.0;
            for (int i = 0; i < 1000; i++) {
                result = deep_compute_1(result);
            }
            (void)result;
            break;
        }
        case 1: {
            /* Function pointer dispatch */
            size_t n = 10000;
            double *data = (double *)malloc(n * sizeof(double));
            if (data) {
                for (size_t i = 0; i < n; i++) data[i] = rng_next_double();
                int fn_idx = rng_next_int(0, 3);
                double result = work_table[fn_idx](data, n);
                (void)result;
                free(data);
            }
            break;
        }
        case 2: {
            /* Bit manipulation work */
            uint32_t val = rng_next_u32();
            volatile uint32_t result = 0;
            for (int i = 0; i < 10000; i++) {
                result += helpers_popcount(val);
                val = helpers_rotl32(val, 7) ^ (uint32_t)i;
                result += helpers_clz(val);
            }
            (void)result;
            break;
        }
        case 3: {
            /* Small matrix + small sort */
            int sz = 32;
            matrix_t *m = matrix_create(sz, sz);
            if (m) {
                matrix_fill_random(m, -1.0, 1.0);
                double norm = matrix_frobenius_norm(m);
                (void)norm;
                matrix_destroy(m);
            }
            double arr[256];
            for (int i = 0; i < 256; i++) arr[i] = rng_next_double();
            quicksort(arr, 256);
            break;
        }
        case 4: {
            /* CRC + hash combo */
            uint8_t buf[4096];
            rng_fill_bytes(buf, sizeof(buf));
            uint32_t crc = crc32_compute(buf, sizeof(buf));
            uint8_t sha[32];
            sha256_hash(&crc, 4, sha);
            (void)sha;
            break;
        }
        case 5: {
            /* Cache-unfriendly strided access */
            size_t sz = 256 * 1024; /* 256KB - exceeds L1 */
            double *arr = (double *)malloc(sz * sizeof(double));
            if (arr) {
                /* Initialize */
                for (size_t i = 0; i < sz; i++) arr[i] = (double)i;
                /* Strided access */
                double sum = 0.0;
                int stride_sizes[] = {64, 32, 16, 8};
                int stride = stride_sizes[phase];
                for (size_t i = 0; i < sz; i += (size_t)stride) {
                    sum += arr[i];
                }
                (void)sum;
                free(arr);
            }
            break;
        }
    }

    if (info->iterations % 30 == 0) {
        LOG_INFO("iteration=%lu mixed sub=%d phase=%s",
                 (unsigned long)info->iterations, sub, phase_name(g_current_phase));
    }

    workload_sleep();
}
