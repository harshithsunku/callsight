# AGENTS.md

## What this repo is

Instrumentation experiments around a demo C project. Two top-level dirs:

- `instrument/` — portable compile-time tracing toolkit (the product).
  Self-contained; must stay free of matrixlab-specific dependencies.
- `matrixlab/` — demo C11/pthreads workload used to exercise the toolkit.

## Build / test commands

```sh
cd matrixlab
make                 # debug build (default profile)
make instrument      # instrumented build -> bin/matrixlab.instr
make clean           # REQUIRED when switching profiles (objects are profile-specific)

# end-to-end verification of the toolkit:
TRACE_ENABLE=1 TRACE_MAX=1000000 timeout 5 ./bin/matrixlab.instr
python3 ../instrument/trace_analyze.py traces/ --top 20

# selection logic check (no build needed):
python3 instrument/gen_flags.py --config instrument/trace.config \
    --scan matrixlab/src --print
```

There is no unit test suite; the end-to-end run above is the smoke test.
A correct run reports `unmatched_exits=0` and plausible hotspots.

## Conventions

- C: C11, `-Wall -Wextra -pedantic`; match the existing comment style
  (short `/* ... */` above each function).
- Python: stdlib only — no third-party dependencies in the toolkit.
- The instrumentation runtime (`instrument/trace.c`) must never be compiled
  with `-finstrument-functions`; every function there carries
  `__attribute__((no_instrument_function))`.
- Runtime config env vars use the `TRACE_*` prefix (`TRACE_ENABLE`,
  `TRACE_DIR`, `TRACE_MAX`). matrixlab's own config uses `MATRIXLAB_*` —
  keep the two namespaces separate.
- Selection rules live only in `instrument/trace.config`; do not hard-code
  exclude lists in Makefiles.
- Do not commit `build/`, `bin/`, or `traces/` (covered by .gitignore).
