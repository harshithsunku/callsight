#ifndef MATRIXLAB_CONVOLUTION_H
#define MATRIXLAB_CONVOLUTION_H

#include <stddef.h>

/* 1D convolution (full) */
__attribute__((noinline))
void conv_1d(const double *signal, size_t sig_len,
             const double *kernel, size_t ker_len,
             double *output);

/* 1D convolution (same size as input) */
__attribute__((noinline))
void conv_1d_same(const double *signal, size_t sig_len,
                   const double *kernel, size_t ker_len,
                   double *output);

/* 2D convolution for image-like data */
__attribute__((noinline))
void conv_2d(const double *input, int rows, int cols,
             const double *kernel, int ksize,
             double *output);

/* Correlation (convolution with flipped kernel) */
__attribute__((noinline))
void conv_correlate(const double *signal, size_t sig_len,
                     const double *pattern, size_t pat_len,
                     double *output);

/* Auto-correlation */
__attribute__((noinline))
void conv_autocorrelation(const double *signal, size_t n, double *output);

/* Generate common kernels */
void conv_kernel_gaussian(double *kernel, int size, double sigma);
void conv_kernel_sobel_x(double kernel[9]);
void conv_kernel_sobel_y(double kernel[9]);

#endif
