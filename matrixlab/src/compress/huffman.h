#ifndef MATRIXLAB_HUFFMAN_H
#define MATRIXLAB_HUFFMAN_H

#include <stddef.h>
#include <stdint.h>

/* Huffman tree node */
typedef struct huffman_node {
    uint8_t byte;
    int freq;
    struct huffman_node *left;
    struct huffman_node *right;
} huffman_node_t;

/* Huffman code table entry */
typedef struct {
    uint32_t code;
    int bits;
} huffman_code_t;

/* Build frequency table from data */
__attribute__((noinline))
void huffman_build_freq(const uint8_t *data, size_t len, int freq[256]);

/* Build Huffman tree from frequency table */
__attribute__((noinline))
huffman_node_t *huffman_build_tree(const int freq[256]);

/* Destroy Huffman tree */
void huffman_destroy_tree(huffman_node_t *root);

/* Generate code table from tree */
__attribute__((noinline))
void huffman_generate_codes(const huffman_node_t *root, huffman_code_t codes[256]);

/* Encode data using Huffman codes */
__attribute__((noinline))
size_t huffman_encode(const uint8_t *data, size_t len,
                       const huffman_code_t codes[256],
                       uint8_t *output, size_t max_out);

/* Decode data using Huffman tree */
__attribute__((noinline))
size_t huffman_decode(const uint8_t *encoded, size_t enc_bits,
                       const huffman_node_t *root,
                       uint8_t *output, size_t max_out);

/* Full encode/decode stress test */
__attribute__((noinline))
void huffman_stress_test(int iterations, int data_size);

/* Tree depth (for profiling deep recursion) */
__attribute__((noinline))
int huffman_tree_depth(const huffman_node_t *root);

#endif
