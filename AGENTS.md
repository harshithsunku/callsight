# AGENTS.md

## What this repo is

**callsight** — an open-source compile-time function-tracing toolkit for
C/C++ projects (GCC/Clang, Make/CMake). Adopted into a target project via
the `callsight` CLI (`uv` package); adds entry/exit hooks at compile time
with zero source edits, controlled by a single `trace.config`.

Layout:

- `src/callsight/` — the Python package (stdlib-only core): `cli.py` (init /
  scan / flags / analyze / ui), `flags.py`, `analyze.py`.
- `src/callsight/ui/` — optional web UI (FastAPI + single-page frontend in
  `static/`); third-party deps live in the `ui` extra, imported only by
  `callsight ui`.
- `src/callsight/runtime/` — `trace.c` / `trace.h` / `trace_shm.h`: the hook
  runtime and shared-memory ring protocol. Self-contained; must stay free
  of project-specific dependencies.
- `src/callsight/stream/` — `trace_stream.c` (on-device streaming client)
  plus vendored single-file zstd v1.5.7 (`zstd.c` / `zstd.h` /
  `zstd_errors.h`, BSD — `zstd.LICENSE`). Regenerate from the official
  repo's `build/single_file_libs/create_single_file_library.sh` if zstd
  ever needs an upgrade.
- `src/callsight/serve.py` — TCP server for remote streams (`callsight
  serve`); zstandard dep lives in the `stream` extra.
- `src/callsight/share/Makefile.callsight`, `src/callsight/cmake/CallSight.cmake`
  — build-system integrations (copied into adopted projects by
  `callsight init`).
- `tests/matrixlab/` — demo C11/pthreads workload; the end-to-end fixture.
- `tests/cmake_demo/` — minimal CMake integration fixture.
- `tests/test_select.py` — unit tests for config parsing/selection.
- `docs/instrumentation-options.md` — compiler-mechanism survey.

## Build / test commands

```sh
# unit tests (pure stdlib):
python3 -m unittest discover -s tests

# CLI without installing:
uv run callsight --help
# web UI (optional deps via the ui extra):
uv run --extra ui callsight ui          # http://127.0.0.1:8321
# installed:
uv tool install . && callsight --help
# docs site (GitHub Pages content):
uv run --group docs mkdocs serve        # local preview
uv run --group docs mkdocs build --strict   # must stay clean

# end-to-end smoke test (Make integration):
cd tests/matrixlab
make clean && make instrument          # clean REQUIRED when switching profiles
TRACE_ENABLE=1 TRACE_MAX=1000000 timeout 5 ./bin/matrixlab.instr
uv run callsight analyze traces/ --top 20

# end-to-end smoke test (CMake integration, cmake via uvx — no system cmake):
cd tests/cmake_demo
uvx cmake -DCALLSIGHT_INSTRUMENT=ON \
    "-DCALLSIGHT_COMMAND=python3;$PWD/../../src/callsight/cli.py" -B build-instr
uvx cmake --build build-instr
TRACE_ENABLE=1 TRACE_MAX=200000 ./build-instr/demo
uv run callsight analyze traces/ --exe build-instr/demo

# end-to-end smoke test (remote streaming):
uv run --extra stream callsight serve --port 9001 --out /tmp/stream_traces &
gcc -O2 -I src/callsight/runtime -o /tmp/trace_stream \
    src/callsight/stream/trace_stream.c src/callsight/stream/zstd.c
/tmp/trace_stream /tk_smoke 127.0.0.1 9001 &
cd tests/matrixlab
TRACE_ENABLE=1 TRACE_SHM=/tk_smoke TRACE_MAX=500000 timeout 5 ./bin/matrixlab.instr
# client exits after drain; then:
uv run callsight analyze /tmp/stream_traces --exe bin/matrixlab.instr
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
  `ui` (FastAPI/uvicorn, under `src/callsight/ui/`) and `stream`
  (zstandard, `src/callsight/serve.py`) — the core must import cleanly
  without them.
- Streaming mode (`TRACE_SHM`) must never stall the workload: when the
  shared-memory ring is full, drop events and count them in the ring
  header. No disk or network I/O in the traced process.
- The hook hot path in trace.c is per-thread and lock-free; anything
  thread-related (e.g. TRACE_THREADS matching) must not use process-global
  state (no strtok) and stays out of the flush path.
- The wire protocol and ring layout live only in
  `src/callsight/runtime/trace_shm.h`; bump `TRACE_SHM_VERSION` /
  `TRACE_STREAM_VERSION` on any layout change.
- The instrumentation runtime (`src/callsight/runtime/trace.c`) must never
  be compiled with `-finstrument-functions`; every function there carries
  `__attribute__((no_instrument_function))`, and both build integrations
  compile it without the flag.
- Runtime config env vars use the `TRACE_*` prefix (`TRACE_ENABLE`,
  `TRACE_DIR`, `TRACE_MAX`). matrixlab's own config uses `MATRIXLAB_*` —
  keep the two namespaces separate.
- Selection rules live only in the target project's `trace.config`; do not
  hard-code exclude lists in Makefiles or CMake files. Supported
  directives: `include`, `exclude`, `exclude-func`, `include-func`
  (call-subtree selection via `callgraph.py`; auto-excludes must keep the
  substring-collision guard).
- `CALLSIGHT_COMMAND` in CMake is a cache variable — pass it with `-D`
  (a plain `set()` before `include(CallSight)` gets shadowed by the cache
  definition under CMP0126).
- Do not commit `.venv/`, `build*/`, `bin/`, `traces/`, or `site/`
  (see .gitignore).
- Docs live in `docs/` (MkDocs Material, `mkdocs.yml`); `docs/status.md`
  includes the root `STATUS.md` — keep the status in STATUS.md, not in the
  docs page. CI deploys the site to GitHub Pages on docs changes.
- Releases are tag-driven: pushing `v*` runs smoke tests, builds dists,
  creates a GitHub Release, and publishes to PyPI (trusted publishing).
