#ifndef MATRIXLAB_MEMORY_POOL_H
#define MATRIXLAB_MEMORY_POOL_H

#include <stddef.h>
#include <stdint.h>

/* Fixed-size block memory pool */
typedef struct mem_pool mem_pool_t;

/* Opaque pool handle */
typedef void *pool_handle_t;

/* Create a memory pool with given block size and count */
mem_pool_t *mempool_create(size_t block_size, size_t block_count);

/* Destroy a memory pool */
void mempool_destroy(mem_pool_t *pool);

/* Allocate a block from the pool */
void *mempool_alloc(mem_pool_t *pool);

/* Free a block back to the pool */
void mempool_free(mem_pool_t *pool, void *ptr);

/* Get pool statistics */
size_t mempool_used_blocks(const mem_pool_t *pool);
size_t mempool_free_blocks(const mem_pool_t *pool);
size_t mempool_total_blocks(const mem_pool_t *pool);

/* Stress the pool with random alloc/free patterns */
void mempool_stress_test(mem_pool_t *pool, int iterations);

/* Defragmentation simulation */
void mempool_defrag_simulate(mem_pool_t *pool);

/* Walk all allocated blocks with callback */
typedef void (*mempool_walk_fn)(void *block, size_t size, void *userdata);
void mempool_walk(mem_pool_t *pool, mempool_walk_fn fn, void *userdata);

#endif
