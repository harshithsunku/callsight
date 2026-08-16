# callsight — compile-time function tracing for C/C++

[![ci](https://github.com/harshithsunku/callsight/actions/workflows/ci.yml/badge.svg)](https://github.com/harshithsunku/callsight/actions/workflows/ci.yml)
[![docs](https://github.com/harshithsunku/callsight/actions/workflows/pages.yml/badge.svg)](https://harshithsunku.github.io/callsight/)
[![PyPI](https://img.shields.io/pypi/v/callsight.svg)](https://pypi.org/project/callsight/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Docs: https://harshithsunku.github.io/callsight/** ·
[Status & roadmap](STATUS.md)

[Getting started](https://harshithsunku.github.io/callsight/getting-started.html) ·
[Configuration](https://harshithsunku.github.io/callsight/configuration.html) ·
[Analysis](https://harshithsunku.github.io/callsight/analysis.html) ·
[Web UI](https://harshithsunku.github.io/callsight/web-ui.html) ·
[Streaming](https://harshithsunku.github.io/callsight/streaming.html) ·
[Architecture](https://harshithsunku.github.io/callsight/architecture.html) ·
[Reference](https://harshithsunku.github.io/callsight/reference.html)

Add entry/exit timing hooks to **every function in a C/C++ project at
compile time, with zero edits to its sources**, control exactly which files,
folders, or functions get hooks from **one config file**, and turn the
resulting traces into per-function hotspot reports and flame graphs. C and
C++, GNU Make and CMake, on Linux.

**Compiler:** selective instrumentation needs **GCC** — the exclude flags it
builds on are GCC-only ([LLVM issue
#15627](https://github.com/llvm/llvm-project/issues/15627)). Clang can
instrument *everything* (a config with no `include`/`exclude` directives);
callsight detects the toolchain and tells you up front rather than letting
the build fail one file at a time.

## Install

```sh
uv tool install callsight           # from PyPI; puts `callsight` on your PATH
uv tool install 'callsight[ui]'     # same, plus the optional web UI
uv tool install 'callsight[stream]' # same, plus the streaming server
uv tool install .                   # or from a source checkout
```

(Requires [uv](https://docs.astral.sh/uv/). The tool itself is Python
stdlib-only; the runtime it injects is dependency-free C.)

## Web UI

```sh
callsight ui                # serves http://127.0.0.1:8321
```

A local web app that walks the whole workflow against any project on the
machine: browse for the project folder, edit its `trace.config`, preview
the instrumentation selection, build the instrumented profile (Make or
CMake — CMake is fetched ephemerally via `uvx` if not installed), run the
binary with tracing enabled, and view the sortable hotspot report. A
second tab, the **config builder**, scans the project folder into
searchable checkbox panes of files and functions (enumerated with `ctags`
— auto-downloaded on first scan when the system has none — with a regex
fallback) and generates the `trace.config` from your picks. No root
needed; bind address defaults to localhost.

## Adopt it in your project (2 steps)

```sh
cd /path/to/your/project
callsight init .          # copies runtime + build wiring, writes trace.config
```

Then follow the printed wiring snippet for your build system:

- **Make**: `include callsight/Makefile.callsight`, add an `instrument`
  target linking `$(TRACE_OBJ)` with `-no-pie` (snippet printed by `init`).
- **CMake**: `include(CallSight)` + `callsight_instrument(<target>)`, then
  configure with `-DCALLSIGHT_INSTRUMENT=ON`.

## Collect and analyze

```sh
make instrument                                 # or: cmake --build build-instr
TRACE_ENABLE=1 TRACE_MAX=1000000 ./yourapp      # collect (inert without TRACE_ENABLE=1)
callsight analyze traces/ --exe ./yourapp --top 20
```

The analyzer reports calls / inclusive / self / max time per function,
resolving symbols — including `static` functions — with `addr2line`.
`unmatched_exits=0` means a clean trace. Trace files are streamed, so a
multi-million-event run costs a few MB of analyzer memory, not gigabytes.

### Flame graphs and machine-readable output

```sh
callsight analyze traces/ --exe ./yourapp --format folded > out.folded
flamegraph.pl out.folded > out.svg      # or drag out.folded into speedscope.app
callsight analyze traces/ --exe ./yourapp --format json --top 0 | jq '.rows[0]'
```

`--format folded` prints one collapsed stack per call path
(`main;handle_request;parse <self_ns>`), the input format understood by
[flamegraph.pl](https://github.com/brendangregg/FlameGraph) and
[speedscope](https://speedscope.app). `--format json` emits the whole report
— summary counters, per-function rows, per-thread timing — for your own
tooling (`--top 0` keeps every row).

## How it works

1. `callsight flags` turns `trace.config` + your source list into
   `-finstrument-functions` plus compile-time exclude lists
   (`-finstrument-functions-exclude-file-list/-exclude-function-list`).
2. The compiler emits calls to `__cyg_profile_func_enter/exit` at the
   entry/exit of every selected function. Excluded code emits **no hook at
   all** — compile-time exclusion is free at runtime.
3. The hook runtime (`trace.c`, itself compiled without the flag) appends
   32-byte events to a per-thread buffer — no locks, no malloc, no I/O in
   the hot path — flushing `trace.<pid>.<tid>.bin` when full or at exit.
4. `callsight analyze` matches enter/exit events per thread and prints
   hotspot tables.

## Selection strategy (important at scale)

Event volume is the main cost — a call-heavy program easily generates
millions of events per second. Levers, cheapest first:

1. **Compile-time excludes** in `trace.config`: run wide once, sort
   analyzer output by `calls`, exclude the chatty leaf helpers, rebuild.
   Typically cuts volume 10–100×.
2. **`include` directives**: instrument only the subsystem under
   investigation.
3. **`include-func` (function/task level)**: name one entry function and
   callsight instruments exactly its call subtree, resolved statically
   from the sources — `include-func workload_sort` traces
   `workload_sort` and everything it calls, nothing else. Explore first
   with `callsight select src/ --function workload_sort`.
4. **Runtime gating**: `TRACE_ENABLE` / `TRACE_MAX` control *when* and *how
   much* you pay; `TRACE_THREADS="sort-*"` traces only matching threads.
5. **Source opt-out** (optional): `__attribute__((no_instrument_function))`.

`callsight scan <dir> --config trace.config` previews what a config selects.

Runtime knobs: `TRACE_ENABLE` (default off), `TRACE_DIR` (default
`./traces`), `TRACE_MAX` (global event cap — always set one for long runs),
`TRACE_THREADS` (thread-name glob filter), `TRACE_SHM` / `TRACE_SHM_SIZE`
(streaming mode, see below).

## Remote streaming (devices, embedded)

On a constrained device you can't accumulate trace files — a busy program
generates millions of events per second. Streaming mode keeps nothing on
the device: the runtime flushes into a shared-memory ring, and a tiny
on-device client forwards events ZSTD-compressed over raw TCP.

```sh
# analysis host (powerful machine):
callsight serve --port 9001 --out traces/     # needs callsight[stream]

# adopt with streaming support:
callsight init --stream /path/to/project      # adds trace_stream.c + zstd.c

# on the device:
cc -O2 -o callsight/trace_stream callsight/trace_stream.c callsight/zstd.c
./callsight/trace_stream /callsight0 <server-ip> 9001 &
TRACE_ENABLE=1 TRACE_SHM=/callsight0 ./yourapp.instr
```

- The traced process does **no disk or network I/O** — only a shared-memory
  memcpy per buffered batch.
- If the ring fills faster than the client drains (network slow, ring too
  small), events are **dropped and counted**, never blocking the workload;
  the server reports the drop count. Size the ring with `TRACE_SHM_SIZE`.
- The server writes standard `trace.stream.*.bin` files — `callsight
  analyze` and the web UI consume them unchanged.
- The client is self-contained C built against the vendored single-file
  zstd v1.5.7 (`src/callsight/stream/zstd.c`, generated from the official
  repo's `build/single_file_libs`; BSD license in `zstd.LICENSE`).

## How it compares

| tool | granularity | selection | needs |
|---|---|---|---|
| **callsight** | every function entry/exit, exact timing | **compile time, from one config file** — excluded code emits no hook at all | rebuild with GCC |
| [uftrace](https://github.com/namhyung/uftrace) | same mechanism, richer live TUI/replay | mostly *runtime* filters (`-F`/`-N`), so filtered functions still pay the hook | rebuild (`-pg`/`-finstrument-functions`) |
| `perf record` | sampled, statistical | none needed | no rebuild; often root/`perf_event_paranoid` |
| gprof (`-pg`) | sampled + call counts | none | rebuild; single-threaded accounting |
| Clang XRay | entry/exit with runtime patching | per-function attributes / lists | rebuild with Clang only |

Reach for `perf` first when you want a cheap statistical profile of a whole
system. Reach for callsight when you need **exact per-call timing for a
chosen subsystem** — every call counted, nothing sampled — and you want the
cost of the functions you did *not* choose to be exactly zero, because they
were never given a hook. The selection lives in `trace.config` next to the
code, and the same config drives the traced device and the analysis host.

## CLI reference

| command | purpose |
|---|---|
| `callsight init <dir> [--build make\|cmake]` | adopt into a project |
| `callsight scan <dir> [--config c]` | preview instrumentation selection |
| `callsight select <dir> --function F [--depth N]` | show a function's call subtree; emit config lines |
| `callsight flags --config c -- srcs...` | print compiler flags (build integrations use this) |
| `callsight analyze [traces/] [--exe bin] [--top N] [--format text\|json\|folded]` | hotspot report, JSON, or collapsed stacks |
| `callsight ui [--host H] [--port P]` | web UI (needs `callsight[ui]`) |
| `callsight provision [--force]` | download the bundled static ctags used by the UI config builder |
| `callsight serve [--host H] [--port P] [--out dir]` | TCP server for remote streams (needs `callsight[stream]`) |

## Repo layout

- `src/callsight/` — the tool: `cli.py`, `flags.py` (config → compiler
  flags), `analyze.py` (offline analyzer), `callgraph.py` (static call
  graph behind `include-func`), `symbols.py` (function enumeration for
  the config builder), `provision.py` (bundled ctags download); the core
  is stdlib-only, `serve.py` (streaming TCP server) needs the `stream`
  extra. `src/callsight/ui/` is the optional web UI (FastAPI, only
  imported by `callsight ui`).
- `src/callsight/runtime/` — `trace.c`/`trace.h`/`trace_shm.h`, the hook
  runtime copied into adopted projects. Self-contained C, no deps beyond
  pthreads.
- `src/callsight/stream/` — `trace_stream.c` on-device streaming client +
  vendored single-file zstd v1.5.7 (`zstd.c`, BSD — see `zstd.LICENSE`).
- `src/callsight/share/Makefile.callsight`, `src/callsight/cmake/CallSight.cmake`
  — build-system integrations.
- `tests/matrixlab/` — multi-threaded C11 demo workload; doubles as the
  end-to-end smoke test (`make instrument` → run → `callsight analyze`).
- `tests/cmake_demo/` — tiny CMake fixture for the CMake integration.
- `docs/` — the documentation site (hand-built static HTML, published to
  GitHub Pages). `docs/architecture.html` carries the survey of GCC/Clang
  instrumentation mechanisms and how they map to callsight's roadmap.

## Known limitations

- Selective instrumentation is GCC-only; Clang has `-finstrument-functions`
  but not the exclude lists ([LLVM
  #15627](https://github.com/llvm/llvm-project/issues/15627)). Under Clang,
  only an unfiltered "instrument everything" config builds.
- Linux only: the runtime uses `SYS_gettid`, `pthread_getname_np` and POSIX
  shared memory.
- Inlined functions emit no hooks (there is no call boundary).
- Overhead ~30–60 ns per event; a profiling build, not a production one.
- A crashed/killed process loses each thread's buffered tail; clean exits
  flush everything.
- `exclude-file-list` matching by the compiler is substring-based; unusually
  overlapping directory names can over-match.
- Analysis needs the recorded addresses to match link addresses, so link with
  `-no-pie` (both build integrations do). `analyze` warns when most addresses
  fail to resolve, which is what a PIE binary looks like.

## Roadmap

- **Phase 2 — web UI**: done (`callsight ui`, optional `callsight[ui]` extra).
  Flame graphs: done via `analyze --format folded` (flamegraph.pl /
  speedscope). Next: richer in-UI report views, live build-log streaming.
- **Phase 3 — remote streaming**: done (`TRACE_SHM` ring → `trace_stream`
  client → ZSTD/TCP → `callsight serve`). Next: runtime on/off via
  `-fpatchable-function-entry` (see the [compiler-mechanism
  survey](https://harshithsunku.github.io/callsight/architecture.html#mechanisms)),
  live stream view in the web UI.
