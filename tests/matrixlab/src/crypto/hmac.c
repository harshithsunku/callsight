#include "hmac.h"
#include "sha256.h"
#include "md5.h"
#include <string.h>
#include <stdlib.h>

/* HMAC-SHA256 implementation */
__attribute__((noinline))
void hmac_sha256(const void *key, size_t key_len,
                  const void *data, size_t data_len,
                  uint8_t mac[32]) {
    uint8_t k_pad[64];
    uint8_t k_ipad[64];
    uint8_t k_opad[64];

    /* If key is longer than block size, hash it first */
    if (key_len > 64) {
        sha256_hash(key, key_len, k_pad);
        memset(k_pad + 32, 0, 32);
    } else {
        memcpy(k_pad, key, key_len);
        memset(k_pad + key_len, 0, 64 - key_len);
    }

    /* XOR with ipad and opad */
    for (int i = 0; i < 64; i++) {
        k_ipad[i] = k_pad[i] ^ 0x36;
        k_opad[i] = k_pad[i] ^ 0x5c;
    }

    /* Inner hash: H(K ^ ipad || data) */
    sha256_ctx_t ctx;
    sha256_init(&ctx);
    sha256_update(&ctx, k_ipad, 64);
    sha256_update(&ctx, data, data_len);
    uint8_t inner[32];
    sha256_final(&ctx, inner);

    /* Outer hash: H(K ^ opad || inner) */
    sha256_init(&ctx);
    sha256_update(&ctx, k_opad, 64);
    sha256_update(&ctx, inner, 32);
    sha256_final(&ctx, mac);
}

/* HMAC-MD5 implementation */
__attribute__((noinline))
void hmac_md5(const void *key, size_t key_len,
               const void *data, size_t data_len,
               uint8_t mac[16]) {
    uint8_t k_pad[64];
    uint8_t k_ipad[64];
    uint8_t k_opad[64];

    if (key_len > 64) {
        md5_hash(key, key_len, k_pad);
        memset(k_pad + 16, 0, 48);
    } else {
        memcpy(k_pad, key, key_len);
        memset(k_pad + key_len, 0, 64 - key_len);
    }

    for (int i = 0; i < 64; i++) {
        k_ipad[i] = k_pad[i] ^ 0x36;
        k_opad[i] = k_pad[i] ^ 0x5c;
    }

    /* Inner hash */
    md5_ctx_t ctx;
    md5_init(&ctx);
    md5_update(&ctx, k_ipad, 64);
    md5_update(&ctx, data, data_len);
    uint8_t inner[16];
    md5_final(&ctx, inner);

    /* Outer hash */
    md5_init(&ctx);
    md5_update(&ctx, k_opad, 64);
    md5_update(&ctx, inner, 16);
    md5_final(&ctx, mac);
}

/* XOR buffers */
static void xor_buffers(uint8_t *dst, const uint8_t *src, size_t len) {
    for (size_t i = 0; i < len; i++) dst[i] ^= src[i];
}

/* PBKDF2-HMAC-SHA256 */
__attribute__((noinline))
void pbkdf2_sha256(const void *password, size_t pass_len,
                    const void *salt, size_t salt_len,
                    int iterations, uint8_t *output, size_t output_len) {
    uint32_t block_num = 1;
    size_t output_offset = 0;

    while (output_offset < output_len) {
        /* U1 = HMAC(password, salt || INT(block_num)) */
        uint8_t *msg = (uint8_t *)malloc(salt_len + 4);
        if (!msg) return;

        memcpy(msg, salt, salt_len);
        msg[salt_len]     = (uint8_t)(block_num >> 24);
        msg[salt_len + 1] = (uint8_t)(block_num >> 16);
        msg[salt_len + 2] = (uint8_t)(block_num >> 8);
        msg[salt_len + 3] = (uint8_t)(block_num);

        uint8_t u[32], result[32];
        hmac_sha256(password, pass_len, msg, salt_len + 4, u);
        memcpy(result, u, 32);

        /* U2..Uc */
        for (int i = 1; i < iterations; i++) {
            hmac_sha256(password, pass_len, u, 32, u);
            xor_buffers(result, u, 32);
        }

        size_t copy = output_len - output_offset;
        if (copy > 32) copy = 32;
        memcpy(output + output_offset, result, copy);
        output_offset += copy;
        block_num++;
        free(msg);
    }
}

/* HMAC stress test */
__attribute__((noinline))
void hmac_stress(const void *data, size_t len, int iterations) {
    uint8_t mac[32];
    uint8_t key[32];
    memset(key, 0x42, 32);

    for (int i = 0; i < iterations; i++) {
        hmac_sha256(key, 32, data, len, mac);
        /* Use result as next key */
        memcpy(key, mac, 32);
    }
}
