# AGENTS.md

## What this repo is

**tracekit** — an open-source compile-time function-tracing toolkit for
C/C++ projects (GCC/Clang, Make/CMake). Adopted into a target project via
the `tracekit` CLI (`uv` package); adds entry/exit hooks at compile time
with zero source edits, controlled by a single `trace.config`.

Layout:

- `src/tracekit/` — the Python package (stdlib-only core): `cli.py` (init /
  scan / flags / analyze / ui), `flags.py`, `analyze.py`.
- `src/tracekit/ui/` — optional web UI (FastAPI + single-page frontend in
  `static/`); third-party deps live in the `ui` extra, imported only by
  `tracekit ui`.
- `src/tracekit/runtime/` — `trace.c` / `trace.h` / `trace_shm.h`: the hook
  runtime and shared-memory ring protocol. Self-contained; must stay free
  of project-specific dependencies.
- `src/tracekit/stream/` — `trace_stream.c` (on-device streaming client)
  plus vendored single-file zstd v1.5.7 (`zstd.c` / `zstd.h` /
  `zstd_errors.h`, BSD — `zstd.LICENSE`). Regenerate from the official
  repo's `build/single_file_libs/create_single_file_library.sh` if zstd
  ever needs an upgrade.
- `src/tracekit/serve.py` — TCP server for remote streams (`tracekit
  serve`); zstandard dep lives in the `stream` extra.
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
# web UI (optional deps via the ui extra):
uv run --extra ui tracekit ui          # http://127.0.0.1:8321
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

# end-to-end smoke test (remote streaming):
uv run --extra stream tracekit serve --port 9001 --out /tmp/stream_traces &
gcc -O2 -I src/tracekit/runtime -o /tmp/trace_stream \
    src/tracekit/stream/trace_stream.c src/tracekit/stream/zstd.c
/tmp/trace_stream /tk_smoke 127.0.0.1 9001 &
cd tests/matrixlab
TRACE_ENABLE=1 TRACE_SHM=/tk_smoke TRACE_MAX=500000 timeout 5 ./bin/matrixlab.instr
# client exits after drain; then:
uv run tracekit analyze /tmp/stream_traces --exe bin/matrixlab.instr
rm -f /dev/shm/tk_smoke
```

There is no pytest suite; `unittest` plus the end-to-end runs above are the
smoke tests. A correct run reports `unmatched_exits=0` and plausible
hotspots (cmake_demo must show only `fib` — `mix` is excluded).

## Conventions

- C: C11, `-Wall -Wextra -pedantic`; match the existing comment style
  (short `/* ... */` above each function).
- Python: stdlib only in the core package (`cli.py`, `flags.py`,
  `analyze.py`). Third-party deps are allowed only in the optional extras —
  `ui` (FastAPI/uvicorn, under `src/tracekit/ui/`) and `stream`
  (zstandard, `src/tracekit/serve.py`) — the core must import cleanly
  without them.
- Streaming mode (`TRACE_SHM`) must never stall the workload: when the
  shared-memory ring is full, drop events and count them in the ring
  header. No disk or network I/O in the traced process.
- The wire protocol and ring layout live only in
  `src/tracekit/runtime/trace_shm.h`; bump `TRACE_SHM_VERSION` /
  `TRACE_STREAM_VERSION` on any layout change.
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
