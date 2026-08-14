#include "matrix_decomp.h"
#include "matrix_multiply.h"
#include "../utils/rng.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* LU decomposition with partial pivoting */
__attribute__((noinline))
int matrix_lu_decompose(matrix_t *a, int *pivot) {
    int n = a->rows;
    for (int i = 0; i < n; i++) pivot[i] = i;

    for (int k = 0; k < n; k++) {
        /* Find pivot */
        double max_val = 0.0;
        int max_row = k;
        for (int i = k; i < n; i++) {
            double v = fabs(a->data[i * n + k]);
            if (v > max_val) { max_val = v; max_row = i; }
        }

        if (max_val < 1e-12) return -1; /* Singular */

        /* Swap rows */
        if (max_row != k) {
            int tmp = pivot[k]; pivot[k] = pivot[max_row]; pivot[max_row] = tmp;
            for (int j = 0; j < n; j++) {
                double t = a->data[k * n + j];
                a->data[k * n + j] = a->data[max_row * n + j];
                a->data[max_row * n + j] = t;
            }
        }

        /* Eliminate below */
        for (int i = k + 1; i < n; i++) {
            a->data[i * n + k] /= a->data[k * n + k];
            for (int j = k + 1; j < n; j++) {
                a->data[i * n + j] -= a->data[i * n + k] * a->data[k * n + j];
            }
        }
    }
    return 0;
}

/* Forward/backward substitution for LU solve */
__attribute__((noinline))
void matrix_lu_solve(const matrix_t *lu, const int *pivot, const double *b, double *x, int n) {
    /* Apply permutation and forward substitution */
    double *y = (double *)malloc((size_t)n * sizeof(double));
    if (!y) return;

    for (int i = 0; i < n; i++) {
        y[i] = b[pivot[i]];
        for (int j = 0; j < i; j++) {
            y[i] -= lu->data[i * n + j] * y[j];
        }
    }

    /* Backward substitution */
    for (int i = n - 1; i >= 0; i--) {
        x[i] = y[i];
        for (int j = i + 1; j < n; j++) {
            x[i] -= lu->data[i * n + j] * x[j];
        }
        x[i] /= lu->data[i * n + i];
    }

    free(y);
}

/* Cholesky decomposition (lower triangle) */
__attribute__((noinline))
int matrix_cholesky(matrix_t *a) {
    int n = a->rows;
    for (int j = 0; j < n; j++) {
        double sum = 0.0;
        for (int k = 0; k < j; k++) {
            sum += a->data[j * n + k] * a->data[j * n + k];
        }

        double diag = a->data[j * n + j] - sum;
        if (diag <= 0.0) return -1; /* Not positive definite */
        a->data[j * n + j] = sqrt(diag);

        for (int i = j + 1; i < n; i++) {
            sum = 0.0;
            for (int k = 0; k < j; k++) {
                sum += a->data[i * n + k] * a->data[j * n + k];
            }
            a->data[i * n + j] = (a->data[i * n + j] - sum) / a->data[j * n + j];
        }

        /* Zero upper triangle */
        for (int i = 0; i < j; i++) {
            a->data[i * n + j] = 0.0;
        }
    }
    return 0;
}

/* In-place vector normalization */
static inline void vec_normalize(double *v, int n) {
    double norm = 0.0;
    for (int i = 0; i < n; i++) norm += v[i] * v[i];
    norm = sqrt(norm);
    if (norm > 1e-12) {
        for (int i = 0; i < n; i++) v[i] /= norm;
    }
}

/* Inner product */
static inline double vec_dot(const double *a, const double *b, int n) {
    double sum = 0.0;
    for (int i = 0; i < n; i++) sum += a[i] * b[i];
    return sum;
}

/* QR decomposition using modified Gram-Schmidt */
__attribute__((noinline))
void matrix_qr_decompose(const matrix_t *a, matrix_t *q, matrix_t *r) {
    int m = a->rows;
    int n = a->cols;

    matrix_fill_zero(r);

    /* Copy A columns into Q */
    for (int i = 0; i < m; i++) {
        for (int j = 0; j < n; j++) {
            matrix_set(q, i, j, matrix_get(a, i, j));
        }
    }

    for (int j = 0; j < n; j++) {
        /* Get column j of Q */
        double *col_j = (double *)malloc((size_t)m * sizeof(double));
        if (!col_j) return;
        for (int i = 0; i < m; i++) col_j[i] = matrix_get(q, i, j);

        /* Orthogonalize against previous columns */
        for (int k = 0; k < j; k++) {
            double *col_k = (double *)malloc((size_t)m * sizeof(double));
            if (!col_k) { free(col_j); return; }
            for (int i = 0; i < m; i++) col_k[i] = matrix_get(q, i, k);

            double r_kj = vec_dot(col_k, col_j, m);
            matrix_set(r, k, j, r_kj);

            for (int i = 0; i < m; i++) {
                col_j[i] -= r_kj * col_k[i];
            }
            free(col_k);
        }

        /* Normalize */
        double norm = sqrt(vec_dot(col_j, col_j, m));
        matrix_set(r, j, j, norm);
        if (norm > 1e-12) {
            for (int i = 0; i < m; i++) {
                matrix_set(q, i, j, col_j[i] / norm);
            }
        }
        free(col_j);
    }
}

