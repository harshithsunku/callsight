# Remote streaming

On a constrained device you cannot accumulate trace files — a busy program
generates millions of events per second. Streaming mode keeps **nothing**
on the device: the runtime flushes into a shared-memory ring, and a tiny
on-device client forwards events ZSTD-compressed over raw TCP.

## When to stream

- **Remote or embedded targets** — the interesting workload runs on a
  device you reach over the network, not on your workstation.
- **No disk (or no spare disk) on the device** — nothing is written to
  the device's filesystem; events live in RAM until they leave over TCP.
- **Long-running workloads** — file mode grows unboundedly; streaming
  bounds device-side memory to one fixed-size ring, no matter how long
  the program runs.

## How it works

```
traced process                 device client                analysis host
──────────────                ──────────────                ─────────────
trace.c hooks ──► shm ring ──► trace_stream ──zstd/TCP──► callsight serve
(TRACE_SHM=/name)   (no disk,               (compression)      │ writes
                     no network,                              ▼
                     drop-counted)                  trace.stream.*.bin
                                                    → analyze / web UI
```

1. **Traced process.** With `TRACE_SHM=/name` set, the `trace.c` hooks
   flush per-thread event batches into a POSIX shared-memory ring
   instead of files. A flush is a locked `memcpy` of a buffered batch —
   short critical sections, serialized by a spinlock, never held across
   I/O (there is no I/O).
2. **On-device client.** `trace_stream` maps the same ring, drains it
   in batches, ZSTD-compresses each batch, and sends it over a raw TCP
   connection. It exits once the traced process detaches and the ring
   is drained.
3. **Analysis host.** `callsight serve` decompresses each chunk and
   appends it to a standard `trace.stream.<id>.bin` file — one per
   connection. `callsight analyze` and the web UI consume these files
   unchanged; events from all threads arrive interleaved in one stream
   and are demultiplexed by the analyzer (each event carries its tid).
   The server prints one line per connection with the event count.

## The never-stall guarantee

Profiling must never slow down the thing it measures:

- **No disk or network I/O in the traced process** — only shared-memory
  copies.
- **Ring full → drop and count.** If the ring fills faster than the
  client drains (slow network, small ring), the tracer drops events and
  increments a `dropped` counter in the ring header. The client forwards
  the count as a notice chunk and the server prints it, so data loss is
  always visible, never silent.
- **Size the ring to taste** with `TRACE_SHM_SIZE` (bytes, default
  16 MiB — `TRACE_SHM_DEF_SIZE` in `trace_shm.h`).

## Ring layout and wire protocol

Both live in `src/callsight/runtime/trace_shm.h` (bump
`TRACE_SHM_VERSION` / `TRACE_STREAM_VERSION` on any layout change). All
integers little-endian.

**Ring header** (`trace_shm_header_t`), followed by `capacity` bytes of
event storage:

| field | type | meaning |
|---|---|---|
| `magic` | `char[8]` | `TKSHM` |
| `version` | `u32` | `TRACE_SHM_VERSION` (1) |
| `capacity` | `u32` | ring bytes after the header |
| `writers` | `u32` | tracer processes attached |
| `lock` | `u32` | spinlock: 0 = free |
| `head` | `u64` | monotonic write offset (bytes) |
| `tail` | `u64` | monotonic read offset (bytes) |
| `dropped` | `u64` | events dropped (ring was full) |

`head`/`tail` are monotonic byte counters; bytes in use = `head - tail`.

**Wire protocol** (client → server, TCP): an 8-byte `TKSTREAM` magic,
`u32 version` (`TRACE_STREAM_VERSION`, 1) and `u32 event_size`, then a
sequence of chunks, each `u32 type, u32 raw_len, u32 zstd_len` followed
by `zstd_len` bytes of ZSTD-compressed payload:

- **type 0 — events**: the payload decompresses to `raw_len` bytes of
  raw events, appended to the output file as-is.
- **type 1 — notice**: the payload decompresses to a `u64` dropped-event
  count, printed by the server.

## Quick start

```sh
# analysis host (needs the stream extra):
uv tool install 'callsight[stream]'
callsight serve                          # listens on 0.0.0.0:9001, writes traces/
# or explicitly: callsight serve --host 0.0.0.0 --port 9001 --out traces/

# adopt with streaming support:
callsight init --stream /path/to/project   # adds trace_stream.c + zstd.c

# on the device:
cc -O2 -o callsight/trace_stream callsight/trace_stream.c callsight/zstd.c
./callsight/trace_stream /tracekit0 <server-ip> 9001 &
TRACE_ENABLE=1 TRACE_SHM=/tracekit0 ./yourapp.instr
```

The client exits once the traced process detaches and the ring is
drained; `callsight serve` prints one line per connection with the
event count.

## Notes

- **Same analysis path.** The server writes standard
  `trace.stream.*.bin` files; point `callsight analyze traces/ --exe
  <binary>` at them exactly as with file-mode traces.
- **Self-contained client.** `trace_stream.c` builds against the
  vendored single-file zstd v1.5.7 (generated from the official repo's
  `build/single_file_libs`; BSD license — see `zstd.LICENSE`). One `cc`
  line, cross-compiles like any C file.
