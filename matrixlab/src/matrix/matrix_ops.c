#include "matrix_ops.h"
#include "../utils/rng.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

/* Create a matrix with given dimensions */
matrix_t *matrix_create(int rows, int cols) {
    matrix_t *m = (matrix_t *)malloc(sizeof(matrix_t));
    if (!m) return NULL;
    m->rows = rows;
    m->cols = cols;
    m->data = (double *)calloc((size_t)(rows * cols), sizeof(double));
    if (!m->data) {
        free(m);
        return NULL;
    }
    return m;
}

/* Destroy a matrix */
void matrix_destroy(matrix_t *m) {
    if (!m) return;
    free(m->data);
    free(m);
}

/* Clone a matrix */
matrix_t *matrix_clone(const matrix_t *m) {
    if (!m) return NULL;
    matrix_t *c = matrix_create(m->rows, m->cols);
    if (c) {
        memcpy(c->data, m->data, (size_t)(m->rows * m->cols) * sizeof(double));
    }
    return c;
}

/* Fill matrix with random values in [lo, hi) */
void matrix_fill_random(matrix_t *m, double lo, double hi) {
    if (!m) return;
    int n = m->rows * m->cols;
    for (int i = 0; i < n; i++) {
        m->data[i] = rng_next_range(lo, hi);
    }
}

/* Fill with identity */
void matrix_fill_identity(matrix_t *m) {
    if (!m) return;
    memset(m->data, 0, (size_t)(m->rows * m->cols) * sizeof(double));
    int mn = m->rows < m->cols ? m->rows : m->cols;
    for (int i = 0; i < mn; i++) {
        m->data[i * m->cols + i] = 1.0;
    }
}

/* Fill with zeros */
void matrix_fill_zero(matrix_t *m) {
    if (!m) return;
    memset(m->data, 0, (size_t)(m->rows * m->cols) * sizeof(double));
}

/* Matrix addition: dst = a + b */
void matrix_add(matrix_t *dst, const matrix_t *a, const matrix_t *b) {
    int n = a->rows * a->cols;
    for (int i = 0; i < n; i++) {
        dst->data[i] = a->data[i] + b->data[i];
    }
}

/* Matrix subtraction: dst = a - b */
void matrix_sub(matrix_t *dst, const matrix_t *a, const matrix_t *b) {
    int n = a->rows * a->cols;
    for (int i = 0; i < n; i++) {
        dst->data[i] = a->data[i] - b->data[i];
    }
}

/* Scale all elements by scalar */
void matrix_scale(matrix_t *m, double scalar) {
    int n = m->rows * m->cols;
    for (int i = 0; i < n; i++) {
        m->data[i] *= scalar;
    }
}

/* Transpose: dst = src^T */
void matrix_transpose(matrix_t *dst, const matrix_t *src) {
    for (int i = 0; i < src->rows; i++) {
        for (int j = 0; j < src->cols; j++) {
            dst->data[j * dst->cols + i] = src->data[i * src->cols + j];
        }
    }
}

/* Frobenius norm */
double matrix_frobenius_norm(const matrix_t *m) {
    double sum = 0.0;
    int n = m->rows * m->cols;
    for (int i = 0; i < n; i++) {
        sum += m->data[i] * m->data[i];
    }
    return sqrt(sum);
}

/* Max-absolute-value norm */
double matrix_max_norm(const matrix_t *m) {
    double maxv = 0.0;
    int n = m->rows * m->cols;
    for (int i = 0; i < n; i++) {
        double v = fabs(m->data[i]);
        if (v > maxv) maxv = v;
    }
    return maxv;
}

/* Element-wise multiplication: dst = a .* b */
void matrix_elementwise_mul(matrix_t *dst, const matrix_t *a, const matrix_t *b) {
    int n = a->rows * a->cols;
    for (int i = 0; i < n; i++) {
        dst->data[i] = a->data[i] * b->data[i];
    }
}

/* Print a small matrix */
void matrix_print(const matrix_t *m, const char *name) {
    if (!m) return;
    printf("Matrix %s (%dx%d):\n", name ? name : "?", m->rows, m->cols);
    int max_r = m->rows < 8 ? m->rows : 8;
    int max_c = m->cols < 8 ? m->cols : 8;
    for (int i = 0; i < max_r; i++) {
        for (int j = 0; j < max_c; j++) {
            printf("%10.4f ", matrix_get(m, i, j));
        }
        if (max_c < m->cols) printf("...");
        printf("\n");
    }
    if (max_r < m->rows) printf("  ...\n");
}
