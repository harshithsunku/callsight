#ifndef MATRIXLAB_MERGESORT_H
#define MATRIXLAB_MERGESORT_H

#include <stddef.h>

/* Top-down mergesort */
__attribute__((noinline))
void mergesort_topdown(double *arr, size_t n);

/* Bottom-up mergesort (iterative) */
__attribute__((noinline))
void mergesort_bottomup(double *arr, size_t n);

/* Natural mergesort (exploits existing order) */
__attribute__((noinline))
void mergesort_natural(double *arr, size_t n);

/* In-place merge (for memory-constrained scenarios) */
__attribute__((noinline))
void mergesort_inplace(double *arr, size_t n);

#endif
