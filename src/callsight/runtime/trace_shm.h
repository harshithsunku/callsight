#ifndef CALLSIGHT_TRACE_SHM_H
#define CALLSIGHT_TRACE_SHM_H

/*
 * Shared-memory ring layout and stream wire protocol for callsight
 * remote tracing.
 *
 * Device side: the traced process (trace.c, TRACE_SHM=/name) flushes
 * events into a POSIX shared-memory ring instead of files — no disk, no
 * network in the profiled process. A separate client process
 * (trace_stream.c) maps the same ring, ZSTD-compresses event batches and
 * streams them over raw TCP to a callsight server (`callsight serve`).
 *
 * This header is shared by both sides; the attach helper is static so each
 * program carries its own copy (drop-in design, no link dependency).
 *
 * Ring layout: one header followed by `capacity` bytes of event storage.
 * head/tail are monotonic byte counters; used = head - tail. When the ring
 * is full the tracer DROPS events and counts them in `dropped` — profiling
 * must never stall the workload. A single spinlock serializes writers and
 * the reader; critical sections are short memcpys of buffered batches.
 *
 * Wire protocol (client -> server, TCP):
 *   magic[8] "TKSTREAM", u32 version, u32 event_size
 *   then chunks: u32 type, u32 raw_len, u32 zstd_len, payload
 *     type 0: events  (payload decompresses to raw_len bytes of events)
 *     type 1: notice  (payload decompresses to a u64 dropped-event count)
 * All integers little-endian.
 */

#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define TRACE_SHM_MAGIC      "TKSHM\0\0\0"
#define TRACE_SHM_VERSION    1u
#define TRACE_SHM_DEF_SIZE   (16u * 1024u * 1024u)

#define TRACE_STREAM_MAGIC   "TKSTREAM"
#define TRACE_STREAM_VERSION 1u

#define TRACE_STREAM_CHUNK_EVENTS 0u
#define TRACE_STREAM_CHUNK_NOTICE 1u

typedef struct {
    char                magic[8];    /* TRACE_SHM_MAGIC */
    uint32_t            version;     /* TRACE_SHM_VERSION */
    uint32_t            capacity;    /* ring bytes after this header */
    volatile uint32_t   writers;     /* tracer processes attached */
    volatile uint32_t   lock;        /* spinlock: 0 = free */
    volatile uint64_t   head;        /* monotonic write offset (bytes) */
    volatile uint64_t   tail;        /* monotonic read offset (bytes) */
    volatile uint64_t   dropped;     /* events dropped (ring was full) */
    uint8_t             _pad[16];
} trace_shm_header_t;

/* Total mapping size for a ring of `capacity` bytes. */
static inline uint64_t trace_shm_total(uint32_t capacity) {
    return (uint64_t)sizeof(trace_shm_header_t) + capacity;
}

/* Byte-level spinlock: short batch copies only, never held across I/O. */
static inline void trace_shm_lock(trace_shm_header_t *h) {
    while (__sync_lock_test_and_set(&h->lock, 1))
        ;
}
static inline void trace_shm_unlock(trace_shm_header_t *h) {
    __sync_lock_release(&h->lock);
}

/*
 * Open or create the ring `name` (POSIX shm name, e.g. "/callsight0") with
 * room for `capacity` event bytes. Returns the mapped header, or NULL on
 * failure (caller decides the fallback). If this call creates the segment,
 * it initializes the header; if it already exists, capacity is taken from
 * the existing header.
 */
static trace_shm_header_t *trace_shm_attach(const char *name,
                                            uint32_t capacity) {
    int created = 0;
    int fd = shm_open(name, O_RDWR, 0600);
    if (fd < 0 && errno == ENOENT) {
        fd = shm_open(name, O_RDWR | O_CREAT | O_EXCL, 0600);
        created = (fd >= 0);
    }
    if (fd < 0)
        return NULL;

    if (created &&
        ftruncate(fd, (off_t)trace_shm_total(capacity)) != 0) {
        close(fd);
        shm_unlink(name);
        return NULL;
    }

    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size == 0) {
        /* Lost a creation race: someone else is mid-ftruncate. Retry once. */
        close(fd);
        usleep(1000);
        fd = shm_open(name, O_RDWR, 0600);
        if (fd < 0 || fstat(fd, &st) != 0 || st.st_size == 0) {
            if (fd >= 0) close(fd);
            return NULL;
        }
    }

    void *map = mmap(NULL, (size_t)st.st_size, PROT_READ | PROT_WRITE,
                     MAP_SHARED, fd, 0);
    close(fd);
    if (map == MAP_FAILED)
        return NULL;

    trace_shm_header_t *h = (trace_shm_header_t *)map;
    if (created) {
        memset(h, 0, sizeof(*h));
        memcpy(h->magic, TRACE_SHM_MAGIC, sizeof(h->magic));
        h->version = TRACE_SHM_VERSION;
        h->capacity = capacity;
    } else if (memcmp(h->magic, TRACE_SHM_MAGIC, sizeof(h->magic)) != 0 ||
               h->version != TRACE_SHM_VERSION) {
        munmap(map, (size_t)st.st_size);
        fprintf(stderr, "callsight: shm %s has incompatible layout\n", name);
        return NULL;
    }
    return h;
}

/* Ring byte area follows the header. */
static inline uint8_t *trace_shm_ring(trace_shm_header_t *h) {
    return (uint8_t *)(h + 1);
}

/*
 * Copy `len` bytes into the ring at the current head (wrapping), without
 * bounds checks — callers hold the lock and have verified space. Returns
 * the advanced head.
 */
static inline uint64_t trace_shm_put(trace_shm_header_t *h, const void *src,
                                     uint64_t len) {
    uint8_t *ring = trace_shm_ring(h);
    uint64_t off = h->head % h->capacity;
    uint64_t first = h->capacity - off;
    if (first > len)
        first = len;
    memcpy(ring + off, src, (size_t)first);
    if (first < len)
        memcpy(ring, (const uint8_t *)src + first, (size_t)(len - first));
    return h->head + len;
}

/* Read-side counterpart of trace_shm_put: copy out of the ring at tail. */
static inline uint64_t trace_shm_get(trace_shm_header_t *h, void *dst,
                                     uint64_t len) {
    const uint8_t *ring = trace_shm_ring(h);
    uint64_t off = h->tail % h->capacity;
    uint64_t first = h->capacity - off;
    if (first > len)
        first = len;
    memcpy(dst, ring + off, (size_t)first);
    if (first < len)
        memcpy((uint8_t *)dst + first, ring, (size_t)(len - first));
    return h->tail + len;
}

#endif /* CALLSIGHT_TRACE_SHM_H */
