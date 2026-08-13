#include "heapsort.h"

/* Swap two doubles */
static inline void hp_swap(double *a, double *b) {
    double t = *a; *a = *b; *b = t;
}

/* Sift down element at index i */
void heapsort_sift_down(double *arr, size_t n, size_t i) {
    while (1) {
        size_t largest = i;
        size_t left = 2 * i + 1;
        size_t right = 2 * i + 2;

        if (left < n && arr[left] > arr[largest]) largest = left;
        if (right < n && arr[right] > arr[largest]) largest = right;

        if (largest == i) break;
        hp_swap(&arr[i], &arr[largest]);
        i = largest;
    }
}

/* Build a max-heap */
void heapsort_build_heap(double *arr, size_t n) {
    if (n <= 1) return;
    for (size_t i = n / 2; i > 0; i--) {
        heapsort_sift_down(arr, n, i - 1);
    }
}

/* Standard heapsort */
__attribute__((noinline))
void heapsort_sort(double *arr, size_t n) {
    if (n <= 1) return;

    heapsort_build_heap(arr, n);

    for (size_t end = n - 1; end > 0; end--) {
        hp_swap(&arr[0], &arr[end]);
        heapsort_sift_down(arr, end, 0);
    }
}

/* Extract max from heap */
double heap_extract_max(double *arr, size_t *n) {
    double max = arr[0];
    (*n)--;
    arr[0] = arr[*n];
    heapsort_sift_down(arr, *n, 0);
    return max;
}

/* Sift up for insertion */
static void heap_sift_up(double *arr, size_t i) {
    while (i > 0) {
        size_t parent = (i - 1) / 2;
        if (arr[i] > arr[parent]) {
            hp_swap(&arr[i], &arr[parent]);
            i = parent;
        } else {
            break;
        }
    }
}

/* Insert into heap */
void heap_insert(double *arr, size_t *n, double val) {
    arr[*n] = val;
    (*n)++;
    heap_sift_up(arr, *n - 1);
}

/* Smoothsort (simplified Leonardo heap variant) */
__attribute__((noinline))
void smoothsort(double *arr, size_t n) {
    /* Simplified: use insertion sort for nearly-sorted detection, else heapsort */
    int nearly_sorted = 1;
    size_t inversions = 0;
    for (size_t i = 1; i < n && inversions < n / 4; i++) {
        if (arr[i] < arr[i - 1]) inversions++;
    }

    if (inversions < n / 10) {
        /* Nearly sorted: use insertion sort */
        for (size_t i = 1; i < n; i++) {
            double key = arr[i];
            size_t j = i;
            while (j > 0 && arr[j - 1] > key) {
                arr[j] = arr[j - 1];
                j--;
            }
            arr[j] = key;
        }
    } else {
        /* Fall back to heapsort */
        (void)nearly_sorted;
        heapsort_sort(arr, n);
    }
}
