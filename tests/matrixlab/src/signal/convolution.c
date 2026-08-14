#include "convolution.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* 1D full convolution */
__attribute__((noinline))
void conv_1d(const double *signal, size_t sig_len,
             const double *kernel, size_t ker_len,
             double *output) {
    size_t out_len = sig_len + ker_len - 1;
    memset(output, 0, out_len * sizeof(double));

    for (size_t i = 0; i < sig_len; i++) {
        for (size_t j = 0; j < ker_len; j++) {
            output[i + j] += signal[i] * kernel[j];
        }
    }
}

/* 1D same-size convolution */
__attribute__((noinline))
void conv_1d_same(const double *signal, size_t sig_len,
                   const double *kernel, size_t ker_len,
                   double *output) {
    int offset = (int)ker_len / 2;
    for (size_t i = 0; i < sig_len; i++) {
        double sum = 0.0;
        for (size_t j = 0; j < ker_len; j++) {
            int idx = (int)i - offset + (int)j;
            if (idx >= 0 && idx < (int)sig_len) {
                sum += signal[idx] * kernel[j];
            }
        }
        output[i] = sum;
    }
}

/* 2D convolution */
__attribute__((noinline))
void conv_2d(const double *input, int rows, int cols,
             const double *kernel, int ksize,
             double *output) {
    int half = ksize / 2;
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            double sum = 0.0;
            for (int kr = -half; kr <= half; kr++) {
                for (int kc = -half; kc <= half; kc++) {
                    int rr = r + kr;
                    int cc = c + kc;
                    if (rr >= 0 && rr < rows && cc >= 0 && cc < cols) {
                        int ki = (kr + half) * ksize + (kc + half);
                        sum += input[rr * cols + cc] * kernel[ki];
                    }
                }
            }
            output[r * cols + c] = sum;
        }
    }
}

/* Cross-correlation */
__attribute__((noinline))
void conv_correlate(const double *signal, size_t sig_len,
                     const double *pattern, size_t pat_len,
                     double *output) {
    if (sig_len < pat_len) return;
    size_t out_len = sig_len - pat_len + 1;

    for (size_t i = 0; i < out_len; i++) {
        double sum = 0.0;
        for (size_t j = 0; j < pat_len; j++) {
            sum += signal[i + j] * pattern[j];
        }
        output[i] = sum;
    }
}

/* Auto-correlation */
__attribute__((noinline))
void conv_autocorrelation(const double *signal, size_t n, double *output) {
    for (size_t lag = 0; lag < n; lag++) {
        double sum = 0.0;
        for (size_t i = 0; i < n - lag; i++) {
            sum += signal[i] * signal[i + lag];
        }
        output[lag] = sum;
    }
}

/* Generate Gaussian kernel */
void conv_kernel_gaussian(double *kernel, int size, double sigma) {
    int half = size / 2;
    double sum = 0.0;
    for (int i = 0; i < size; i++) {
        for (int j = 0; j < size; j++) {
            double x = (double)(i - half);
            double y = (double)(j - half);
            double val = exp(-(x * x + y * y) / (2.0 * sigma * sigma));
            kernel[i * size + j] = val;
            sum += val;
        }
    }
    /* Normalize */
    for (int i = 0; i < size * size; i++) {
        kernel[i] /= sum;
    }
}

/* Sobel X kernel */
void conv_kernel_sobel_x(double kernel[9]) {
    double k[] = {-1, 0, 1, -2, 0, 2, -1, 0, 1};
    memcpy(kernel, k, 9 * sizeof(double));
}

/* Sobel Y kernel */
void conv_kernel_sobel_y(double kernel[9]) {
    double k[] = {-1, -2, -1, 0, 0, 0, 1, 2, 1};
    memcpy(kernel, k, 9 * sizeof(double));
}
