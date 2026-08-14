#include "statistics.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>

/* Compute arithmetic mean */
double stats_mean(const double *data, size_t n) {
    if (n == 0) return 0.0;
    double sum = 0.0;
    for (size_t i = 0; i < n; i++) sum += data[i];
    return sum / (double)n;
}

/* Compute variance (two-pass for numerical stability) */
double stats_variance(const double *data, size_t n) {
    if (n < 2) return 0.0;
    double m = stats_mean(data, n);
    double sum = 0.0;
    for (size_t i = 0; i < n; i++) {
        double d = data[i] - m;
        sum += d * d;
    }
    return sum / (double)(n - 1);
}

/* Compute standard deviation */
double stats_stddev(const double *data, size_t n) {
    return sqrt(stats_variance(data, n));
}

/* Partition helper for quickselect */
static size_t stats_partition(double *data, size_t lo, size_t hi) {
    double pivot = data[hi];
    size_t i = lo;
    for (size_t j = lo; j < hi; j++) {
        if (data[j] <= pivot) {
            double tmp = data[i]; data[i] = data[j]; data[j] = tmp;
            i++;
        }
    }
    double tmp = data[i]; data[i] = data[hi]; data[hi] = tmp;
    return i;
}

/* Quickselect for k-th element */
__attribute__((noinline))
static double stats_quickselect(double *data, size_t n, size_t k) {
    size_t lo = 0, hi = n - 1;
    while (lo < hi) {
        size_t p = stats_partition(data, lo, hi);
        if (p == k) return data[p];
        else if (p < k) lo = p + 1;
        else hi = p - 1;
    }
    return data[lo];
}

/* Compute median (modifies array order) */
double stats_median(double *data, size_t n) {
    if (n == 0) return 0.0;
    if (n == 1) return data[0];
    return stats_quickselect(data, n, n / 2);
}

/* Compute percentile (modifies array order) */
double stats_percentile(double *data, size_t n, double p) {
    if (n == 0) return 0.0;
    size_t idx = (size_t)(p * (double)(n - 1));
    if (idx >= n) idx = n - 1;
    return stats_quickselect(data, n, idx);
}

/* Initialize running statistics */
void stats_running_init(running_stats_t *rs) {
    rs->count = 0;
    rs->mean = 0.0;
    rs->m2 = 0.0;
    rs->min = 1e308;
    rs->max = -1e308;
}

/* Push value using Welford's online algorithm */
void stats_running_push(running_stats_t *rs, double value) {
    rs->count += 1.0;
    double delta = value - rs->mean;
    rs->mean += delta / rs->count;
    double delta2 = value - rs->mean;
    rs->m2 += delta * delta2;
    if (value < rs->min) rs->min = value;
    if (value > rs->max) rs->max = value;
}

/* Get running mean */
double stats_running_mean(const running_stats_t *rs) {
    return rs->mean;
}

/* Get running variance */
double stats_running_variance(const running_stats_t *rs) {
    if (rs->count < 2.0) return 0.0;
    return rs->m2 / (rs->count - 1.0);
}

/* Create a histogram */
histogram_t *stats_histogram_create(int nbins, double lo, double hi) {
    histogram_t *h = (histogram_t *)malloc(sizeof(histogram_t));
    if (!h) return NULL;
    h->nbins = nbins;
    h->lo = lo;
    h->hi = hi;
    h->total = 0;
    h->bins = (int *)calloc((size_t)nbins, sizeof(int));
    if (!h->bins) { free(h); return NULL; }
    return h;
}

/* Destroy a histogram */
void stats_histogram_destroy(histogram_t *h) {
    if (!h) return;
    free(h->bins);
    free(h);
}

/* Add value to histogram */
void stats_histogram_add(histogram_t *h, double value) {
    if (value < h->lo || value >= h->hi) return;
    double width = (h->hi - h->lo) / (double)h->nbins;
    int bin = (int)((value - h->lo) / width);
    if (bin >= h->nbins) bin = h->nbins - 1;
    h->bins[bin]++;
    h->total++;
}

/* Print histogram */
void stats_histogram_print(const histogram_t *h) {
    double width = (h->hi - h->lo) / (double)h->nbins;
    int max_count = 0;
    for (int i = 0; i < h->nbins; i++) {
        if (h->bins[i] > max_count) max_count = h->bins[i];
    }

    for (int i = 0; i < h->nbins; i++) {
        double lo = h->lo + width * i;
        int bar_len = max_count > 0 ? (h->bins[i] * 40 / max_count) : 0;
        printf("[%7.2f] %6d |", lo, h->bins[i]);
        for (int j = 0; j < bar_len; j++) printf("#");
        printf("\n");
    }
}

/* Compute covariance between x and y */
double stats_covariance(const double *x, const double *y, size_t n) {
    if (n < 2) return 0.0;
    double mx = stats_mean(x, n);
    double my = stats_mean(y, n);
    double sum = 0.0;
    for (size_t i = 0; i < n; i++) {
        sum += (x[i] - mx) * (y[i] - my);
    }
    return sum / (double)(n - 1);
}

/* Pearson correlation coefficient */
double stats_correlation(const double *x, const double *y, size_t n) {
    double cov = stats_covariance(x, y, n);
    double sx = stats_stddev(x, n);
    double sy = stats_stddev(y, n);
    if (sx < 1e-12 || sy < 1e-12) return 0.0;
    return cov / (sx * sy);
}

/* Moving average */
void stats_moving_average(const double *data, double *out, size_t n, int window) {
    if (window <= 0 || n == 0) return;
    double sum = 0.0;
    for (size_t i = 0; i < n; i++) {
        sum += data[i];
        if ((int)i >= window) sum -= data[i - (size_t)window];
        int count = (int)i < window ? (int)i + 1 : window;
        out[i] = sum / (double)count;
    }
}
