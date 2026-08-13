#include "fft.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Bit-reversal permutation */
void fft_bit_reverse(complex_t *data, size_t n) {
    size_t j = 0;
    for (size_t i = 0; i < n - 1; i++) {
        if (i < j) {
            complex_t tmp = data[i];
            data[i] = data[j];
            data[j] = tmp;
        }
        size_t m = n >> 1;
        while (m >= 1 && j >= m) {
            j -= m;
            m >>= 1;
        }
        j += m;
    }
}

/* Iterative radix-2 Cooley-Tukey FFT */
__attribute__((noinline))
void fft_transform(complex_t *data, size_t n, int inverse) {
    if (n <= 1) return;

    fft_bit_reverse(data, n);

    for (size_t len = 2; len <= n; len <<= 1) {
        double angle = 2.0 * M_PI / (double)len * (inverse ? -1.0 : 1.0);
        complex_t wn = {cos(angle), sin(angle)};

        for (size_t i = 0; i < n; i += len) {
            complex_t w = {1.0, 0.0};
            for (size_t j = 0; j < len / 2; j++) {
                complex_t u = data[i + j];
                complex_t t = complex_mul(w, data[i + j + len / 2]);
                data[i + j] = complex_add(u, t);
                data[i + j + len / 2] = complex_sub(u, t);
                w = complex_mul(w, wn);
            }
        }
    }

    /* Normalize for inverse FFT */
    if (inverse) {
        double inv_n = 1.0 / (double)n;
        for (size_t i = 0; i < n; i++) {
            data[i].re *= inv_n;
            data[i].im *= inv_n;
        }
    }
}

/* Recursive FFT (deep call stacks for profiling) */
__attribute__((noinline))
void fft_recursive(complex_t *data, size_t n, int inverse) {
    if (n <= 1) return;

    /* Separate even and odd */
    complex_t *even = (complex_t *)malloc(n / 2 * sizeof(complex_t));
    complex_t *odd = (complex_t *)malloc(n / 2 * sizeof(complex_t));
    if (!even || !odd) { free(even); free(odd); return; }

    for (size_t i = 0; i < n / 2; i++) {
        even[i] = data[2 * i];
        odd[i] = data[2 * i + 1];
    }

    /* Recurse */
    fft_recursive(even, n / 2, inverse);
    fft_recursive(odd, n / 2, inverse);

    /* Combine */
    double angle = 2.0 * M_PI / (double)n * (inverse ? -1.0 : 1.0);
    complex_t w = {1.0, 0.0};
    complex_t wn = {cos(angle), sin(angle)};

    for (size_t k = 0; k < n / 2; k++) {
        complex_t t = complex_mul(w, odd[k]);
        data[k] = complex_add(even[k], t);
        data[k + n / 2] = complex_sub(even[k], t);
        w = complex_mul(w, wn);
    }

    free(even);
    free(odd);
}

/* Compute power spectrum */
__attribute__((noinline))
void fft_power_spectrum(const complex_t *data, double *power, size_t n) {
    for (size_t i = 0; i < n; i++) {
        power[i] = complex_magnitude(data[i]);
    }
}

/* Generate test signal: sum of sinusoids */
void fft_generate_signal(complex_t *data, size_t n, const double *frequencies, int nfreq) {
    for (size_t i = 0; i < n; i++) {
        data[i].re = 0.0;
        data[i].im = 0.0;
        double t = (double)i / (double)n;
        for (int f = 0; f < nfreq; f++) {
            data[i].re += sin(2.0 * M_PI * frequencies[f] * t);
        }
    }
}
