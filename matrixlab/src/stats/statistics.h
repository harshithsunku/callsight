#ifndef MATRIXLAB_STATISTICS_H
#define MATRIXLAB_STATISTICS_H

#include <stddef.h>

/* Basic descriptive statistics */
double stats_mean(const double *data, size_t n);
double stats_variance(const double *data, size_t n);
double stats_stddev(const double *data, size_t n);
double stats_median(double *data, size_t n);
double stats_percentile(double *data, size_t n, double p);

/* Running statistics (online algorithm) */
typedef struct {
    double count;
    double mean;
    double m2;
    double min;
    double max;
} running_stats_t;

void stats_running_init(running_stats_t *rs);
void stats_running_push(running_stats_t *rs, double value);
double stats_running_mean(const running_stats_t *rs);
double stats_running_variance(const running_stats_t *rs);

/* Histogram */
typedef struct {
    int *bins;
    int nbins;
    double lo;
    double hi;
    size_t total;
} histogram_t;

histogram_t *stats_histogram_create(int nbins, double lo, double hi);
void stats_histogram_destroy(histogram_t *h);
void stats_histogram_add(histogram_t *h, double value);
void stats_histogram_print(const histogram_t *h);

/* Covariance and correlation */
double stats_covariance(const double *x, const double *y, size_t n);
double stats_correlation(const double *x, const double *y, size_t n);

/* Moving average */
void stats_moving_average(const double *data, double *out, size_t n, int window);

#endif
