#ifndef MATRIXLAB_HEAPSORT_H
#define MATRIXLAB_HEAPSORT_H

#include <stddef.h>

/* Standard heapsort */
__attribute__((noinline))
void heapsort_sort(double *arr, size_t n);

/* Build a max-heap */
void heapsort_build_heap(double *arr, size_t n);

/* Sift down operation */
void heapsort_sift_down(double *arr, size_t n, size_t i);

/* Priority queue operations */
double heap_extract_max(double *arr, size_t *n);
void heap_insert(double *arr, size_t *n, double val);

/* Smoothsort (adaptive heapsort variant) */
__attribute__((noinline))
void smoothsort(double *arr, size_t n);

#endif
