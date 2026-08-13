#include "memory_pool.h"
#include "../utils/rng.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <pthread.h>

/* Memory pool internals */
struct mem_pool {
    uint8_t *memory;          /* Raw memory block */
    uint8_t *bitmap;          /* Allocation bitmap */
    size_t block_size;        /* Size of each block */
    size_t block_count;       /* Total blocks */
    size_t used;              /* Currently allocated */
    size_t high_water;        /* Peak allocation */
    pthread_mutex_t lock;     /* Thread safety */
    volatile int active;      /* Pool is active */
};

/* Create a memory pool with given block size and count */
mem_pool_t *mempool_create(size_t block_size, size_t block_count) {
    mem_pool_t *pool = (mem_pool_t *)malloc(sizeof(mem_pool_t));
    if (!pool) return NULL;

    pool->block_size = block_size < 8 ? 8 : block_size;
    pool->block_count = block_count;
    pool->used = 0;
    pool->high_water = 0;
    pool->active = 1;

    pool->memory = (uint8_t *)malloc(pool->block_size * pool->block_count);
    pool->bitmap = (uint8_t *)calloc(pool->block_count, 1);

    if (!pool->memory || !pool->bitmap) {
        free(pool->memory);
        free(pool->bitmap);
        free(pool);
        return NULL;
    }

    /* Touch all pages to fault them in */
    memset(pool->memory, 0, pool->block_size * pool->block_count);
    pthread_mutex_init(&pool->lock, NULL);
    return pool;
}

/* Destroy a memory pool */
void mempool_destroy(mem_pool_t *pool) {
    if (!pool) return;
    pool->active = 0;
    pthread_mutex_lock(&pool->lock);
    free(pool->memory);
    free(pool->bitmap);
    pthread_mutex_unlock(&pool->lock);
    pthread_mutex_destroy(&pool->lock);
    free(pool);
}

/* Find a free block using linear scan */
static int mempool_find_free(const mem_pool_t *pool) {
    for (size_t i = 0; i < pool->block_count; i++) {
        if (!pool->bitmap[i]) return (int)i;
    }
    return -1;
}

/* Allocate a block from the pool */
void *mempool_alloc(mem_pool_t *pool) {
    if (!pool || !pool->active) return NULL;

    pthread_mutex_lock(&pool->lock);
    int idx = mempool_find_free(pool);
    if (idx < 0) {
        pthread_mutex_unlock(&pool->lock);
        return NULL;
    }

    pool->bitmap[idx] = 1;
    pool->used++;
    if (pool->used > pool->high_water)
        pool->high_water = pool->used;

    void *ptr = pool->memory + ((size_t)idx * pool->block_size);
    pthread_mutex_unlock(&pool->lock);
    return ptr;
}

/* Free a block back to the pool */
void mempool_free(mem_pool_t *pool, void *ptr) {
    if (!pool || !ptr) return;

    pthread_mutex_lock(&pool->lock);
    ptrdiff_t offset = (uint8_t *)ptr - pool->memory;
    if (offset < 0 || (size_t)offset >= pool->block_size * pool->block_count) {
        pthread_mutex_unlock(&pool->lock);
        return;
    }

    size_t idx = (size_t)offset / pool->block_size;
    if (idx < pool->block_count && pool->bitmap[idx]) {
        pool->bitmap[idx] = 0;
        pool->used--;
    }
    pthread_mutex_unlock(&pool->lock);
}

/* Get pool statistics */
size_t mempool_used_blocks(const mem_pool_t *pool) {
    return pool ? pool->used : 0;
}

/* Get free blocks */
size_t mempool_free_blocks(const mem_pool_t *pool) {
    return pool ? pool->block_count - pool->used : 0;
}

/* Get total blocks */
size_t mempool_total_blocks(const mem_pool_t *pool) {
    return pool ? pool->block_count : 0;
}

/* Stress the pool with random alloc/free patterns */
void mempool_stress_test(mem_pool_t *pool, int iterations) {
    if (!pool) return;

    void **ptrs = (void **)calloc((size_t)iterations, sizeof(void *));
    if (!ptrs) return;

    int count = 0;
    for (int i = 0; i < iterations; i++) {
        if (rng_next_u32() % 3 != 0 && count < iterations) {
            /* Allocate */
            ptrs[count] = mempool_alloc(pool);
            if (ptrs[count]) {
                memset(ptrs[count], (int)(i & 0xFF), pool->block_size);
                count++;
            }
        } else if (count > 0) {
            /* Free random block */
            int idx = (int)(rng_next_u32() % (uint32_t)count);
            mempool_free(pool, ptrs[idx]);
            ptrs[idx] = ptrs[count - 1];
            count--;
        }
    }

    /* Cleanup remaining */
    for (int i = 0; i < count; i++) {
        mempool_free(pool, ptrs[i]);
    }
    free(ptrs);
}

/* Defragmentation simulation - repack allocated blocks */
void mempool_defrag_simulate(mem_pool_t *pool) {
    if (!pool) return;

    pthread_mutex_lock(&pool->lock);
    size_t dst = 0;
    for (size_t src = 0; src < pool->block_count; src++) {
        if (pool->bitmap[src]) {
            if (dst != src) {
                memcpy(pool->memory + dst * pool->block_size,
                       pool->memory + src * pool->block_size,
                       pool->block_size);
                pool->bitmap[dst] = 1;
                pool->bitmap[src] = 0;
            }
            dst++;
        }
    }
    pthread_mutex_unlock(&pool->lock);
}

/* Walk all allocated blocks with callback */
void mempool_walk(mem_pool_t *pool, mempool_walk_fn fn, void *userdata) {
    if (!pool || !fn) return;

    pthread_mutex_lock(&pool->lock);
    for (size_t i = 0; i < pool->block_count; i++) {
        if (pool->bitmap[i]) {
            fn(pool->memory + i * pool->block_size, pool->block_size, userdata);
        }
    }
    pthread_mutex_unlock(&pool->lock);
}
