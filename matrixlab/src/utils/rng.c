#include "rng.h"
#include <math.h>
#include <string.h>

/* Thread-local xoshiro256** state */
static __thread uint64_t rng_state[4] = {0x12345678DEADBEEFULL, 0xABCDEF0123456789ULL,
                                          0xFEDCBA9876543210ULL, 0x0102030405060708ULL};

/* SplitMix64 for seeding */
static inline uint64_t splitmix64(uint64_t *state) {
    uint64_t result = (*state += 0x9E3779B97F4A7C15ULL);
    result = (result ^ (result >> 30)) * 0xBF58476D1CE4E5B9ULL;
    result = (result ^ (result >> 27)) * 0x94D049BB133111EBULL;
    return result ^ (result >> 31);
}

/* Rotate left helper */
static inline uint64_t rotl(const uint64_t x, int k) {
    return (x << k) | (x >> (64 - k));
}

/* Initialize the RNG subsystem */
void rng_init(uint64_t seed) {
    uint64_t s = seed;
    rng_state[0] = splitmix64(&s);
    rng_state[1] = splitmix64(&s);
    rng_state[2] = splitmix64(&s);
    rng_state[3] = splitmix64(&s);
}

/* Thread-local RNG seed */
void rng_seed_thread(uint64_t seed) {
    rng_init(seed);
}

/* Core xoshiro256** generator */
static inline uint64_t rng_next_raw(void) {
    const uint64_t result = rotl(rng_state[1] * 5, 7) * 9;
    const uint64_t t = rng_state[1] << 17;

    rng_state[2] ^= rng_state[0];
    rng_state[3] ^= rng_state[1];
    rng_state[1] ^= rng_state[2];
    rng_state[0] ^= rng_state[3];

    rng_state[2] ^= t;
    rng_state[3] = rotl(rng_state[3], 45);

    return result;
}

/* Generate a random uint32 */
uint32_t rng_next_u32(void) {
    return (uint32_t)(rng_next_raw() >> 32);
}

/* Generate a random uint64 */
uint64_t rng_next_u64(void) {
    return rng_next_raw();
}

/* Generate a random double in [0, 1) */
double rng_next_double(void) {
    return (double)(rng_next_raw() >> 11) * 0x1.0p-53;
}

/* Generate a random double in [lo, hi) */
double rng_next_range(double lo, double hi) {
    return lo + rng_next_double() * (hi - lo);
}

/* Generate a random int in [lo, hi) */
int rng_next_int(int lo, int hi) {
    if (lo >= hi) return lo;
    return lo + (int)(rng_next_u32() % (uint32_t)(hi - lo));
}

/* Fill buffer with random bytes */
void rng_fill_bytes(void *buf, size_t len) {
    uint8_t *p = (uint8_t *)buf;
    while (len >= 8) {
        uint64_t val = rng_next_raw();
        memcpy(p, &val, 8);
        p += 8;
        len -= 8;
    }
    if (len > 0) {
        uint64_t val = rng_next_raw();
        memcpy(p, &val, len);
    }
}

/* Gaussian distribution using Box-Muller transform */
double rng_next_gaussian(double mean, double stddev) {
    static __thread int have_spare = 0;
    static __thread double spare;

    if (have_spare) {
        have_spare = 0;
        return mean + stddev * spare;
    }

    double u, v, s;
    do {
        u = rng_next_double() * 2.0 - 1.0;
        v = rng_next_double() * 2.0 - 1.0;
        s = u * u + v * v;
    } while (s >= 1.0 || s == 0.0);

    s = sqrt(-2.0 * log(s) / s);
    spare = v * s;
    have_spare = 1;
    return mean + stddev * u * s;
}

/* Shuffle array of doubles using Fisher-Yates */
void rng_shuffle_doubles(double *arr, size_t n) {
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = rng_next_u64() % (i + 1);
        double tmp = arr[i];
        arr[i] = arr[j];
        arr[j] = tmp;
    }
}

/* Shuffle array of ints using Fisher-Yates */
void rng_shuffle_ints(int *arr, size_t n) {
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = rng_next_u64() % (i + 1);
        int tmp = arr[i];
        arr[i] = arr[j];
        arr[j] = tmp;
    }
}

/* Shuffle array of pointers using Fisher-Yates */
void rng_shuffle_ptrs(void **arr, size_t n) {
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = rng_next_u64() % (i + 1);
        void *tmp = arr[i];
        arr[i] = arr[j];
        arr[j] = tmp;
    }
}
