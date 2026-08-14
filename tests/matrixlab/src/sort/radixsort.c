#include "radixsort.h"
#include <stdlib.h>
#include <string.h>

/* Counting sort on a specific byte position */
__attribute__((noinline))
void counting_sort(uint32_t *arr, size_t n, int bit) {
    uint32_t *output = (uint32_t *)malloc(n * sizeof(uint32_t));
    if (!output) return;

    int count[256] = {0};
    int shift = bit * 8;

    /* Count occurrences */
    for (size_t i = 0; i < n; i++) {
        uint8_t byte = (uint8_t)((arr[i] >> shift) & 0xFF);
        count[byte]++;
    }

    /* Prefix sum */
    for (int i = 1; i < 256; i++) {
        count[i] += count[i - 1];
    }

    /* Build output (stable, from end) */
    for (size_t i = n; i > 0; i--) {
        uint8_t byte = (uint8_t)((arr[i - 1] >> shift) & 0xFF);
        count[byte]--;
        output[count[byte]] = arr[i - 1];
    }

    memcpy(arr, output, n * sizeof(uint32_t));
    free(output);
}

/* Radix sort for unsigned integers (LSD) */
__attribute__((noinline))
void radixsort_u32(uint32_t *arr, size_t n) {
    if (n <= 1) return;
    for (int byte = 0; byte < 4; byte++) {
        counting_sort(arr, n, byte);
    }
}

/* Radix sort for signed integers */
__attribute__((noinline))
void radixsort_i32(int32_t *arr, size_t n) {
    if (n <= 1) return;

    /* Offset to make all values unsigned */
    uint32_t *tmp = (uint32_t *)malloc(n * sizeof(uint32_t));
    if (!tmp) return;

    for (size_t i = 0; i < n; i++) {
        tmp[i] = (uint32_t)(arr[i] + (int32_t)0x80000000);
    }

    radixsort_u32(tmp, n);

    for (size_t i = 0; i < n; i++) {
        arr[i] = (int32_t)(tmp[i] - (uint32_t)0x80000000);
    }
    free(tmp);
}

/* Bucket sort for doubles in [0, 1) */
__attribute__((noinline))
void bucketsort_doubles(double *arr, size_t n) {
    if (n <= 1) return;

    int nbuckets = (int)n / 4;
    if (nbuckets < 4) nbuckets = 4;

    /* Allocate buckets */
    typedef struct { double *data; size_t count; size_t cap; } bucket_t;
    bucket_t *buckets = (bucket_t *)calloc((size_t)nbuckets, sizeof(bucket_t));
    if (!buckets) return;

    for (int i = 0; i < nbuckets; i++) {
        buckets[i].cap = 8;
        buckets[i].data = (double *)malloc(8 * sizeof(double));
        buckets[i].count = 0;
    }

    /* Distribute into buckets */
    for (size_t i = 0; i < n; i++) {
        int b = (int)(arr[i] * (double)nbuckets);
        if (b >= nbuckets) b = nbuckets - 1;
        if (b < 0) b = 0;

        bucket_t *bk = &buckets[b];
        if (bk->count >= bk->cap) {
            bk->cap *= 2;
            bk->data = (double *)realloc(bk->data, bk->cap * sizeof(double));
        }
        bk->data[bk->count++] = arr[i];
    }

    /* Sort each bucket (insertion sort) */
    for (int b = 0; b < nbuckets; b++) {
        double *bd = buckets[b].data;
        size_t bn = buckets[b].count;
        for (size_t i = 1; i < bn; i++) {
            double key = bd[i];
            size_t j = i;
            while (j > 0 && bd[j - 1] > key) {
                bd[j] = bd[j - 1];
                j--;
            }
            bd[j] = key;
        }
    }

    /* Concatenate */
    size_t idx = 0;
    for (int b = 0; b < nbuckets; b++) {
        memcpy(arr + idx, buckets[b].data, buckets[b].count * sizeof(double));
        idx += buckets[b].count;
        free(buckets[b].data);
    }
    free(buckets);
}

/* Shell sort */
__attribute__((noinline))
void shellsort(double *arr, size_t n) {
    /* Ciura gap sequence */
    static const size_t gaps[] = {701, 301, 132, 57, 23, 10, 4, 1};
    int ngaps = (int)(sizeof(gaps) / sizeof(gaps[0]));

    for (int g = 0; g < ngaps; g++) {
        size_t gap = gaps[g];
        if (gap >= n) continue;

        for (size_t i = gap; i < n; i++) {
            double temp = arr[i];
            size_t j = i;
            while (j >= gap && arr[j - gap] > temp) {
                arr[j] = arr[j - gap];
                j -= gap;
            }
            arr[j] = temp;
        }
    }
}
