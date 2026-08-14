#include "crc32.h"
#include <string.h>

/* CRC32 lookup table */
static uint32_t crc32_table[256];
static uint32_t crc32c_table[256];
static int crc32_table_initialized = 0;

/* Initialize CRC32 lookup table */
void crc32_init_table(void) {
    if (crc32_table_initialized) return;

    /* Standard CRC32 (IEEE) polynomial */
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t crc = i;
        for (int j = 0; j < 8; j++) {
            if (crc & 1)
                crc = (crc >> 1) ^ 0xEDB88320u;
            else
                crc >>= 1;
        }
        crc32_table[i] = crc;
    }

    /* CRC32C (Castagnoli) polynomial */
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t crc = i;
        for (int j = 0; j < 8; j++) {
            if (crc & 1)
                crc = (crc >> 1) ^ 0x82F63B78u;
            else
                crc >>= 1;
        }
        crc32c_table[i] = crc;
    }

    crc32_table_initialized = 1;
}

/* Update running CRC32 */
uint32_t crc32_update(uint32_t crc, const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    crc ^= 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc = crc32_table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFFu;
}

/* Compute CRC32 of data */
__attribute__((noinline))
uint32_t crc32_compute(const void *data, size_t len) {
    return crc32_update(0, data, len);
}

/* CRC32 stress test */
__attribute__((noinline))
uint32_t crc32_stress(const void *data, size_t len, int iterations) {
    uint32_t crc = 0;
    for (int i = 0; i < iterations; i++) {
        crc = crc32_update(crc, data, len);
    }
    return crc;
}

/* CRC32C computation */
__attribute__((noinline))
uint32_t crc32c_compute(const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc = crc32c_table[(crc ^ p[i]) & 0xFF] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFFu;
}
