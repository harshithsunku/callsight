# Remote streaming

On a constrained device you cannot accumulate trace files — a busy program
generates millions of events per second. Streaming mode keeps **nothing**
on the device: the runtime flushes into a shared-memory ring, and a tiny
on-device client forwards events ZSTD-compressed over raw TCP.

## Topology

```
traced process                 device client                analysis host
──────────────                ──────────────                ─────────────
trace.c hooks ──► shm ring ──► trace_stream ──zstd/TCP──► callsight serve
(TRACE_SHM=/name)   (no disk,               (compression)      │ writes
                     no network,                              ▼
                     drop-counted)                  trace.stream.*.bin
                                                    → analyze / web UI
```

## Setup

```sh
# analysis host (needs the stream extra):
uv tool install 'callsight[stream]'
callsight serve --port 9001 --out traces/

# adopt with streaming support:
callsight init --stream /path/to/project   # adds trace_stream.c + zstd.c

# on the device:
cc -O2 -o callsight/trace_stream callsight/trace_stream.c callsight/zstd.c
./callsight/trace_stream /tracekit0 <server-ip> 9001 &
TRACE_ENABLE=1 TRACE_SHM=/tracekit0 ./yourapp.instr
```

The client exits once the traced process detaches and the ring is drained;
`callsight serve` prints one line per connection with the event count.

## Guarantees and trade-offs

- **No I/O in the traced process.** Flushes are a locked `memcpy` of a
  buffered batch into shared memory.
- **Never stalls the workload.** If the ring fills faster than the client
  drains (slow network, small ring), events are *dropped and counted*; the
  server prints the drop count. Size the ring with `TRACE_SHM_SIZE`
  (default 16 MiB).
- **Same analysis path.** The server writes standard
  `trace.stream.*.bin` files; `callsight analyze` and the web UI consume
  them unchanged. Events from all threads are interleaved in one stream
  and demultiplexed by the analyzer (each event carries its tid).
- **Self-contained client.** `trace_stream.c` builds against the vendored
  single-file zstd v1.5.7 (generated from the official repo's
  `build/single_file_libs`; BSD license — see `zstd.LICENSE`). One `cc`
  line, cross-compiles like any C file.

## Wire protocol (stable, versioned)

Defined in `trace_shm.h`. Header: `TKSTREAM` magic, version, event size;
then chunks of `type, raw_len, zstd_len, payload`. Type 0 = events,
type 1 = drop notice. All integers little-endian.
