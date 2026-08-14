#ifndef MATRIXLAB_CRC32_H
#define MATRIXLAB_CRC32_H

#include <stdint.h>
#include <stddef.h>

/* Initialize CRC32 lookup table */
void crc32_init_table(void);

/* Compute CRC32 of data */
__attribute__((noinline))
uint32_t crc32_compute(const void *data, size_t len);

/* Update running CRC32 */
uint32_t crc32_update(uint32_t crc, const void *data, size_t len);

/* CRC32 stress test */
__attribute__((noinline))
uint32_t crc32_stress(const void *data, size_t len, int iterations);

/* CRC32C (Castagnoli) variant */
__attribute__((noinline))
uint32_t crc32c_compute(const void *data, size_t len);

#endif
