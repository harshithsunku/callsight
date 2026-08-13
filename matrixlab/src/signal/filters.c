#include "filters.h"
#include "../utils/rng.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* FIR filter implementation */
__attribute__((noinline))
void filter_fir(const double *input, double *output, size_t n,
                const double *coeffs, int ntaps) {
    for (size_t i = 0; i < n; i++) {
        double sum = 0.0;
        for (int k = 0; k < ntaps; k++) {
            if (i >= (size_t)k) {
                sum += coeffs[k] * input[i - (size_t)k];
            }
        }
        output[i] = sum;
    }
}

/* IIR biquad filter */
__attribute__((noinline))
void filter_iir_biquad(const double *input, double *output, size_t n,
                        const double b[3], const double a[3]) {
    double x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    for (size_t i = 0; i < n; i++) {
        double x0 = input[i];
        double y0 = b[0] * x0 + b[1] * x1 + b[2] * x2
                   - a[1] * y1 - a[2] * y2;
        output[i] = y0;
        x2 = x1; x1 = x0;
        y2 = y1; y1 = y0;
    }
}

/* Generate low-pass FIR using windowed sinc */
void filter_design_lowpass(double *coeffs, int ntaps, double cutoff) {
    int M = ntaps - 1;
    double sum = 0.0;
    for (int i = 0; i <= M; i++) {
        double n = (double)i - (double)M / 2.0;
        if (fabs(n) < 1e-12) {
            coeffs[i] = 2.0 * cutoff;
        } else {
            coeffs[i] = sin(2.0 * M_PI * cutoff * n) / (M_PI * n);
        }
        /* Hamming window */
        coeffs[i] *= 0.54 - 0.46 * cos(2.0 * M_PI * (double)i / (double)M);
        sum += coeffs[i];
    }
    /* Normalize */
    for (int i = 0; i <= M; i++) {
        coeffs[i] /= sum;
    }
}

/* Generate high-pass FIR via spectral inversion */
void filter_design_highpass(double *coeffs, int ntaps, double cutoff) {
    filter_design_lowpass(coeffs, ntaps, cutoff);
    for (int i = 0; i < ntaps; i++) {
        coeffs[i] = -coeffs[i];
    }
    coeffs[ntaps / 2] += 1.0;
}

/* Insertion sort for small window median */
static void median_sort(double *arr, int n) {
    for (int i = 1; i < n; i++) {
        double key = arr[i];
        int j = i - 1;
        while (j >= 0 && arr[j] > key) {
            arr[j + 1] = arr[j];
            j--;
        }
        arr[j + 1] = key;
    }
}

/* Median filter */
__attribute__((noinline))
void filter_median(const double *input, double *output, size_t n, int window) {
    double *buf = (double *)malloc((size_t)window * sizeof(double));
    if (!buf) return;

    int half = window / 2;
    for (size_t i = 0; i < n; i++) {
        int count = 0;
        for (int k = -half; k <= half; k++) {
            int idx = (int)i + k;
            if (idx >= 0 && idx < (int)n) {
                buf[count++] = input[idx];
            }
        }
        median_sort(buf, count);
        output[i] = buf[count / 2];
    }
    free(buf);
}

/* Exponential moving average */
void filter_ema(const double *input, double *output, size_t n, double alpha) {
    if (n == 0) return;
    output[0] = input[0];
    for (size_t i = 1; i < n; i++) {
        output[i] = alpha * input[i] + (1.0 - alpha) * output[i - 1];
    }
}

/* Generate noisy sinusoidal signal */
void filter_generate_noisy(double *output, size_t n, double freq, double noise_amp) {
    for (size_t i = 0; i < n; i++) {
        double t = (double)i / (double)n;
        output[i] = sin(2.0 * M_PI * freq * t) + noise_amp * rng_next_gaussian(0.0, 1.0);
    }
}

/* Cascade multiple biquad sections */
__attribute__((noinline))
void filter_cascade_biquad(const double *input, double *output, size_t n,
                            const double (*b)[3], const double (*a)[3], int nsections) {
    double *tmp1 = (double *)malloc(n * sizeof(double));
    double *tmp2 = (double *)malloc(n * sizeof(double));
    if (!tmp1 || !tmp2) { free(tmp1); free(tmp2); return; }

    memcpy(tmp1, input, n * sizeof(double));

    for (int s = 0; s < nsections; s++) {
        filter_iir_biquad(tmp1, tmp2, n, b[s], a[s]);
        /* Swap buffers */
        double *swap = tmp1; tmp1 = tmp2; tmp2 = swap;
    }

    memcpy(output, tmp1, n * sizeof(double));
    free(tmp1); free(tmp2);
}
