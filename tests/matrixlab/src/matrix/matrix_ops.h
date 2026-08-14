#ifndef MATRIXLAB_MATRIX_OPS_H
#define MATRIXLAB_MATRIX_OPS_H

#include <stddef.h>

/* Matrix structure (row-major) */
typedef struct {
    double *data;
    int rows;
    int cols;
} matrix_t;

/* Create/destroy matrices */
matrix_t *matrix_create(int rows, int cols);
void matrix_destroy(matrix_t *m);
matrix_t *matrix_clone(const matrix_t *m);

/* Initialize with random values */
void matrix_fill_random(matrix_t *m, double lo, double hi);
void matrix_fill_identity(matrix_t *m);
void matrix_fill_zero(matrix_t *m);

/* Element access */
static inline double matrix_get(const matrix_t *m, int r, int c) {
    return m->data[r * m->cols + c];
}
static inline void matrix_set(matrix_t *m, int r, int c, double val) {
    m->data[r * m->cols + c] = val;
}

/* Basic operations */
void matrix_add(matrix_t *dst, const matrix_t *a, const matrix_t *b);
void matrix_sub(matrix_t *dst, const matrix_t *a, const matrix_t *b);
void matrix_scale(matrix_t *m, double scalar);
void matrix_transpose(matrix_t *dst, const matrix_t *src);

/* Norms */
double matrix_frobenius_norm(const matrix_t *m);
double matrix_max_norm(const matrix_t *m);

/* Element-wise operations */
void matrix_elementwise_mul(matrix_t *dst, const matrix_t *a, const matrix_t *b);

/* Print (small matrix) */
void matrix_print(const matrix_t *m, const char *name);

#endif
