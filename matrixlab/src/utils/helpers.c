#include "helpers.h"
#include <stdio.h>
#include <string.h>

/* FNV-1a hash */
uint64_t helpers_fnv1a(const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    uint64_t hash = 0xCBF29CE484222325ULL;
    for (size_t i = 0; i < len; i++) {
        hash ^= p[i];
        hash *= 0x100000001B3ULL;
    }
    return hash;
}

/* Hex dump for debugging */
void helpers_hexdump(const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    for (size_t i = 0; i < len; i++) {
        if (i > 0 && i % 16 == 0) printf("\n");
        printf("%02x ", p[i]);
    }
    printf("\n");
}

/* Compute simple checksum of a buffer */
uint32_t helpers_checksum(const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    uint32_t sum = 0;
    for (size_t i = 0; i < len; i++) {
        sum = helpers_rotl32(sum, 5) ^ p[i];
    }
    return sum;
}

/* Bit pattern generation for testing */
void helpers_generate_bitpattern(uint8_t *buf, size_t len, uint32_t seed) {
    for (size_t i = 0; i < len; i++) {
        seed = seed * 1103515245 + 12345;
        buf[i] = (uint8_t)(seed >> 16);
    }
}

/* Constant-time comparison */
int helpers_constant_time_compare(const void *a, const void *b, size_t len) {
    const volatile uint8_t *pa = (const volatile uint8_t *)a;
    const volatile uint8_t *pb = (const volatile uint8_t *)b;
    volatile uint8_t diff = 0;
    for (size_t i = 0; i < len; i++) {
        diff |= pa[i] ^ pb[i];
    }
    return diff == 0;
}
