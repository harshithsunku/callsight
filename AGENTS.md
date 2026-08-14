# AGENTS.md

## What this repo is

**tracekit** — an open-source compile-time function-tracing toolkit for
C/C++ projects (GCC/Clang, Make/CMake). Adopted into a target project via
the `tracekit` CLI (`uv` package); adds entry/exit hooks at compile time
with zero source edits, controlled by a single `trace.config`.

Layout:

- `src/tracekit/` — the Python package (stdlib-only): `cli.py` (init /
  scan / flags / analyze), `flags.py`, `analyze.py`.
- `src/tracekit/runtime/` — `trace.c` / `trace.h`: the hook runtime.
  Self-contained; must stay free of project-specific dependencies.
- `src/tracekit/share/Makefile.tracekit`, `src/tracekit/cmake/TraceKit.cmake`
  — build-system integrations (copied into adopted projects by
  `tracekit init`).
- `tests/matrixlab/` — demo C11/pthreads workload; the end-to-end fixture.
- `tests/cmake_demo/` — minimal CMake integration fixture.
- `tests/test_select.py` — unit tests for config parsing/selection.
- `docs/instrumentation-options.md` — compiler-mechanism survey.

## Build / test commands

```sh
# unit tests (pure stdlib):
python3 -m unittest discover -s tests

# CLI without installing:
uv run tracekit --help
# installed:
uv tool install . && tracekit --help

# end-to-end smoke test (Make integration):
cd tests/matrixlab
make clean && make instrument          # clean REQUIRED when switching profiles
TRACE_ENABLE=1 TRACE_MAX=1000000 timeout 5 ./bin/matrixlab.instr
uv run tracekit analyze traces/ --top 20

# end-to-end smoke test (CMake integration, cmake via uvx — no system cmake):
cd tests/cmake_demo
uvx cmake -DTRACEKIT_INSTRUMENT=ON \
    "-DTRACEKIT_COMMAND=python3;$PWD/../../src/tracekit/cli.py" -B build-instr
uvx cmake --build build-instr
TRACE_ENABLE=1 TRACE_MAX=200000 ./build-instr/demo
uv run tracekit analyze traces/ --exe build-instr/demo
```

There is no pytest suite; `unittest` plus the end-to-end runs above are the
smoke tests. A correct run reports `unmatched_exits=0` and plausible
hotspots (cmake_demo must show only `fib` — `mix` is excluded).

## Conventions

- C: C11, `-Wall -Wextra -pedantic`; match the existing comment style
  (short `/* ... */` above each function).
- Python: stdlib only — no third-party dependencies in the core package.
  Future web-UI deps go in an optional `tracekit[ui]` extra.
- The instrumentation runtime (`src/tracekit/runtime/trace.c`) must never
  be compiled with `-finstrument-functions`; every function there carries
  `__attribute__((no_instrument_function))`, and both build integrations
  compile it without the flag.
- Runtime config env vars use the `TRACE_*` prefix (`TRACE_ENABLE`,
  `TRACE_DIR`, `TRACE_MAX`). matrixlab's own config uses `MATRIXLAB_*` —
  keep the two namespaces separate.
- Selection rules live only in the target project's `trace.config`; do not
  hard-code exclude lists in Makefiles or CMake files.
- `TRACEKIT_COMMAND` in CMake is a cache variable — pass it with `-D`
  (a plain `set()` before `include(TraceKit)` gets shadowed by the cache
  definition under CMP0126).
- Do not commit `.venv/`, `build*/`, `bin/`, or `traces/` (see .gitignore).
