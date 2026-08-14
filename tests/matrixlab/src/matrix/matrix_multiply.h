#ifndef MATRIXLAB_MATRIX_MULTIPLY_H
#define MATRIXLAB_MATRIX_MULTIPLY_H

#include "matrix_ops.h"

/* Naive O(n^3) multiplication */
__attribute__((noinline))
void matrix_multiply_naive(matrix_t *dst, const matrix_t *a, const matrix_t *b);

/* Cache-friendly blocked multiplication */
__attribute__((noinline))
void matrix_multiply_blocked(matrix_t *dst, const matrix_t *a, const matrix_t *b, int block_size);

/* Strassen multiplication (recursive) */
__attribute__((noinline))
void matrix_multiply_strassen(matrix_t *dst, const matrix_t *a, const matrix_t *b);

/* Multiply using function pointer dispatch */
typedef void (*matrix_mul_fn)(matrix_t *, const matrix_t *, const matrix_t *);

/* Get named multiply function */
matrix_mul_fn matrix_multiply_get_method(const char *name);

/* Auto-select best method based on size */
void matrix_multiply_auto(matrix_t *dst, const matrix_t *a, const matrix_t *b);

/* Benchmark a multiply method and return elapsed ms */
double matrix_multiply_benchmark(matrix_mul_fn fn, int size, int iterations);

#endif
