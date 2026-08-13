#include "rle.h"
#include "../utils/rng.h"
#include <stdlib.h>
#include <string.h>

/* Simple RLE encode: pairs of (count, byte) */
__attribute__((noinline))
size_t rle_encode(const uint8_t *data, size_t len, uint8_t *output, size_t max_out) {
    size_t out_idx = 0;
    size_t i = 0;

    while (i < len && out_idx + 2 <= max_out) {
        uint8_t current = data[i];
        uint8_t count = 1;

        while (i + count < len && data[i + count] == current && count < 255) {
            count++;
        }

        output[out_idx++] = count;
        output[out_idx++] = current;
        i += count;
    }
    return out_idx;
}

/* Simple RLE decode */
__attribute__((noinline))
size_t rle_decode(const uint8_t *data, size_t len, uint8_t *output, size_t max_out) {
    size_t out_idx = 0;
    size_t i = 0;

    while (i + 1 < len) {
        uint8_t count = data[i];
        uint8_t byte = data[i + 1];
        i += 2;

        for (uint8_t j = 0; j < count && out_idx < max_out; j++) {
            output[out_idx++] = byte;
        }
    }
    return out_idx;
}

/* RLE with escape byte (0xFF) for non-repeating data */
__attribute__((noinline))
size_t rle_encode_escape(const uint8_t *data, size_t len, uint8_t *output, size_t max_out) {
    size_t out_idx = 0;
    size_t i = 0;

    while (i < len && out_idx < max_out) {
        uint8_t current = data[i];
        size_t run = 1;

        while (i + run < len && data[i + run] == current && run < 255) {
            run++;
        }

        if (run >= 3 || current == 0xFF) {
            /* Encode as run */
            if (out_idx + 3 > max_out) break;
            output[out_idx++] = 0xFF; /* escape */
            output[out_idx++] = (uint8_t)run;
            output[out_idx++] = current;
        } else {
            /* Literal */
            for (size_t j = 0; j < run && out_idx < max_out; j++) {
                output[out_idx++] = data[i + j];
            }
        }
        i += run;
    }
    return out_idx;
}

/* RLE stress test */
__attribute__((noinline))
void rle_stress_test(int iterations, int data_size) {
    uint8_t *data = (uint8_t *)malloc((size_t)data_size);
    uint8_t *encoded = (uint8_t *)malloc((size_t)data_size * 2);
    uint8_t *decoded = (uint8_t *)malloc((size_t)data_size);
    if (!data || !encoded || !decoded) {
        free(data); free(encoded); free(decoded);
        return;
    }

    for (int iter = 0; iter < iterations; iter++) {
        /* Generate data with variable run lengths */
        int i = 0;
        while (i < data_size) {
            uint8_t byte = (uint8_t)(rng_next_u32() & 0xFF);
            int run = rng_next_int(1, 20);
            for (int j = 0; j < run && i < data_size; j++) {
                data[i++] = byte;
            }
        }

        /* Encode */
        size_t enc_size = rle_encode(data, (size_t)data_size, encoded, (size_t)data_size * 2);

        /* Decode */
        size_t dec_size = rle_decode(encoded, enc_size, decoded, (size_t)data_size);
        (void)dec_size;
    }

    free(data); free(encoded); free(decoded);
}

/* Pack booleans to bits */
__attribute__((noinline))
size_t rle_pack_bits(const uint8_t *bools, size_t n, uint8_t *output) {
    size_t nbytes = (n + 7) / 8;
    memset(output, 0, nbytes);
    for (size_t i = 0; i < n; i++) {
        if (bools[i]) {
            output[i / 8] |= (uint8_t)(1 << (i % 8));
        }
    }
    return nbytes;
}

/* Unpack bits to booleans */
__attribute__((noinline))
size_t rle_unpack_bits(const uint8_t *packed, size_t nbytes, uint8_t *output, size_t max_out) {
    size_t count = 0;
    for (size_t i = 0; i < nbytes && count < max_out; i++) {
        for (int bit = 0; bit < 8 && count < max_out; bit++) {
            output[count++] = (packed[i] >> bit) & 1;
        }
    }
    return count;
}
