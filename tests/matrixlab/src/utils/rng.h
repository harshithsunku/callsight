#ifndef MATRIXLAB_RNG_H
#define MATRIXLAB_RNG_H

#include <stdint.h>
#include <stddef.h>

/* Initialize the RNG subsystem */
void rng_init(uint64_t seed);

/* Generate a random uint32 */
uint32_t rng_next_u32(void);

/* Generate a random uint64 */
uint64_t rng_next_u64(void);

/* Generate a random double in [0, 1) */
double rng_next_double(void);

/* Generate a random double in [lo, hi) */
double rng_next_range(double lo, double hi);

/* Generate a random int in [lo, hi) */
int rng_next_int(int lo, int hi);

/* Fill buffer with random bytes */
void rng_fill_bytes(void *buf, size_t len);

/* Gaussian distribution (Box-Muller) */
double rng_next_gaussian(double mean, double stddev);

/* Thread-local RNG seed */
void rng_seed_thread(uint64_t seed);

/* Shuffle array of doubles */
void rng_shuffle_doubles(double *arr, size_t n);

/* Shuffle array of ints */
void rng_shuffle_ints(int *arr, size_t n);

/* Shuffle array of pointers */
void rng_shuffle_ptrs(void **arr, size_t n);

#endif
