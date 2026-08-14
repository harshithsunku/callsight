#ifndef MATRIXLAB_RLE_H
#define MATRIXLAB_RLE_H

#include <stddef.h>
#include <stdint.h>

/* RLE encode: data -> output, returns encoded size */
__attribute__((noinline))
size_t rle_encode(const uint8_t *data, size_t len, uint8_t *output, size_t max_out);

/* RLE decode: data -> output, returns decoded size */
__attribute__((noinline))
size_t rle_decode(const uint8_t *data, size_t len, uint8_t *output, size_t max_out);

/* RLE encode with escape byte for mixed data */
__attribute__((noinline))
size_t rle_encode_escape(const uint8_t *data, size_t len, uint8_t *output, size_t max_out);

/* RLE stress test: encode/decode random data */
__attribute__((noinline))
void rle_stress_test(int iterations, int data_size);

/* Pack bits (8 booleans -> 1 byte) */
__attribute__((noinline))
size_t rle_pack_bits(const uint8_t *bools, size_t n, uint8_t *output);

/* Unpack bits */
__attribute__((noinline))
size_t rle_unpack_bits(const uint8_t *packed, size_t nbytes, uint8_t *output, size_t max_out);

#endif
