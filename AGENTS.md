# AGENTS.md

## What this repo is

**callsight** — an open-source compile-time function-tracing toolkit for
C/C++ projects (Make/CMake, Linux). Adopted into a target project via
the `callsight` CLI (`uv` package); adds entry/exit hooks at compile time
with zero source edits, controlled by a single `trace.config`. Selective
coverage requires GCC: `-finstrument-functions-exclude-file-list` /
`-exclude-function-list` do not exist in Clang (LLVM issue #15627).

Layout:

- `src/callsight/` — the Python package (stdlib-only core): `cli.py` (init /
  scan / flags / analyze / ui), `flags.py` (selection + `render_config`),
  `analyze.py`, `symbols.py` (function enumeration for the UI config
  builder: ctags when on PATH, `callgraph.find_definitions` regex fallback).
- `src/callsight/provision.py` — stdlib-only; downloads the bundled static
  ctags (release assets `callsight-ctags-linux-<arch>` + `.sha256`) into
  `$CALLSIGHT_HOME/bin` (default `~/.callsight/bin`); `find_ctags()` checks
  PATH first, then the bundled copy.
- `src/callsight/ui/` — optional web UI (FastAPI + tabbed single-page
  frontend in `static/`: "Workflow" = build/run/analyze, "Config Builder" =
  folder scan → checkbox selection → generates `trace.config` via
  `/api/functions` + `/api/config/generate`); third-party deps live in the
  `ui` extra, imported only by `callsight ui`.
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
- `tests/test_symbols.py` — unit tests for `symbols.py` (both strategies).
- `tests/test_config_generate.py` — unit tests for `flags.render_config`.
- `tests/test_callgraph.py` — unit tests for `callgraph.py` + include-func selection.
- `tests/test_provision.py` — unit tests for `provision.py` (mocked downloads).
- `tests/test_analyze.py` — unit tests for `analyze.py`: file parsing,
  enter/exit matching, `resolve()` (mocked addr2line), report formats.
- `tests/test_compiler.py` — unit tests for compiler detection and the
  GCC-only exclude-flag guard.
- `tests/test_ui_report.py` — unit tests for the UI report and flame
  endpoints; skipped unless the `ui` extra is installed.
- `tests/runtime/` — end-to-end tests for the C runtime itself
  (`runtime_probe.c` + `test_runtime.py`): segment integrity under thread-id
  reuse, fork safety, budget/wrap/free-space/write-failure paths, clock
  modes, summary mode, PIE symbolization, ground-truth accuracy. Needs a
  compiler, so it lives in a subdirectory `unittest discover -s tests` does
  not recurse into — run it explicitly.
- `tests/bench/` — `bench.c` + `run_bench.py`: the published overhead
  numbers. Any claim about ns/hook comes from here or it is not made.
- `docs/` — the published documentation site: hand-built static HTML (no
  generator), one file per page plus `assets/styles.css`, `assets/docs.js`,
  `assets/flamegraph.svg` and `screenshots/`. `docs/architecture.html`
  carries the compiler-mechanism survey.
- `.github/scripts/check_docs_links.py` — stdlib link/anchor checker; the
  pages workflow runs it before publishing.

## Build / test commands

```sh
# unit tests (pure stdlib); CI runs both ends of requires-python:
python3 -m unittest discover -s tests
uv run --python 3.9 python -m unittest discover -s tests
# the UI tests skip themselves without FastAPI:
uv run --extra ui python -m unittest discover -s tests

# the C runtime, end to end (needs a compiler; not picked up by discover):
python3 tests/runtime/test_runtime.py
# overhead numbers (rebuilds the workload twice, ~1 min):
python3 tests/bench/run_bench.py

# race-check the threaded paths:
gcc -std=c11 -O1 -g -fsanitize=thread -I src/callsight/runtime \
    -finstrument-functions -c -o /tmp/probe.o tests/runtime/runtime_probe.c
gcc -std=c11 -O1 -g -fsanitize=thread -I src/callsight/runtime \
    -c -o /tmp/rt.o src/callsight/runtime/trace.c
gcc -fsanitize=thread -o /tmp/probe /tmp/probe.o /tmp/rt.o -lpthread
TSAN_OPTIONS=halt_on_error=1:exitcode=1 TRACE_ENABLE=1 TRACE_DIR=/tmp/tt \
    TRACE_SEG_MB=1 TRACE_MAX_MB=16 /tmp/probe threads 4 3000

# CLI without installing:
uv run callsight --help
# web UI (optional deps via the ui extra):
uv run --extra ui callsight ui          # http://127.0.0.1:8321
# installed:
uv tool install . && callsight --help
# docs site (GitHub Pages content) — plain static files, no build step:
python3 -m http.server -d docs 8000            # local preview
python3 .github/scripts/check_docs_links.py docs   # must stay clean

# end-to-end smoke test (Make integration):
cd tests/matrixlab
make clean && make instrument          # clean REQUIRED when switching profiles
uv run callsight run --timeout 5 --max-events 1000000 -- ./bin/matrixlab.instr

# end-to-end smoke test (CMake integration, cmake via uvx — no system cmake):
cd tests/cmake_demo
uvx cmake -DCALLSIGHT_INSTRUMENT=ON \
    "-DCALLSIGHT_COMMAND=python3;$PWD/../../src/callsight/cli.py" -B build-instr
uvx cmake --build build-instr
TRACE_ENABLE=1 TRACE_MAX=200000 ./build-instr/demo
uv run callsight analyze traces/ --exe build-instr/demo

# end-to-end smoke test (remote streaming):
# Sync the extra FIRST — resolving it inside the backgrounded server races
# the client, which then connects to a closed port (CI hit exactly this).
uv sync --extra stream
PYTHONUNBUFFERED=1 uv run --extra stream callsight serve --port 9001 \
    --out /tmp/stream_traces >/tmp/serve.log 2>&1 &
until grep -q listening /tmp/serve.log; do sleep 1; done
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
  `analyze.py`, `symbols.py`). Third-party deps are allowed only in the
  optional extras —
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
- The two `-finstrument-functions-exclude-*` flags are GCC-only. Both build
  integrations pass `--compiler-cmd`, and `flags.check_compiler` refuses a
  selective config under clang with an explanation — never emit those flags
  for a detected clang, and never let a failed detection block a build
  (unknown is treated as GCC).
- `callgraph.expand` must stay FIFO: a depth-limited walk that pops
  depth-first lets a long path claim a node past the limit and silently drop
  the subtree a shorter path would have expanded.
- `analyze.py` streams: events are matched as they are read and never
  collected into a list. Anything added there must keep memory proportional
  to functions/threads, not to the event count. (`--format chrome` makes two
  passes over the files rather than buffering a timeline, for this reason.)
- Capture must stay bounded. `TRACE_MAX_MB` defaults to 512 and reaching any
  limit is recorded as an in-band marker event — a capture that ends early
  must never look like a capture that finished.
- Process-wide counters are charged **per flush** (once per 8192 events),
  never per event. A shared atomic on the hot path serializes every thread on
  one cache line exactly when tracing is heaviest.
- Trace files are opened `O_EXCL` and never appended to: the kernel recycles
  thread ids, and appending would write a second file header into the middle
  of an existing capture, shifting every later record off the 32-byte grid
  with nothing in the file to reveal it.
- The runtime is fork-aware (`pthread_atfork` child handler) and uses raw
  descriptors rather than stdio — stdio's buffer would be duplicated into the
  child. Every `write()` return value is checked.
- Event kind 2 is a marker, not an exit. Any reader that treats an unknown
  kind as an exit corrupts the whole match.
- The trace header is version-gated by `header_size`: readers skip to it
  rather than assuming a size, and version 1 files must keep analyzing.
- The shm ring and the stream handshake carry the tracer's clock calibration
  and load bias. Without them the server records raw cycle counts as
  nanoseconds — a trace that looks fine and is wrong by the clock ratio.
- `CALLSIGHT_COMMAND` in CMake is a cache variable — pass it with `-D`
  (a plain `set()` before `include(CallSight)` gets shadowed by the cache
  definition under CMP0126).
- Do not commit `.venv/`, `build*/`, `bin/`, `traces/`, or `site/`
  (see .gitignore).
- Published performance numbers come from `tests/bench/run_bench.py`. If a
  number in the README or docs cannot be reproduced by running it, change the
  number, not the claim.
- Docs live in `docs/` as hand-built static HTML — no site generator, no
  build step: the pages workflow checks links and uploads `docs/` as-is.
  Each page carries its own nav/footer (copy an existing page as the
  template), pulls the shared `assets/styles.css` + `assets/docs.js`, and
  must set the `active` class on its own nav link, a `<title>`, and a
  meta description. Adding a page means editing the nav and footer of every
  other page — the checker catches broken links, not missing ones. Never hard-code colors in a page: use the CSS custom
  properties, which carry both themes. Run
  `python3 .github/scripts/check_docs_links.py docs` after editing — every
  local link and `#anchor` must resolve.
- Status lives in `STATUS.md` only; the site links to it rather than
  duplicating it.
- Docs screenshots and `assets/flamegraph.svg` are generated from real runs
  of `tests/matrixlab`, not mocked. Regenerate them rather than editing.
- Releases are tag-driven: pushing `v*` builds the static
  `callsight-ctags-linux-{x86_64,aarch64}` assets (universal-ctags p6.2.1,
  cross-compiled for aarch64, that leg is continue-on-error), runs smoke
  tests, builds dists, creates a GitHub Release with dists + ctags assets,
  and publishes to PyPI (trusted publishing).
