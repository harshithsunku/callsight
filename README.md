# log — compile-time instrumentation lab

Experiments in adding logging/timing infrastructure to C/C++ projects **at
compile time, without modifying the project's source code**.

## Layout

- **`instrument/`** — the reusable toolkit. Entry/exit hooks for every
  function via `-finstrument-functions`, selective coverage (files, folders,
  functions) controlled from a single `trace.config`, per-thread binary
  tracing, and an offline analyzer that turns traces into hotspot reports.
  Designed to be dropped into any GCC/Clang codebase. See
  [`instrument/README.md`](instrument/README.md).
- **`matrixlab/`** — the demo target: a multi-threaded numerical computation
  engine (matrix, stats, signal, crypto, sort, graph, compression modules +
  thread pool). Stands in for "a project with many files"; the toolkit
  treats it as an arbitrary external codebase.

## Quick start (demo)

```sh
cd matrixlab
make instrument                                   # builds bin/matrixlab.instr
TRACE_ENABLE=1 TRACE_MAX=2000000 ./bin/matrixlab.instr
python3 ../instrument/trace_analyze.py traces/ --top 20
```

Edit `instrument/trace.config` to change what gets instrumented
(include/exclude folders, files, individual functions), then rebuild.

Other matrixlab builds: `make` (debug), `make release`, `make symbols`,
`./run.sh` to launch. Note `build/` objects are profile-specific — run
`make clean` when switching profiles.
