#ifndef MATRIXLAB_HMAC_H
#define MATRIXLAB_HMAC_H

#include <stdint.h>
#include <stddef.h>

/* HMAC-SHA256 */
__attribute__((noinline))
void hmac_sha256(const void *key, size_t key_len,
                  const void *data, size_t data_len,
                  uint8_t mac[32]);

/* HMAC-MD5 */
__attribute__((noinline))
void hmac_md5(const void *key, size_t key_len,
               const void *data, size_t data_len,
               uint8_t mac[16]);

/* PBKDF2-HMAC-SHA256 (key derivation) */
__attribute__((noinline))
void pbkdf2_sha256(const void *password, size_t pass_len,
                    const void *salt, size_t salt_len,
                    int iterations, uint8_t *output, size_t output_len);

/* HMAC stress test */
__attribute__((noinline))
void hmac_stress(const void *data, size_t len, int iterations);

#endif
