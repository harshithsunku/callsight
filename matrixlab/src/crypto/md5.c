#include "md5.h"
#include <string.h>

/* MD5 round constants */
static const uint32_t T[64] = {
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391
};

/* Shift amounts per round */
static const int S[64] = {
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21
};

#define ROTL32(x, n) (((x) << (n)) | ((x) >> (32 - (n))))

/* Process one 64-byte block */
__attribute__((noinline))
static void md5_transform(md5_ctx_t *ctx, const uint8_t block[64]) {
    uint32_t M[16];
    for (int i = 0; i < 16; i++) {
        M[i] = ((uint32_t)block[i * 4]) |
               ((uint32_t)block[i * 4 + 1] << 8) |
               ((uint32_t)block[i * 4 + 2] << 16) |
               ((uint32_t)block[i * 4 + 3] << 24);
    }

    uint32_t a = ctx->state[0], b = ctx->state[1];
    uint32_t c = ctx->state[2], d = ctx->state[3];

    for (int i = 0; i < 64; i++) {
        uint32_t f, g;
        if (i < 16) {
            f = (b & c) | (~b & d);
            g = (uint32_t)i;
        } else if (i < 32) {
            f = (d & b) | (~d & c);
            g = (5 * (uint32_t)i + 1) % 16;
        } else if (i < 48) {
            f = b ^ c ^ d;
            g = (3 * (uint32_t)i + 5) % 16;
        } else {
            f = c ^ (b | ~d);
            g = (7 * (uint32_t)i) % 16;
        }

        uint32_t temp = d;
        d = c;
        c = b;
        b = b + ROTL32(a + f + T[i] + M[g], S[i]);
        a = temp;
    }

    ctx->state[0] += a; ctx->state[1] += b;
    ctx->state[2] += c; ctx->state[3] += d;
}

/* Initialize MD5 context */
void md5_init(md5_ctx_t *ctx) {
    ctx->state[0] = 0x67452301;
    ctx->state[1] = 0xefcdab89;
    ctx->state[2] = 0x98badcfe;
    ctx->state[3] = 0x10325476;
    ctx->count = 0;
    memset(ctx->buffer, 0, 64);
}

/* Update hash with data */
__attribute__((noinline))
void md5_update(md5_ctx_t *ctx, const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    uint32_t bufidx = (uint32_t)(ctx->count % 64);
    ctx->count += len;

    while (len > 0) {
        size_t space = 64 - bufidx;
        size_t copy = len < space ? len : space;
        memcpy(ctx->buffer + bufidx, p, copy);
        bufidx += (uint32_t)copy;
        p += copy;
        len -= copy;
        if (bufidx == 64) {
            md5_transform(ctx, ctx->buffer);
            bufidx = 0;
        }
    }
}

/* Finalize hash */
__attribute__((noinline))
void md5_final(md5_ctx_t *ctx, uint8_t digest[16]) {
    uint64_t bits = ctx->count * 8;
    uint8_t pad = 0x80;
    md5_update(ctx, &pad, 1);

    uint8_t zero = 0;
    while ((ctx->count % 64) != 56) {
        md5_update(ctx, &zero, 1);
    }

    /* Append length (little-endian) */
    uint8_t len_bytes[8];
    for (int i = 0; i < 8; i++) {
        len_bytes[i] = (uint8_t)(bits >> (i * 8));
    }
    md5_update(ctx, len_bytes, 8);

    /* Output (little-endian) */
    for (int i = 0; i < 4; i++) {
        digest[i * 4]     = (uint8_t)(ctx->state[i]);
        digest[i * 4 + 1] = (uint8_t)(ctx->state[i] >> 8);
        digest[i * 4 + 2] = (uint8_t)(ctx->state[i] >> 16);
        digest[i * 4 + 3] = (uint8_t)(ctx->state[i] >> 24);
    }
}

/* One-shot hash */
__attribute__((noinline))
void md5_hash(const void *data, size_t len, uint8_t digest[16]) {
    md5_ctx_t ctx;
    md5_init(&ctx);
    md5_update(&ctx, data, len);
    md5_final(&ctx, digest);
}

/* Convert to hex string */
void md5_to_hex(const uint8_t digest[16], char hex[33]) {
    static const char hexchars[] = "0123456789abcdef";
    for (int i = 0; i < 16; i++) {
        hex[i * 2]     = hexchars[digest[i] >> 4];
        hex[i * 2 + 1] = hexchars[digest[i] & 0x0f];
    }
    hex[32] = '\0';
}

/* Stress test: hash repeatedly */
__attribute__((noinline))
void md5_stress(const void *data, size_t len, int iterations, uint8_t digest[16]) {
    md5_hash(data, len, digest);
    for (int i = 1; i < iterations; i++) {
        md5_hash(digest, 16, digest);
    }
}
