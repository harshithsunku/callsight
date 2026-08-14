#include "arena.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

/* Arena chunk for linked list of allocations */
typedef struct arena_chunk {
    struct arena_chunk *next;
    size_t capacity;
    size_t used;
    uint8_t data[];
} arena_chunk_t;

/* Arena internals */
struct arena {
    arena_chunk_t *head;      /* Current chunk */
    arena_chunk_t *first;     /* First chunk (for reset) */
    size_t default_cap;       /* Default chunk capacity */
    size_t total_used;        /* Total bytes allocated */
    size_t peak_used;         /* Peak allocation */
};

/* Allocate a new chunk */
static arena_chunk_t *arena_chunk_new(size_t cap) {
    arena_chunk_t *chunk = (arena_chunk_t *)malloc(sizeof(arena_chunk_t) + cap);
    if (!chunk) return NULL;
    chunk->next = NULL;
    chunk->capacity = cap;
    chunk->used = 0;
    return chunk;
}

/* Create an arena with given initial capacity */
arena_t *arena_create(size_t capacity) {
    arena_t *arena = (arena_t *)malloc(sizeof(arena_t));
    if (!arena) return NULL;

    if (capacity < 4096) capacity = 4096;
    arena->default_cap = capacity;
    arena->total_used = 0;
    arena->peak_used = 0;

    arena->first = arena_chunk_new(capacity);
    arena->head = arena->first;

    if (!arena->first) {
        free(arena);
        return NULL;
    }
    return arena;
}

/* Destroy an arena and free all memory */
void arena_destroy(arena_t *arena) {
    if (!arena) return;
    arena_chunk_t *chunk = arena->first;
    while (chunk) {
        arena_chunk_t *next = chunk->next;
        free(chunk);
        chunk = next;
    }
    free(arena);
}

/* Align offset up to alignment boundary */
static inline size_t arena_align_up(size_t val, size_t align) {
    return (val + align - 1) & ~(align - 1);
}

/* Allocate aligned memory from the arena */
void *arena_alloc_aligned(arena_t *arena, size_t size, size_t alignment) {
    if (!arena || size == 0) return NULL;

    arena_chunk_t *chunk = arena->head;
    size_t aligned_offset = arena_align_up(chunk->used, alignment);

    if (aligned_offset + size > chunk->capacity) {
        /* Need a new chunk */
        size_t new_cap = arena->default_cap;
        if (size + alignment > new_cap) new_cap = size + alignment;

        arena_chunk_t *new_chunk = arena_chunk_new(new_cap);
        if (!new_chunk) return NULL;

        chunk->next = new_chunk;
        arena->head = new_chunk;
        chunk = new_chunk;
        aligned_offset = arena_align_up(0, alignment);
    }

    void *ptr = chunk->data + aligned_offset;
    chunk->used = aligned_offset + size;
    arena->total_used += size;

    if (arena->total_used > arena->peak_used)
        arena->peak_used = arena->total_used;

    return ptr;
}

/* Allocate from the arena (8-byte aligned by default) */
void *arena_alloc(arena_t *arena, size_t size) {
    return arena_alloc_aligned(arena, size, 8);
}

/* Reset the arena (free all allocations at once) */
void arena_reset(arena_t *arena) {
    if (!arena) return;

    /* Free all chunks except the first */
    arena_chunk_t *chunk = arena->first->next;
    while (chunk) {
        arena_chunk_t *next = chunk->next;
        free(chunk);
        chunk = next;
    }

    arena->first->next = NULL;
    arena->first->used = 0;
    arena->head = arena->first;
    arena->total_used = 0;
}

/* Get arena usage */
size_t arena_used(const arena_t *arena) {
    return arena ? arena->total_used : 0;
}

/* Get arena capacity */
size_t arena_capacity(const arena_t *arena) {
    if (!arena) return 0;
    size_t total = 0;
    const arena_chunk_t *chunk = arena->first;
    while (chunk) {
        total += chunk->capacity;
        chunk = chunk->next;
    }
    return total;
}

/* Get arena peak usage */
size_t arena_peak(const arena_t *arena) {
    return arena ? arena->peak_used : 0;
}

/* Save current arena position */
arena_savepoint_t arena_save(const arena_t *arena) {
    arena_savepoint_t sp = {0};
    if (arena) sp.saved_offset = arena->total_used;
    return sp;
}

/* Restore arena to saved position (approximate - resets current chunk) */
void arena_restore(arena_t *arena, arena_savepoint_t sp) {
    if (!arena) return;
    /* Simple approach: if we haven't grown past this chunk, just rewind */
    if (sp.saved_offset < arena->total_used) {
        arena->head->used = 0;
        arena->total_used = sp.saved_offset;
    }
}

/* Arena stress: allocate many small varied blocks */
void arena_stress_varied(arena_t *arena, int count) {
    if (!arena) return;

    static const size_t sizes[] = {8, 16, 32, 64, 128, 256, 512, 1024, 7, 13, 37, 97};
    int nsizes = (int)(sizeof(sizes) / sizeof(sizes[0]));

    for (int i = 0; i < count; i++) {
        size_t sz = sizes[i % nsizes];
        void *p = arena_alloc(arena, sz);
        if (p) {
            memset(p, (int)(i & 0xFF), sz);
        }
    }
}
