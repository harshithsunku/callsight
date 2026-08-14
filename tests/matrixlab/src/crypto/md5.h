#ifndef MATRIXLAB_MD5_H
#define MATRIXLAB_MD5_H

#include <stdint.h>
#include <stddef.h>

/* MD5 context */
typedef struct {
    uint32_t state[4];
    uint64_t count;
    uint8_t buffer[64];
} md5_ctx_t;

/* Initialize MD5 context */
void md5_init(md5_ctx_t *ctx);

/* Update with data */
__attribute__((noinline))
void md5_update(md5_ctx_t *ctx, const void *data, size_t len);

/* Finalize and produce digest */
__attribute__((noinline))
void md5_final(md5_ctx_t *ctx, uint8_t digest[16]);

/* One-shot hash */
__attribute__((noinline))
void md5_hash(const void *data, size_t len, uint8_t digest[16]);

/* Convert to hex */
void md5_to_hex(const uint8_t digest[16], char hex[33]);

/* Stress test: hash repeatedly */
__attribute__((noinline))
void md5_stress(const void *data, size_t len, int iterations, uint8_t digest[16]);

#endif
