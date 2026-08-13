#ifndef MATRIXLAB_SHA256_H
#define MATRIXLAB_SHA256_H

#include <stdint.h>
#include <stddef.h>

/* SHA-256 context */
typedef struct {
    uint32_t state[8];
    uint64_t bitcount;
    uint8_t buffer[64];
    uint32_t buflen;
} sha256_ctx_t;

/* Initialize SHA-256 context */
void sha256_init(sha256_ctx_t *ctx);

/* Update with data */
__attribute__((noinline))
void sha256_update(sha256_ctx_t *ctx, const void *data, size_t len);

/* Finalize and produce digest */
__attribute__((noinline))
void sha256_final(sha256_ctx_t *ctx, uint8_t digest[32]);

/* One-shot hash */
__attribute__((noinline))
void sha256_hash(const void *data, size_t len, uint8_t digest[32]);

/* Convert digest to hex string */
void sha256_to_hex(const uint8_t digest[32], char hex[65]);

/* Hash a buffer repeatedly for CPU stress */
__attribute__((noinline))
void sha256_stress(const void *data, size_t len, int iterations, uint8_t digest[32]);

#endif
