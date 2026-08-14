#ifndef MATRIXLAB_MATRIX_DECOMP_H
#define MATRIXLAB_MATRIX_DECOMP_H

#include "matrix_ops.h"

/* LU decomposition (in-place, with partial pivoting) */
__attribute__((noinline))
int matrix_lu_decompose(matrix_t *a, int *pivot);

/* Solve Ax=b using LU decomposition result */
__attribute__((noinline))
void matrix_lu_solve(const matrix_t *lu, const int *pivot, const double *b, double *x, int n);

/* Cholesky decomposition (lower triangular) */
__attribute__((noinline))
int matrix_cholesky(matrix_t *a);

/* QR decomposition using Gram-Schmidt */
__attribute__((noinline))
void matrix_qr_decompose(const matrix_t *a, matrix_t *q, matrix_t *r);

/* Compute determinant via LU */
__attribute__((noinline))
double matrix_determinant(const matrix_t *m);

/* Matrix inversion via LU */
__attribute__((noinline))
matrix_t *matrix_inverse(const matrix_t *m);

/* Power iteration for dominant eigenvalue */
__attribute__((noinline))
double matrix_power_iteration(const matrix_t *m, double *eigenvector, int max_iter);

/* Generate positive definite matrix (for Cholesky) */
matrix_t *matrix_generate_positive_definite(int n);

#endif
