#ifndef MATRIXLAB_RADIXSORT_H
#define MATRIXLAB_RADIXSORT_H

#include <stddef.h>
#include <stdint.h>

/* Radix sort for unsigned integers */
__attribute__((noinline))
void radixsort_u32(uint32_t *arr, size_t n);

/* Radix sort for signed integers (offset trick) */
__attribute__((noinline))
void radixsort_i32(int32_t *arr, size_t n);

/* Counting sort (used as radix sub-routine) */
__attribute__((noinline))
void counting_sort(uint32_t *arr, size_t n, int bit);

/* Bucket sort for doubles in [0, 1) */
__attribute__((noinline))
void bucketsort_doubles(double *arr, size_t n);

/* Shell sort (gap-based insertion sort) */
__attribute__((noinline))
void shellsort(double *arr, size_t n);

#endif
