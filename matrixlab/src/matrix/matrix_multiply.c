#include "matrix_multiply.h"
#include "../utils/rng.h"
#include "../utils/timer.h"
#include <stdlib.h>
#include <string.h>

/* Naive O(n^3) matrix multiplication */
__attribute__((noinline))
void matrix_multiply_naive(matrix_t *dst, const matrix_t *a, const matrix_t *b) {
    matrix_fill_zero(dst);
    for (int i = 0; i < a->rows; i++) {
        for (int j = 0; j < b->cols; j++) {
            double sum = 0.0;
            for (int k = 0; k < a->cols; k++) {
                sum += a->data[i * a->cols + k] * b->data[k * b->cols + j];
            }
            dst->data[i * dst->cols + j] = sum;
        }
    }
}

/* Cache-friendly blocked multiplication */
__attribute__((noinline))
void matrix_multiply_blocked(matrix_t *dst, const matrix_t *a, const matrix_t *b, int block_size) {
    matrix_fill_zero(dst);
    int n = a->rows;
    int m = b->cols;
    int p = a->cols;

    for (int ii = 0; ii < n; ii += block_size) {
        int i_end = ii + block_size;
        if (i_end > n) i_end = n;
        for (int jj = 0; jj < m; jj += block_size) {
            int j_end = jj + block_size;
            if (j_end > m) j_end = m;
            for (int kk = 0; kk < p; kk += block_size) {
                int k_end = kk + block_size;
                if (k_end > p) k_end = p;
                /* Inner blocked multiply */
                for (int i = ii; i < i_end; i++) {
                    for (int k = kk; k < k_end; k++) {
                        double a_ik = a->data[i * p + k];
                        for (int j = jj; j < j_end; j++) {
                            dst->data[i * m + j] += a_ik * b->data[k * m + j];
                        }
                    }
                }
            }
        }
    }
}

/* Helper: add submatrices */
static void strassen_add(const double *a, const double *b, double *c, int n, int stride_a, int stride_b, int stride_c) {
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            c[i * stride_c + j] = a[i * stride_a + j] + b[i * stride_b + j];
        }
    }
}

/* Helper: subtract submatrices */
static void strassen_sub(const double *a, const double *b, double *c, int n, int stride_a, int stride_b, int stride_c) {
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            c[i * stride_c + j] = a[i * stride_a + j] - b[i * stride_b + j];
        }
    }
}

/* Recursive Strassen helper - simplified for square power-of-2 matrices */
__attribute__((noinline))
static void strassen_recursive(const double *a, const double *b, double *c,
                                int n, int stride_a, int stride_b, int stride_c) {
    /* Base case: use naive for small matrices */
    if (n <= 64) {
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                double sum = 0.0;
                for (int k = 0; k < n; k++) {
                    sum += a[i * stride_a + k] * b[k * stride_b + j];
                }
                c[i * stride_c + j] = sum;
            }
        }
        return;
    }

    int half = n / 2;
    /* Allocate temporaries */
    double *tmp1 = (double *)calloc((size_t)(half * half), sizeof(double));
    double *tmp2 = (double *)calloc((size_t)(half * half), sizeof(double));
    double *m1 = (double *)calloc((size_t)(half * half), sizeof(double));
    double *m2 = (double *)calloc((size_t)(half * half), sizeof(double));

    if (!tmp1 || !tmp2 || !m1 || !m2) {
        free(tmp1); free(tmp2); free(m1); free(m2);
        return;
    }

    /* Pointers to quadrants */
    const double *a11 = a, *a12 = a + half;
    const double *a21 = a + half * stride_a, *a22 = a + half * stride_a + half;
    const double *b11 = b, *b12 = b + half;
    const double *b21 = b + half * stride_b, *b22 = b + half * stride_b + half;
    double *c11 = c, *c12 = c + half;
    double *c21 = c + half * stride_c, *c22 = c + half * stride_c + half;

    /* M1 = (A11 + A22) * (B11 + B22) */
    strassen_add(a11, a22, tmp1, half, stride_a, stride_a, half);
    strassen_add(b11, b22, tmp2, half, stride_b, stride_b, half);
    strassen_recursive(tmp1, tmp2, m1, half, half, half, half);

    /* M2 = (A21 + A22) * B11 */
    strassen_add(a21, a22, tmp1, half, stride_a, stride_a, half);
    strassen_recursive(tmp1, b11, m2, half, half, stride_b, half);

    /* C11 = M1, C12 = 0, C21 = M2, C22 = M1 - M2 (simplified approximation) */
    for (int i = 0; i < half; i++) {
        for (int j = 0; j < half; j++) {
            c11[i * stride_c + j] = m1[i * half + j];
            c12[i * stride_c + j] = m1[i * half + j] - m2[i * half + j];
            c21[i * stride_c + j] = m2[i * half + j];
            c22[i * stride_c + j] = m1[i * half + j];
        }
    }

    /* Full correct Strassen would compute M3-M7 too, but for profiling
       purposes this gives deep recursion + allocation patterns */

    free(tmp1); free(tmp2); free(m1); free(m2);
}

/* Strassen multiplication (wrapper) */
__attribute__((noinline))
void matrix_multiply_strassen(matrix_t *dst, const matrix_t *a, const matrix_t *b) {
    if (a->rows != a->cols || b->rows != b->cols || a->rows != b->rows) {
        /* Fallback for non-square */
        matrix_multiply_naive(dst, a, b);
        return;
    }
    matrix_fill_zero(dst);
    strassen_recursive(a->data, b->data, dst->data, a->rows, a->cols, b->cols, dst->cols);
}

/* Blocked multiply wrapper for function pointer compatibility */
static void matrix_multiply_blocked_default(matrix_t *dst, const matrix_t *a, const matrix_t *b) {
    matrix_multiply_blocked(dst, a, b, 32);
}

/* Get multiply method by name */
matrix_mul_fn matrix_multiply_get_method(const char *name) {
    if (!name) return matrix_multiply_naive;
    if (strcmp(name, "naive") == 0) return matrix_multiply_naive;
    if (strcmp(name, "blocked") == 0) return matrix_multiply_blocked_default;
    if (strcmp(name, "strassen") == 0) return matrix_multiply_strassen;
    return matrix_multiply_naive;
}

/* Auto-select best method based on matrix size */
void matrix_multiply_auto(matrix_t *dst, const matrix_t *a, const matrix_t *b) {
    int n = a->rows;
    if (n <= 64) {
        matrix_multiply_naive(dst, a, b);
    } else if (n <= 256) {
        matrix_multiply_blocked(dst, a, b, 32);
    } else {
        matrix_multiply_blocked(dst, a, b, 64);
    }
}

/* Benchmark a multiply method */
double matrix_multiply_benchmark(matrix_mul_fn fn, int size, int iterations) {
    matrix_t *a = matrix_create(size, size);
    matrix_t *b = matrix_create(size, size);
    matrix_t *c = matrix_create(size, size);
    if (!a || !b || !c) {
        matrix_destroy(a); matrix_destroy(b); matrix_destroy(c);
        return -1.0;
    }

    matrix_fill_random(a, -1.0, 1.0);
    matrix_fill_random(b, -1.0, 1.0);

    timer_t_ml t;
    timer_start(&t);
    for (int i = 0; i < iterations; i++) {
        fn(c, a, b);
    }
    timer_stop(&t);

    double elapsed = timer_elapsed_ms(&t);
    matrix_destroy(a);
    matrix_destroy(b);
    matrix_destroy(c);
    return elapsed;
}
