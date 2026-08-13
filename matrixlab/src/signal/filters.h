#ifndef MATRIXLAB_FILTERS_H
#define MATRIXLAB_FILTERS_H

#include <stddef.h>

/* FIR filter */
__attribute__((noinline))
void filter_fir(const double *input, double *output, size_t n,
                const double *coeffs, int ntaps);

/* IIR filter (second-order section) */
__attribute__((noinline))
void filter_iir_biquad(const double *input, double *output, size_t n,
                        const double b[3], const double a[3]);

/* Generate low-pass FIR coefficients (windowed sinc) */
void filter_design_lowpass(double *coeffs, int ntaps, double cutoff);

/* Generate high-pass FIR coefficients */
void filter_design_highpass(double *coeffs, int ntaps, double cutoff);

/* Apply median filter */
__attribute__((noinline))
void filter_median(const double *input, double *output, size_t n, int window);

/* Apply exponential moving average */
void filter_ema(const double *input, double *output, size_t n, double alpha);

/* Generate noisy test signal */
void filter_generate_noisy(double *output, size_t n, double freq, double noise_amp);

/* Cascade multiple biquad sections */
__attribute__((noinline))
void filter_cascade_biquad(const double *input, double *output, size_t n,
                            const double (*b)[3], const double (*a)[3], int nsections);

#endif
