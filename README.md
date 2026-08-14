# callscope — compile-time function tracing for C/C++

[![ci](https://github.com/harshithsunku/callscope/actions/workflows/ci.yml/badge.svg)](https://github.com/harshithsunku/callscope/actions/workflows/ci.yml)
[![docs](https://github.com/harshithsunku/callscope/actions/workflows/pages.yml/badge.svg)](https://harshithsunku.github.io/callscope/)
[![PyPI](https://img.shields.io/pypi/v/callscope.svg)](https://pypi.org/project/callscope/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Docs: https://harshithsunku.github.io/callscope/** ·
[Status & roadmap](STATUS.md)

Add entry/exit timing hooks to **every function in a C/C++ project at
compile time, with zero edits to its sources**, control exactly which files,
folders, or functions get hooks from **one config file**, and turn the
resulting traces into per-function hotspot reports. Works with GCC and
Clang, C and C++, with GNU Make and CMake projects.

## Install

```sh
uv tool install callscope        # from PyPI; puts `callscope` on your PATH
uv tool install 'callscope[ui]'  # same, plus the optional web UI
uv tool install .                # or from a source checkout
```

(Requires [uv](https://docs.astral.sh/uv/). The tool itself is Python
stdlib-only; the runtime it injects is dependency-free C.)

## Web UI

```sh
callscope ui                # serves http://127.0.0.1:8321
```

A local web app that walks the whole workflow against any project on the
machine: browse for the project folder, edit its `trace.config`, preview
the instrumentation selection, build the instrumented profile (Make or
CMake — CMake is fetched ephemerally via `uvx` if not installed), run the
binary with tracing enabled, and view the sortable hotspot report. No root
needed; bind address defaults to localhost.

## Adopt it in your project (2 steps)

```sh
cd /path/to/your/project
callscope init .          # copies runtime + build wiring, writes trace.config
```

Then follow the printed wiring snippet for your build system:

- **Make**: `include callscope/Makefile.callscope`, add an `instrument`
  target linking `$(TRACE_OBJ)` with `-no-pie` (snippet printed by `init`).
- **CMake**: `include(CallScope)` + `callscope_instrument(<target>)`, then
  configure with `-DCALLSCOPE_INSTRUMENT=ON`.

## Collect and analyze

```sh
make instrument                                 # or: cmake --build build-instr
TRACE_ENABLE=1 TRACE_MAX=1000000 ./yourapp      # collect (inert without TRACE_ENABLE=1)
callscope analyze traces/ --exe ./yourapp --top 20
```

The analyzer reports calls / inclusive / self / max time per function,
resolving symbols — including `static` functions — with `addr2line`.
`unmatched_exits=0` means a clean trace.

## How it works

1. `callscope flags` turns `trace.config` + your source list into
   `-finstrument-functions` plus compile-time exclude lists
   (`-finstrument-functions-exclude-file-list/-exclude-function-list`).
2. The compiler emits calls to `__cyg_profile_func_enter/exit` at the
   entry/exit of every selected function. Excluded code emits **no hook at
   all** — compile-time exclusion is free at runtime.
3. The hook runtime (`trace.c`, itself compiled without the flag) appends
   32-byte events to a per-thread buffer — no locks, no malloc, no I/O in
   the hot path — flushing `trace.<pid>.<tid>.bin` when full or at exit.
4. `callscope analyze` matches enter/exit events per thread and prints
   hotspot tables.

## Selection strategy (important at scale)

Event volume is the main cost — a call-heavy program easily generates
millions of events per second. Levers, cheapest first:

1. **Compile-time excludes** in `trace.config`: run wide once, sort
   analyzer output by `calls`, exclude the chatty leaf helpers, rebuild.
   Typically cuts volume 10–100×.
2. **`include` directives**: instrument only the subsystem under
   investigation.
3. **Runtime gating**: `TRACE_ENABLE` / `TRACE_MAX` control *when* and *how
   much* you pay.
4. **Source opt-out** (optional): `__attribute__((no_instrument_function))`.

`callscope scan <dir> --config trace.config` previews what a config selects.

Runtime knobs: `TRACE_ENABLE` (default off), `TRACE_DIR` (default
`./traces`), `TRACE_MAX` (global event cap — always set one for long runs),
`TRACE_SHM` / `TRACE_SHM_SIZE` (streaming mode, see above).

## Remote streaming (devices, embedded)

On a constrained device you can't accumulate trace files — a busy program
generates millions of events per second. Streaming mode keeps nothing on
the device: the runtime flushes into a shared-memory ring, and a tiny
on-device client forwards events ZSTD-compressed over raw TCP.

```sh
# analysis host (powerful machine):
callscope serve --port 9001 --out traces/     # needs callscope[stream]

# adopt with streaming support:
callscope init --stream /path/to/project      # adds trace_stream.c + zstd.c

# on the device:
cc -O2 -o callscope/trace_stream callscope/trace_stream.c callscope/zstd.c
./callscope/trace_stream /callscope0 <server-ip> 9001 &
TRACE_ENABLE=1 TRACE_SHM=/callscope0 ./yourapp.instr
```

- The traced process does **no disk or network I/O** — only a shared-memory
  memcpy per buffered batch.
- If the ring fills faster than the client drains (network slow, ring too
  small), events are **dropped and counted**, never blocking the workload;
  the server reports the drop count. Size the ring with `TRACE_SHM_SIZE`.
- The server writes standard `trace.stream.*.bin` files — `callscope
  analyze` and the web UI consume them unchanged.
- The client is self-contained C built against the vendored single-file
  zstd v1.5.7 (`src/callscope/stream/zstd.c`, generated from the official
  repo's `build/single_file_libs`; BSD license in `zstd.LICENSE`).

## CLI reference

| command | purpose |
|---|---|
| `callscope init <dir> [--build make\|cmake]` | adopt into a project |
| `callscope scan <dir> [--config c]` | preview instrumentation selection |
| `callscope flags --config c -- srcs...` | print compiler flags (build integrations use this) |
| `callscope analyze [traces/] [--exe bin] [--top N]` | hotspot report |
| `callscope ui [--host H] [--port P]` | web UI (needs `callscope[ui]`) |
| `callscope serve [--host H] [--port P] [--out dir]` | TCP server for remote streams (needs `callscope[stream]`) |

## Repo layout

- `src/callscope/` — the tool: `cli.py`, `flags.py` (config → compiler
  flags), `analyze.py` (offline analyzer); stdlib-only. `src/callscope/ui/`
  is the optional web UI (FastAPI, only imported by `callscope ui`).
- `src/callscope/runtime/` — `trace.c`/`trace.h`/`trace_shm.h`, the hook
  runtime copied into adopted projects. Self-contained C, no deps beyond
  pthreads.
- `src/callscope/stream/` — `trace_stream.c` on-device streaming client +
  vendored single-file zstd v1.5.7 (`zstd.c`, BSD — see `zstd.LICENSE`).
- `src/callscope/share/Makefile.callscope`, `src/callscope/cmake/CallScope.cmake`
  — build-system integrations.
- `tests/matrixlab/` — multi-threaded C11 demo workload; doubles as the
  end-to-end smoke test (`make instrument` → run → `callscope analyze`).
- `tests/cmake_demo/` — tiny CMake fixture for the CMake integration.
- `docs/instrumentation-options.md` — survey of GCC/Clang compile-time
  instrumentation mechanisms and how they map to callscope's roadmap.

## Known limitations

- Inlined functions emit no hooks (there is no call boundary).
- Overhead ~30–60 ns per event; a profiling build, not a production one.
- A crashed/killed process loses each thread's buffered tail; clean exits
  flush everything.
- `exclude-file-list` matching by the compiler is substring-based; unusually
  overlapping directory names can over-match.

## Roadmap

- **Phase 2 — web UI**: done (`callscope ui`, optional `callscope[ui]` extra).
  Next: richer report views (call graphs, flame graphs), live build-log
  streaming.
- **Phase 3 — remote streaming**: done (`TRACE_SHM` ring → `trace_stream`
  client → ZSTD/TCP → `callscope serve`). Next: runtime on/off via
  `-fpatchable-function-entry` (see docs/instrumentation-options.md), live
  stream view in the web UI.