/* Determinant via LU decomposition */
__attribute__((noinline))
double matrix_determinant(const matrix_t *m) {
    if (m->rows != m->cols) return 0.0;
    int n = m->rows;

    matrix_t *lu = matrix_clone(m);
    int *pivot = (int *)malloc((size_t)n * sizeof(int));
    if (!lu || !pivot) {
        matrix_destroy(lu);
        free(pivot);
        return 0.0;
    }

    int ret = matrix_lu_decompose(lu, pivot);
    if (ret != 0) {
        matrix_destroy(lu);
        free(pivot);
        return 0.0;
    }

    double det = 1.0;
    int swaps = 0;
    for (int i = 0; i < n; i++) {
        det *= lu->data[i * n + i];
        if (pivot[i] != i) swaps++;
    }

    matrix_destroy(lu);
    free(pivot);
    return (swaps % 2 == 0) ? det : -det;
}

/* Matrix inverse via LU decomposition */
__attribute__((noinline))
matrix_t *matrix_inverse(const matrix_t *m) {
    if (m->rows != m->cols) return NULL;
    int n = m->rows;

    matrix_t *lu = matrix_clone(m);
    int *pivot = (int *)malloc((size_t)n * sizeof(int));
    matrix_t *inv = matrix_create(n, n);
    double *col = (double *)malloc((size_t)n * sizeof(double));
    double *e = (double *)calloc((size_t)n, sizeof(double));

    if (!lu || !pivot || !inv || !col || !e) goto fail;

    if (matrix_lu_decompose(lu, pivot) != 0) goto fail;

    for (int j = 0; j < n; j++) {
        memset(e, 0, (size_t)n * sizeof(double));
        e[j] = 1.0;
        matrix_lu_solve(lu, pivot, e, col, n);
        for (int i = 0; i < n; i++) {
            inv->data[i * n + j] = col[i];
        }
    }

    matrix_destroy(lu);
    free(pivot); free(col); free(e);
    return inv;

fail:
    matrix_destroy(lu);
    matrix_destroy(inv);
    free(pivot); free(col); free(e);
    return NULL;
}

/* Power iteration for dominant eigenvalue */
__attribute__((noinline))
double matrix_power_iteration(const matrix_t *m, double *eigenvector, int max_iter) {
    int n = m->rows;
    double *v = (double *)malloc((size_t)n * sizeof(double));
    double *w = (double *)malloc((size_t)n * sizeof(double));
    if (!v || !w) { free(v); free(w); return 0.0; }

    /* Initialize with random vector */
    for (int i = 0; i < n; i++) v[i] = rng_next_double();
    vec_normalize(v, n);

    double eigenvalue = 0.0;
    for (int iter = 0; iter < max_iter; iter++) {
        /* w = A * v */
        for (int i = 0; i < n; i++) {
            w[i] = 0.0;
            for (int j = 0; j < n; j++) {
                w[i] += m->data[i * n + j] * v[j];
            }
        }

        /* Eigenvalue estimate = v^T * w */
        eigenvalue = vec_dot(v, w, n);

        /* Normalize w -> v */
        vec_normalize(w, n);
        memcpy(v, w, (size_t)n * sizeof(double));
    }

    if (eigenvector) {
        memcpy(eigenvector, v, (size_t)n * sizeof(double));
    }

    free(v); free(w);
    return eigenvalue;
}

/* Generate a positive-definite matrix: A = R^T * R + I */
matrix_t *matrix_generate_positive_definite(int n) {
    matrix_t *r = matrix_create(n, n);
    matrix_t *rt = matrix_create(n, n);
    matrix_t *result = matrix_create(n, n);
    if (!r || !rt || !result) {
        matrix_destroy(r); matrix_destroy(rt); matrix_destroy(result);
        return NULL;
    }

    matrix_fill_random(r, -1.0, 1.0);
    matrix_transpose(rt, r);
    matrix_multiply_naive(result, rt, r);

    /* Add diagonal to ensure positive definiteness */
    for (int i = 0; i < n; i++) {
        result->data[i * n + i] += (double)n;
    }

    matrix_destroy(r);
    matrix_destroy(rt);
    return result;
}
