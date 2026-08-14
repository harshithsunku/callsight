#include "huffman.h"
#include "../utils/rng.h"
#include <stdlib.h>
#include <string.h>

/* Build frequency table */
__attribute__((noinline))
void huffman_build_freq(const uint8_t *data, size_t len, int freq[256]) {
    memset(freq, 0, 256 * sizeof(int));
    for (size_t i = 0; i < len; i++) {
        freq[data[i]]++;
    }
}

/* Create a new tree node */
static huffman_node_t *huffman_new_node(uint8_t byte, int freq) {
    huffman_node_t *node = (huffman_node_t *)malloc(sizeof(huffman_node_t));
    if (!node) return NULL;
    node->byte = byte;
    node->freq = freq;
    node->left = NULL;
    node->right = NULL;
    return node;
}

/* Min-heap for building Huffman tree */
typedef struct {
    huffman_node_t **nodes;
    int size;
    int capacity;
} node_heap_t;

/* Heap sift up */
static void nh_sift_up(node_heap_t *h, int i) {
    while (i > 0) {
        int parent = (i - 1) / 2;
        if (h->nodes[i]->freq < h->nodes[parent]->freq) {
            huffman_node_t *tmp = h->nodes[i];
            h->nodes[i] = h->nodes[parent];
            h->nodes[parent] = tmp;
            i = parent;
        } else break;
    }
}

/* Heap sift down */
static void nh_sift_down(node_heap_t *h, int i) {
    while (1) {
        int smallest = i;
        int left = 2 * i + 1, right = 2 * i + 2;
        if (left < h->size && h->nodes[left]->freq < h->nodes[smallest]->freq) smallest = left;
        if (right < h->size && h->nodes[right]->freq < h->nodes[smallest]->freq) smallest = right;
        if (smallest == i) break;
        huffman_node_t *tmp = h->nodes[i];
        h->nodes[i] = h->nodes[smallest];
        h->nodes[smallest] = tmp;
        i = smallest;
    }
}

/* Build Huffman tree from frequency table */
__attribute__((noinline))
huffman_node_t *huffman_build_tree(const int freq[256]) {
    node_heap_t heap;
    heap.capacity = 256;
    heap.size = 0;
    heap.nodes = (huffman_node_t **)malloc(256 * sizeof(huffman_node_t *));
    if (!heap.nodes) return NULL;

    /* Add all non-zero frequency bytes */
    for (int i = 0; i < 256; i++) {
        if (freq[i] > 0) {
            huffman_node_t *node = huffman_new_node((uint8_t)i, freq[i]);
            if (!node) { free(heap.nodes); return NULL; }
            heap.nodes[heap.size] = node;
            nh_sift_up(&heap, heap.size);
            heap.size++;
        }
    }

    if (heap.size == 0) { free(heap.nodes); return NULL; }
    if (heap.size == 1) {
        /* Single symbol: create a parent */
        huffman_node_t *parent = huffman_new_node(0, heap.nodes[0]->freq);
        if (parent) { parent->left = heap.nodes[0]; }
        free(heap.nodes);
        return parent;
    }

    /* Build tree by combining two smallest nodes */
    while (heap.size > 1) {
        /* Extract two minimums */
        huffman_node_t *left = heap.nodes[0];
        heap.nodes[0] = heap.nodes[--heap.size];
        nh_sift_down(&heap, 0);

        huffman_node_t *right = heap.nodes[0];
        heap.nodes[0] = heap.nodes[--heap.size];
        nh_sift_down(&heap, 0);

        /* Create parent */
        huffman_node_t *parent = huffman_new_node(0, left->freq + right->freq);
        if (!parent) break;
        parent->left = left;
        parent->right = right;

        /* Insert parent */
        heap.nodes[heap.size] = parent;
        nh_sift_up(&heap, heap.size);
        heap.size++;
    }

    huffman_node_t *root = heap.size > 0 ? heap.nodes[0] : NULL;
    free(heap.nodes);
    return root;
}

/* Destroy Huffman tree recursively */
void huffman_destroy_tree(huffman_node_t *root) {
    if (!root) return;
    huffman_destroy_tree(root->left);
    huffman_destroy_tree(root->right);
    free(root);
}

