#ifndef MATRIXLAB_HELPERS_H
#define MATRIXLAB_HELPERS_H

#include <stddef.h>
#include <stdint.h>

/* Bit manipulation helpers */
static inline uint32_t helpers_popcount(uint32_t x) {
    x = x - ((x >> 1) & 0x55555555u);
    x = (x & 0x33333333u) + ((x >> 2) & 0x33333333u);
    return (((x + (x >> 4)) & 0x0F0F0F0Fu) * 0x01010101u) >> 24;
}

static inline uint32_t helpers_clz(uint32_t x) {
    if (x == 0) return 32;
    uint32_t n = 0;
    if ((x & 0xFFFF0000u) == 0) { n += 16; x <<= 16; }
    if ((x & 0xFF000000u) == 0) { n += 8; x <<= 8; }
    if ((x & 0xF0000000u) == 0) { n += 4; x <<= 4; }
    if ((x & 0xC0000000u) == 0) { n += 2; x <<= 2; }
    if ((x & 0x80000000u) == 0) { n += 1; }
    return n;
}

static inline uint32_t helpers_next_pow2(uint32_t x) {
    x--;
    x |= x >> 1; x |= x >> 2; x |= x >> 4;
    x |= x >> 8; x |= x >> 16;
    return x + 1;
}

/* Byte reversal */
static inline uint32_t helpers_bswap32(uint32_t x) {
    return ((x >> 24) & 0xFF) | ((x >> 8) & 0xFF00) |
           ((x << 8) & 0xFF0000) | ((x << 24) & 0xFF000000u);
}

/* Min/Max helpers */
static inline int helpers_min_int(int a, int b) { return a < b ? a : b; }
static inline int helpers_max_int(int a, int b) { return a > b ? a : b; }
static inline double helpers_min_dbl(double a, double b) { return a < b ? a : b; }
static inline double helpers_max_dbl(double a, double b) { return a > b ? a : b; }
static inline double helpers_clamp(double x, double lo, double hi) {
    return x < lo ? lo : (x > hi ? hi : x);
}

/* Bit rotation */
static inline uint32_t helpers_rotl32(uint32_t x, int k) {
    return (x << k) | (x >> (32 - k));
}
static inline uint32_t helpers_rotr32(uint32_t x, int k) {
    return (x >> k) | (x << (32 - k));
}

/* Hash combine */
static inline uint64_t helpers_hash_combine(uint64_t h1, uint64_t h2) {
    h1 ^= h2 + 0x9E3779B97F4A7C15ULL + (h1 << 6) + (h1 >> 2);
    return h1;
}

/* Simple string hash (FNV-1a) */
uint64_t helpers_fnv1a(const void *data, size_t len);

/* Hex dump for debugging */
void helpers_hexdump(const void *data, size_t len);

/* Compute checksum of a buffer */
uint32_t helpers_checksum(const void *data, size_t len);

/* Bit pattern generation for testing */
void helpers_generate_bitpattern(uint8_t *buf, size_t len, uint32_t seed);

/* Memory comparison with timing attack resistance */
int helpers_constant_time_compare(const void *a, const void *b, size_t len);

#endif
