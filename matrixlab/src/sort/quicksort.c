#include "quicksort.h"
#include "../utils/rng.h"

/* Swap two doubles */
static inline void qs_swap(double *a, double *b) {
    double t = *a; *a = *b; *b = t;
}

/* Insertion sort for small arrays */
static void insertion_sort(double *arr, size_t n) {
    for (size_t i = 1; i < n; i++) {
        double key = arr[i];
        size_t j = i;
        while (j > 0 && arr[j - 1] > key) {
            arr[j] = arr[j - 1];
            j--;
        }
        arr[j] = key;
    }
}

/* Lomuto partition */
static size_t qs_partition(double *arr, size_t lo, size_t hi) {
    double pivot = arr[hi];
    size_t i = lo;
    for (size_t j = lo; j < hi; j++) {
        if (arr[j] <= pivot) {
            qs_swap(&arr[i], &arr[j]);
            i++;
        }
    }
    qs_swap(&arr[i], &arr[hi]);
    return i;
}

/* Recursive quicksort implementation (tail-call optimised) */
__attribute__((noinline))
static void qs_recursive(double *arr, size_t lo, size_t hi) {
    while (lo < hi) {
        size_t p = qs_partition(arr, lo, hi);
        /* Recurse into the smaller half, iterate the larger */
        if (p - lo < hi - p) {
            if (p > 0) qs_recursive(arr, lo, p - 1);
            lo = p + 1;
        } else {
            qs_recursive(arr, p + 1, hi);
            if (p == 0) break;
            hi = p - 1;
        }
    }
}

/* Standard quicksort */
__attribute__((noinline))
void quicksort(double *arr, size_t n) {
    if (n <= 1) return;
    qs_recursive(arr, 0, n - 1);
}

/* Three-way partition (Dutch National Flag, tail-call optimised) */
__attribute__((noinline))
static void qs_3way_recursive(double *arr, size_t lo, size_t hi) {
    while (lo < hi) {
        double pivot = arr[lo + (rng_next_u64() % (hi - lo + 1))];
        size_t lt = lo, gt = hi, i = lo;

        while (i <= gt) {
            if (arr[i] < pivot) {
                qs_swap(&arr[lt], &arr[i]);
                lt++; i++;
            } else if (arr[i] > pivot) {
                qs_swap(&arr[i], &arr[gt]);
                if (gt == 0) break;
                gt--;
            } else {
                i++;
            }
        }

        /* Recurse into smaller half, iterate the larger */
        size_t left_size = (lt > lo) ? lt - lo : 0;
        size_t right_size = (hi > gt) ? hi - gt : 0;
        if (left_size < right_size) {
            if (lt > 0 && lo < lt) qs_3way_recursive(arr, lo, lt - 1);
            lo = gt + 1;
        } else {
            if (gt + 1 <= hi) qs_3way_recursive(arr, gt + 1, hi);
            if (lt == 0) break;
            hi = lt - 1;
        }
    }
}

/* Three-way quicksort */
__attribute__((noinline))
void quicksort_3way(double *arr, size_t n) {
    if (n <= 1) return;
    qs_3way_recursive(arr, 0, n - 1);
}

/* Randomized partition */
static size_t qs_partition_random(double *arr, size_t lo, size_t hi) {
    size_t pivot_idx = lo + (rng_next_u64() % (hi - lo + 1));
    qs_swap(&arr[pivot_idx], &arr[hi]);
    return qs_partition(arr, lo, hi);
}

/* Recursive randomized quicksort (tail-call optimised) */
__attribute__((noinline))
static void qs_random_recursive(double *arr, size_t lo, size_t hi) {
    while (lo < hi) {
        size_t p = qs_partition_random(arr, lo, hi);
        if (p - lo < hi - p) {
            if (p > 0) qs_random_recursive(arr, lo, p - 1);
            lo = p + 1;
        } else {
            qs_random_recursive(arr, p + 1, hi);
            if (p == 0) break;
            hi = p - 1;
        }
    }
}

/* Randomized quicksort */
__attribute__((noinline))
void quicksort_random(double *arr, size_t n) {
    if (n <= 1) return;
    qs_random_recursive(arr, 0, n - 1);
}

/* Hybrid quicksort with insertion sort for small partitions (tail-call optimised) */
__attribute__((noinline))
static void qs_hybrid_recursive(double *arr, size_t lo, size_t hi) {
    while (lo < hi) {
        if (hi - lo < 16) {
            insertion_sort(arr + lo, hi - lo + 1);
            return;
        }
        size_t p = qs_partition_random(arr, lo, hi);
        if (p - lo < hi - p) {
            if (p > 0) qs_hybrid_recursive(arr, lo, p - 1);
            lo = p + 1;
        } else {
            qs_hybrid_recursive(arr, p + 1, hi);
            if (p == 0) break;
            hi = p - 1;
        }
    }
}

/* Hybrid quicksort */
__attribute__((noinline))
void quicksort_hybrid(double *arr, size_t n) {
    if (n <= 1) return;
    qs_hybrid_recursive(arr, 0, n - 1);
}

/* Verify sorted order */
int sort_is_sorted(const double *arr, size_t n) {
    for (size_t i = 1; i < n; i++) {
        if (arr[i] < arr[i - 1]) return 0;
    }
    return 1;
}
