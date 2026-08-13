#ifndef MATRIXLAB_FFT_H
#define MATRIXLAB_FFT_H

#include <stddef.h>

/* Complex number */
typedef struct {
    double re;
    double im;
} complex_t;

/* In-place radix-2 Cooley-Tukey FFT */
__attribute__((noinline))
void fft_transform(complex_t *data, size_t n, int inverse);

/* Recursive FFT (for deep call stacks in profiling) */
__attribute__((noinline))
void fft_recursive(complex_t *data, size_t n, int inverse);

/* Power spectrum from FFT output */
__attribute__((noinline))
void fft_power_spectrum(const complex_t *data, double *power, size_t n);

/* Generate test signal: sum of sinusoids */
void fft_generate_signal(complex_t *data, size_t n, const double *frequencies, int nfreq);

/* Complex arithmetic helpers */
static inline complex_t complex_add(complex_t a, complex_t b) {
    return (complex_t){a.re + b.re, a.im + b.im};
}
static inline complex_t complex_sub(complex_t a, complex_t b) {
    return (complex_t){a.re - b.re, a.im - b.im};
}
static inline complex_t complex_mul(complex_t a, complex_t b) {
    return (complex_t){a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re};
}
static inline double complex_magnitude(complex_t a) {
    return a.re * a.re + a.im * a.im;
}

/* Bit-reversal permutation */
void fft_bit_reverse(complex_t *data, size_t n);

#endif
