# tracekit — compile-time function tracing for C/C++

Add entry/exit timing hooks to **every function in a C/C++ project at
compile time, with zero edits to its sources**, control exactly which files,
folders, or functions get hooks from **one config file**, and turn the
resulting traces into per-function hotspot reports. Works with GCC and
Clang, C and C++, with GNU Make and CMake projects.

## Install

```sh
uv tool install .        # from this repo; puts `tracekit` on your PATH
```

(Requires [uv](https://docs.astral.sh/uv/). The tool itself is Python
stdlib-only; the runtime it injects is dependency-free C.)

## Adopt it in your project (2 steps)

```sh
cd /path/to/your/project
tracekit init .          # copies runtime + build wiring, writes trace.config
```

Then follow the printed wiring snippet for your build system:

- **Make**: `include tracekit/Makefile.tracekit`, add an `instrument`
  target linking `$(TRACE_OBJ)` with `-no-pie` (snippet printed by `init`).
- **CMake**: `include(TraceKit)` + `tracekit_instrument(<target>)`, then
  configure with `-DTRACEKIT_INSTRUMENT=ON`.

## Collect and analyze

```sh
make instrument                                 # or: cmake --build build-instr
TRACE_ENABLE=1 TRACE_MAX=1000000 ./yourapp      # collect (inert without TRACE_ENABLE=1)
tracekit analyze traces/ --exe ./yourapp --top 20
```

The analyzer reports calls / inclusive / self / max time per function,
resolving symbols — including `static` functions — with `addr2line`.
`unmatched_exits=0` means a clean trace.

## How it works

1. `tracekit flags` turns `trace.config` + your source list into
   `-finstrument-functions` plus compile-time exclude lists
   (`-finstrument-functions-exclude-file-list/-exclude-function-list`).
2. The compiler emits calls to `__cyg_profile_func_enter/exit` at the
   entry/exit of every selected function. Excluded code emits **no hook at
   all** — compile-time exclusion is free at runtime.
3. The hook runtime (`trace.c`, itself compiled without the flag) appends
   32-byte events to a per-thread buffer — no locks, no malloc, no I/O in
   the hot path — flushing `trace.<pid>.<tid>.bin` when full or at exit.
4. `tracekit analyze` matches enter/exit events per thread and prints
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

`tracekit scan <dir> --config trace.config` previews what a config selects.

Runtime knobs: `TRACE_ENABLE` (default off), `TRACE_DIR` (default
`./traces`), `TRACE_MAX` (global event cap — always set one for long runs).

## CLI reference

| command | purpose |
|---|---|
| `tracekit init <dir> [--build make\|cmake]` | adopt into a project |
| `tracekit scan <dir> [--config c]` | preview instrumentation selection |
| `tracekit flags --config c -- srcs...` | print compiler flags (build integrations use this) |
| `tracekit analyze [traces/] [--exe bin] [--top N]` | hotspot report |

## Repo layout

- `src/tracekit/` — the tool: `cli.py`, `flags.py` (config → compiler
  flags), `analyze.py` (offline analyzer); stdlib-only.
- `src/tracekit/runtime/` — `trace.c`/`trace.h`, the hook runtime copied
  into adopted projects. Self-contained C, no deps beyond pthreads.
- `src/tracekit/share/Makefile.tracekit`, `src/tracekit/cmake/TraceKit.cmake`
  — build-system integrations.
- `tests/matrixlab/` — multi-threaded C11 demo workload; doubles as the
  end-to-end smoke test (`make instrument` → run → `tracekit analyze`).
- `tests/cmake_demo/` — tiny CMake fixture for the CMake integration.
- `docs/instrumentation-options.md` — survey of GCC/Clang compile-time
  instrumentation mechanisms and how they map to tracekit's roadmap.

## Known limitations

- Inlined functions emit no hooks (there is no call boundary).
- Overhead ~30–60 ns per event; a profiling build, not a production one.
- A crashed/killed process loses each thread's buffered tail; clean exits
  flush everything.
- `exclude-file-list` matching by the compiler is substring-based; unusually
  overlapping directory names can over-match.

## Roadmap

- **Phase 2 — web UI**: uv-run local web app (no root) for folder selection,
  config editing, build triggering, and report viewing, as an optional
  `tracekit[ui]` extra.
- **Phase 3 — remote streaming**: sink abstraction in the runtime; an
  on-device client streams ZSTD-compressed events over raw TCP to an
  analysis server; runtime on/off via `-fpatchable-function-entry`.
