#include "mergesort.h"
#include <stdlib.h>
#include <string.h>

/* Merge two sorted subarrays */
__attribute__((noinline))
static void merge(double *arr, double *tmp, size_t lo, size_t mid, size_t hi) {
    memcpy(tmp + lo, arr + lo, (hi - lo + 1) * sizeof(double));

    size_t i = lo, j = mid + 1, k = lo;
    while (i <= mid && j <= hi) {
        if (tmp[i] <= tmp[j])
            arr[k++] = tmp[i++];
        else
            arr[k++] = tmp[j++];
    }
    while (i <= mid) arr[k++] = tmp[i++];
    while (j <= hi) arr[k++] = tmp[j++];
}

/* Recursive mergesort */
__attribute__((noinline))
static void msort_recursive(double *arr, double *tmp, size_t lo, size_t hi) {
    if (lo >= hi) return;
    size_t mid = lo + (hi - lo) / 2;
    msort_recursive(arr, tmp, lo, mid);
    msort_recursive(arr, tmp, mid + 1, hi);
    merge(arr, tmp, lo, mid, hi);
}

/* Top-down mergesort */
__attribute__((noinline))
void mergesort_topdown(double *arr, size_t n) {
    if (n <= 1) return;
    double *tmp = (double *)malloc(n * sizeof(double));
    if (!tmp) return;
    msort_recursive(arr, tmp, 0, n - 1);
    free(tmp);
}

/* Bottom-up (iterative) mergesort */
__attribute__((noinline))
void mergesort_bottomup(double *arr, size_t n) {
    if (n <= 1) return;
    double *tmp = (double *)malloc(n * sizeof(double));
    if (!tmp) return;

    for (size_t width = 1; width < n; width *= 2) {
        for (size_t lo = 0; lo < n - width; lo += 2 * width) {
            size_t mid = lo + width - 1;
            size_t hi = lo + 2 * width - 1;
            if (hi >= n) hi = n - 1;
            merge(arr, tmp, lo, mid, hi);
        }
    }
    free(tmp);
}

/* Find end of sorted run */
static size_t find_run_end(const double *arr, size_t start, size_t n) {
    size_t i = start + 1;
    while (i < n && arr[i] >= arr[i - 1]) i++;
    return i - 1;
}

/* Natural mergesort (exploits pre-existing order) */
__attribute__((noinline))
void mergesort_natural(double *arr, size_t n) {
    if (n <= 1) return;
    double *tmp = (double *)malloc(n * sizeof(double));
    if (!tmp) return;

    int sorted = 0;
    while (!sorted) {
        sorted = 1;
        size_t lo = 0;
        while (lo < n - 1) {
            size_t mid = find_run_end(arr, lo, n);
            if (mid >= n - 1) break;
            size_t hi = find_run_end(arr, mid + 1, n);
            merge(arr, tmp, lo, mid, hi);
            if (lo > 0 || hi < n - 1) sorted = 0;
            lo = hi + 1;
        }
    }
    free(tmp);
}

/* In-place merge using rotation */
static void rotate(double *arr, size_t lo, size_t mid, size_t hi) {
    /* Reverse [lo..mid] */
    for (size_t i = lo, j = mid; i < j; i++, j--) {
        double t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    /* Reverse [mid+1..hi] */
    for (size_t i = mid + 1, j = hi; i < j; i++, j--) {
        double t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
    /* Reverse [lo..hi] */
    for (size_t i = lo, j = hi; i < j; i++, j--) {
        double t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    }
}

/* In-place merge */
static void merge_inplace(double *arr, size_t lo, size_t mid, size_t hi) {
    size_t i = lo, j = mid + 1;
    while (i <= mid && j <= hi) {
        if (arr[i] <= arr[j]) {
            i++;
        } else {
            /* Rotate arr[i..mid] and arr[j] */
            rotate(arr, i, mid, j);
            size_t dist = j - mid;
            i += dist;
            mid = j;
            j++;
        }
    }
}

/* In-place mergesort */
__attribute__((noinline))
static void msort_inplace_recursive(double *arr, size_t lo, size_t hi) {
    if (lo >= hi) return;
    size_t mid = lo + (hi - lo) / 2;
    msort_inplace_recursive(arr, lo, mid);
    msort_inplace_recursive(arr, mid + 1, hi);
    merge_inplace(arr, lo, mid, hi);
}

__attribute__((noinline))
void mergesort_inplace(double *arr, size_t n) {
    if (n <= 1) return;
    msort_inplace_recursive(arr, 0, n - 1);
}
