#ifndef MATRIXLAB_QUICKSORT_H
#define MATRIXLAB_QUICKSORT_H

#include <stddef.h>

/* Standard quicksort */
__attribute__((noinline))
void quicksort(double *arr, size_t n);

/* Three-way quicksort (handles duplicates) */
__attribute__((noinline))
void quicksort_3way(double *arr, size_t n);

/* Randomized quicksort */
__attribute__((noinline))
void quicksort_random(double *arr, size_t n);

/* Quicksort with insertion sort for small partitions */
__attribute__((noinline))
void quicksort_hybrid(double *arr, size_t n);

/* Verify array is sorted */
int sort_is_sorted(const double *arr, size_t n);

#endif