/* Recursive code generation */
__attribute__((noinline))
static void gen_codes_recursive(const huffman_node_t *node, huffman_code_t codes[256],
                                  uint32_t code, int depth) {
    if (!node) return;

    if (!node->left && !node->right) {
        /* Leaf node */
        codes[node->byte].code = code;
        codes[node->byte].bits = depth > 0 ? depth : 1;
        return;
    }

    gen_codes_recursive(node->left, codes, code << 1, depth + 1);
    gen_codes_recursive(node->right, codes, (code << 1) | 1, depth + 1);
}

/* Generate code table */
__attribute__((noinline))
void huffman_generate_codes(const huffman_node_t *root, huffman_code_t codes[256]) {
    memset(codes, 0, 256 * sizeof(huffman_code_t));
    gen_codes_recursive(root, codes, 0, 0);
}

/* Encode data */
__attribute__((noinline))
size_t huffman_encode(const uint8_t *data, size_t len,
                       const huffman_code_t codes[256],
                       uint8_t *output, size_t max_out) {
    memset(output, 0, max_out);
    size_t bit_pos = 0;

    for (size_t i = 0; i < len; i++) {
        const huffman_code_t *c = &codes[data[i]];
        for (int b = c->bits - 1; b >= 0; b--) {
            if (bit_pos / 8 >= max_out) return bit_pos;
            if (c->code & (1u << (unsigned)b)) {
                output[bit_pos / 8] |= (uint8_t)(1 << (7 - (bit_pos % 8)));
            }
            bit_pos++;
        }
    }
    return bit_pos;
}

/* Decode data */
__attribute__((noinline))
size_t huffman_decode(const uint8_t *encoded, size_t enc_bits,
                       const huffman_node_t *root,
                       uint8_t *output, size_t max_out) {
    size_t out_idx = 0;
    const huffman_node_t *node = root;

    for (size_t bit = 0; bit < enc_bits && out_idx < max_out; bit++) {
        int b = (encoded[bit / 8] >> (7 - (bit % 8))) & 1;
        node = b ? node->right : node->left;

        if (!node) { node = root; continue; }

        if (!node->left && !node->right) {
            output[out_idx++] = node->byte;
            node = root;
        }
    }
    return out_idx;
}

/* Full Huffman stress test */
__attribute__((noinline))
void huffman_stress_test(int iterations, int data_size) {
    uint8_t *data = (uint8_t *)malloc((size_t)data_size);
    uint8_t *encoded = (uint8_t *)malloc((size_t)data_size * 2);
    uint8_t *decoded = (uint8_t *)malloc((size_t)data_size);
    if (!data || !encoded || !decoded) {
        free(data); free(encoded); free(decoded);
        return;
    }

    for (int iter = 0; iter < iterations; iter++) {
        /* Generate data with non-uniform distribution */
        for (int i = 0; i < data_size; i++) {
            double r = rng_next_double();
            if (r < 0.3) data[i] = 'a';
            else if (r < 0.5) data[i] = 'b';
            else if (r < 0.65) data[i] = 'c';
            else data[i] = (uint8_t)(rng_next_u32() % 256);
        }

        /* Build tree */
        int freq[256];
        huffman_build_freq(data, (size_t)data_size, freq);
        huffman_node_t *tree = huffman_build_tree(freq);
        if (!tree) continue;

        /* Generate codes */
        huffman_code_t codes[256];
        huffman_generate_codes(tree, codes);

        /* Encode */
        size_t enc_bits = huffman_encode(data, (size_t)data_size, codes,
                                          encoded, (size_t)data_size * 2);

        /* Decode */
        size_t dec_size = huffman_decode(encoded, enc_bits, tree,
                                          decoded, (size_t)data_size);
        (void)dec_size;

        huffman_destroy_tree(tree);
    }

    free(data); free(encoded); free(decoded);
}

/* Tree depth (recursive) */
__attribute__((noinline))
int huffman_tree_depth(const huffman_node_t *root) {
    if (!root) return 0;
    int left = huffman_tree_depth(root->left);
    int right = huffman_tree_depth(root->right);
    return 1 + (left > right ? left : right);
}
