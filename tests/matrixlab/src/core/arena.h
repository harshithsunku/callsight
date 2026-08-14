#ifndef MATRIXLAB_ARENA_H
#define MATRIXLAB_ARENA_H

#include <stddef.h>
#include <stdint.h>

/* Arena (bump) allocator for fast sequential allocations */
typedef struct arena arena_t;

/* Create an arena with given initial capacity */
arena_t *arena_create(size_t capacity);

/* Destroy an arena and free all memory */
void arena_destroy(arena_t *arena);

/* Allocate from the arena (never individually freed) */
void *arena_alloc(arena_t *arena, size_t size);

/* Allocate aligned memory from the arena */
void *arena_alloc_aligned(arena_t *arena, size_t size, size_t alignment);

/* Reset the arena (free all allocations at once) */
void arena_reset(arena_t *arena);

/* Get arena usage statistics */
size_t arena_used(const arena_t *arena);
size_t arena_capacity(const arena_t *arena);
size_t arena_peak(const arena_t *arena);

/* Temporary arena scope - save/restore position */
typedef struct {
    size_t saved_offset;
} arena_savepoint_t;

arena_savepoint_t arena_save(const arena_t *arena);
void arena_restore(arena_t *arena, arena_savepoint_t sp);

/* Arena stress: allocate many small varied blocks */
void arena_stress_varied(arena_t *arena, int count);

#endif
