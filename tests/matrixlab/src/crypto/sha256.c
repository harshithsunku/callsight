#include "sha256.h"
#include <string.h>

/* SHA-256 constants */
static const uint32_t K256[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
};

/* Bit manipulation macros */
#define ROTR32(x, n)  (((x) >> (n)) | ((x) << (32 - (n))))
#define CH(x, y, z)   (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z)  (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define SIGMA0(x)      (ROTR32(x, 2) ^ ROTR32(x, 13) ^ ROTR32(x, 22))
#define SIGMA1(x)      (ROTR32(x, 6) ^ ROTR32(x, 11) ^ ROTR32(x, 25))
#define sigma0(x)      (ROTR32(x, 7) ^ ROTR32(x, 18) ^ ((x) >> 3))
#define sigma1(x)      (ROTR32(x, 17) ^ ROTR32(x, 19) ^ ((x) >> 10))

/* Process one 64-byte block */
__attribute__((noinline))
static void sha256_transform(sha256_ctx_t *ctx, const uint8_t block[64]) {
    uint32_t W[64];
    uint32_t a, b, c, d, e, f, g, h;

    /* Prepare message schedule */
    for (int t = 0; t < 16; t++) {
        W[t] = ((uint32_t)block[t * 4] << 24) |
               ((uint32_t)block[t * 4 + 1] << 16) |
               ((uint32_t)block[t * 4 + 2] << 8) |
               ((uint32_t)block[t * 4 + 3]);
    }
    for (int t = 16; t < 64; t++) {
        W[t] = sigma1(W[t - 2]) + W[t - 7] + sigma0(W[t - 15]) + W[t - 16];
    }

    /* Initialize working variables */
    a = ctx->state[0]; b = ctx->state[1];
    c = ctx->state[2]; d = ctx->state[3];
    e = ctx->state[4]; f = ctx->state[5];
    g = ctx->state[6]; h = ctx->state[7];

    /* 64 rounds */
    for (int t = 0; t < 64; t++) {
        uint32_t T1 = h + SIGMA1(e) + CH(e, f, g) + K256[t] + W[t];
        uint32_t T2 = SIGMA0(a) + MAJ(a, b, c);
        h = g; g = f; f = e;
        e = d + T1;
        d = c; c = b; b = a;
        a = T1 + T2;
    }

    /* Update state */
    ctx->state[0] += a; ctx->state[1] += b;
    ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f;
    ctx->state[6] += g; ctx->state[7] += h;
}

/* Initialize SHA-256 context */
void sha256_init(sha256_ctx_t *ctx) {
    ctx->state[0] = 0x6a09e667; ctx->state[1] = 0xbb67ae85;
    ctx->state[2] = 0x3c6ef372; ctx->state[3] = 0xa54ff53a;
    ctx->state[4] = 0x510e527f; ctx->state[5] = 0x9b05688c;
    ctx->state[6] = 0x1f83d9ab; ctx->state[7] = 0x5be0cd19;
    ctx->bitcount = 0;
    ctx->buflen = 0;
}

/* Update hash with data */
__attribute__((noinline))
void sha256_update(sha256_ctx_t *ctx, const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;

    ctx->bitcount += (uint64_t)len * 8;

    /* Fill buffer and process */
    while (len > 0) {
        size_t space = 64 - ctx->buflen;
        size_t copy = len < space ? len : space;
        memcpy(ctx->buffer + ctx->buflen, p, copy);
        ctx->buflen += (uint32_t)copy;
        p += copy;
        len -= copy;

        if (ctx->buflen == 64) {
            sha256_transform(ctx, ctx->buffer);
            ctx->buflen = 0;
        }
    }
}

/* Finalize hash */
__attribute__((noinline))
void sha256_final(sha256_ctx_t *ctx, uint8_t digest[32]) {
    /* Padding */
    uint8_t pad[64];
    memset(pad, 0, 64);
    pad[0] = 0x80;

    uint32_t padlen = (ctx->buflen < 56) ? (56 - ctx->buflen) : (120 - ctx->buflen);
    sha256_update(ctx, pad, padlen);

    /* Append length in bits (big-endian) */
    uint8_t bits[8];
    for (int i = 0; i < 8; i++) {
        bits[i] = (uint8_t)(ctx->bitcount >> (56 - i * 8));
    }
    sha256_update(ctx, bits, 8);

    /* Output */
    for (int i = 0; i < 8; i++) {
        digest[i * 4]     = (uint8_t)(ctx->state[i] >> 24);
        digest[i * 4 + 1] = (uint8_t)(ctx->state[i] >> 16);
        digest[i * 4 + 2] = (uint8_t)(ctx->state[i] >> 8);
        digest[i * 4 + 3] = (uint8_t)(ctx->state[i]);
    }
}

/* One-shot hash */
__attribute__((noinline))
void sha256_hash(const void *data, size_t len, uint8_t digest[32]) {
    sha256_ctx_t ctx;
    sha256_init(&ctx);
    sha256_update(&ctx, data, len);
    sha256_final(&ctx, digest);
}

/* Convert digest to hex string */
void sha256_to_hex(const uint8_t digest[32], char hex[65]) {
    static const char hexchars[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        hex[i * 2]     = hexchars[digest[i] >> 4];
        hex[i * 2 + 1] = hexchars[digest[i] & 0x0f];
    }
    hex[64] = '\0';
}

/* Iterative hashing stress test */
__attribute__((noinline))
void sha256_stress(const void *data, size_t len, int iterations, uint8_t digest[32]) {
    sha256_hash(data, len, digest);
    for (int i = 1; i < iterations; i++) {
        sha256_hash(digest, 32, digest);
    }
}
