/*
 * trace_stream — on-device streaming client for callsight remote tracing.
 *
 * Maps the shared-memory ring a traced process writes to (trace.c with
 * TRACE_SHM=/name), drains it in batches, ZSTD-compresses each batch and
 * sends it over raw TCP to a `callsight serve` listener. Self-contained:
 * build with the vendored single-file zstd:
 *
 *     cc -O2 -o trace_stream trace_stream.c zstd.c
 *
 * (`callsight init --stream` copies all needed files flat into the target
 * project's callsight/ dir; add -lrt on older glibc for shm_open.)
 *
 * Usage: trace_stream <shm-name> <server-host> <port>
 *   e.g. trace_stream /callsight0 192.168.1.10 9001
 *
 * Exits once a tracer attached and detached (writers 1 -> 0) and the ring
 * is fully drained. If no tracer ever attaches, it waits — Ctrl-C to stop.
 */

#include <arpa/inet.h>
#include <netdb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "zstd.h"
#include "trace.h"       /* trace_event_t (copied flat by `callsight init`) */
#include "trace_shm.h"

/* Drain batch size: big enough to amortize syscalls, small enough to keep
 * ring latency low. 64 KiB raw ~= 2048 events per chunk. */
#define CHUNK_BYTES (64u * 1024u)

static int connect_tcp(const char *host, const char *port) {
    struct addrinfo hints, *res, *rp;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, port, &hints, &res) != 0)
        return -1;
    int fd = -1;
    for (rp = res; rp; rp = rp->ai_next) {
        fd = socket(rp->ai_family, rp->ai_socktype, rp->ai_protocol);
        if (fd >= 0 && connect(fd, rp->ai_addr, rp->ai_addrlen) != 0) {
            close(fd);
            fd = -1;
        }
        if (fd >= 0) break;
    }
    freeaddrinfo(res);
    return fd;
}

/* write() loop: short counts on a stream socket must not lose data. */
static int send_all(int fd, const void *buf, size_t len) {
    const uint8_t *p = buf;
    while (len > 0) {
        ssize_t n = send(fd, p, len, 0);
        if (n <= 0) return -1;
        p += n;
        len -= (size_t)n;
    }
    return 0;
}

static int send_chunk(int fd, ZSTD_CCtx *cctx, uint32_t type,
                      const void *raw, uint32_t raw_len,
                      void *zbuf, size_t zcap) {
    size_t zlen = ZSTD_compressCCtx(cctx, zbuf, zcap, raw, raw_len, 1);
    if (ZSTD_isError(zlen)) {
        fprintf(stderr, "trace_stream: zstd: %s\n", ZSTD_getErrorName(zlen));
        return -1;
    }
    uint32_t hdr[3] = {type, raw_len, (uint32_t)zlen};
    return send_all(fd, hdr, sizeof(hdr)) || send_all(fd, zbuf, zlen);
}

int main(int argc, char **argv) {
    if (argc != 4) {
        fprintf(stderr, "usage: %s <shm-name> <server-host> <port>\n",
                argv[0]);
        return 2;
    }
    const char *shm_name = argv[1];

    trace_shm_header_t *h = trace_shm_attach(shm_name, TRACE_SHM_DEF_SIZE);
    if (!h) {
        fprintf(stderr, "trace_stream: cannot attach shm %s\n", shm_name);
        return 1;
    }

    int fd = connect_tcp(argv[2], argv[3]);
    if (fd < 0) {
        fprintf(stderr, "trace_stream: cannot connect to %s:%s\n",
                argv[2], argv[3]);
        return 1;
    }

    /* stream header: magic, version, event record size */
    struct {
        char     magic[8];
        uint32_t version;
        uint32_t event_size;
    } sheader;
    memcpy(sheader.magic, TRACE_STREAM_MAGIC, sizeof(sheader.magic));
    sheader.version = TRACE_STREAM_VERSION;
    sheader.event_size = (uint32_t)sizeof(trace_event_t);
    if (send_all(fd, &sheader, sizeof(sheader)) != 0) {
        fprintf(stderr, "trace_stream: server closed during handshake\n");
        return 1;
    }

    ZSTD_CCtx *cctx = ZSTD_createCCtx();
    void *raw = malloc(CHUNK_BYTES);
    void *zbuf = malloc(ZSTD_compressBound(CHUNK_BYTES));
    if (!cctx || !raw || !zbuf) {
        fprintf(stderr, "trace_stream: out of memory\n");
        return 1;
    }

    int saw_writer = (h->writers > 0);
    uint64_t last_dropped = 0;
    uint64_t total_sent = 0;

    for (;;) {
        uint64_t avail;
        trace_shm_lock(h);
        avail = h->head - h->tail;
        if (avail > CHUNK_BYTES)
            avail = CHUNK_BYTES;
        if (avail > 0)
            h->tail = trace_shm_get(h, raw, avail);
        trace_shm_unlock(h);

        if (avail > 0) {
            if (send_chunk(fd, cctx, TRACE_STREAM_CHUNK_EVENTS,
                           raw, (uint32_t)avail, zbuf,
                           ZSTD_compressBound(CHUNK_BYTES)) != 0) {
                fprintf(stderr, "trace_stream: send failed, %llu events "
                        "forwarded before disconnect\n",
                        (unsigned long long)(total_sent / sizeof(trace_event_t)));
                return 1;
            }
            total_sent += avail;
        }

        uint64_t dropped = h->dropped;
        if (dropped != last_dropped) {
            send_chunk(fd, cctx, TRACE_STREAM_CHUNK_NOTICE,
                       &dropped, sizeof(dropped), zbuf,
                       ZSTD_compressBound(CHUNK_BYTES));
            last_dropped = dropped;
        }

        if (h->writers > 0)
            saw_writer = 1;
        if (saw_writer && h->writers == 0 && avail == 0 && h->head == h->tail)
            break;  /* tracer exited, ring drained */

        if (avail == 0)
            usleep(1000);  /* idle poll: 1ms keeps latency and CPU low */
    }

    close(fd);
    printf("trace_stream: done, %llu events forwarded, %llu dropped\n",
           (unsigned long long)(total_sent / sizeof(trace_event_t)),
           (unsigned long long)h->dropped);
    return 0;
}
