# callsight — project status

**callsight** adds compile-time entry/exit tracing to any C/C++ project
(Make/CMake, Linux) with zero source edits: selective coverage from one
`trace.config`, a lean per-thread runtime, hotspot analysis, flame-graph
export, a web UI, and remote streaming from constrained devices over
ZSTD-compressed TCP. Selective coverage requires **GCC** — the
`-finstrument-functions-exclude-*` flags it relies on are GCC-only ([LLVM
#15627](https://github.com/llvm/llvm-project/issues/15627)); Clang can only
instrument everything, and callsight says so before the build starts.

## Where we are

| component | state | evidence |
|---|---|---|
| Core engine (`flags`, `analyze`) | ✅ done | 130 unit tests (incl. full `analyze.py` coverage), run on Python 3.9 and 3.13 |
| Streaming analyzer | ✅ done | events matched as they are read; 1M-event trace: 190 MB → 14 MB peak RSS, identical report |
| Flame graph / JSON export (`analyze --format folded\|json`) | ✅ done | collapsed stacks for flamegraph.pl & speedscope; folded self-time total matches the text report exactly |
| Toolchain check (GCC-only exclude flags) | ✅ done | compiler detected from `$CC` / `CMAKE_C_COMPILER`; a selective config under clang fails with an explanation, not a driver error |
| CLI (`init` / `scan` / `select` / `flags` / `analyze`) | ✅ done | adoption drill on a foreign copy of matrixlab: build → run → analyze clean |
| Function/task-level selection (`include-func` + call graph) | ✅ done | `include-func workload_sort` e2e: only the 31-function subtree traced, `unmatched_exits=0` |
| Thread-level selection (`TRACE_THREADS`) | ✅ done | `TRACE_THREADS='sort-*'` e2e: 3 of 26 threads traced |
| Make integration | ✅ done | matrixlab e2e: `unmatched_exits=0` |
| CMake integration | ✅ done | `tests/cmake_demo` e2e: `unmatched_exits=0`; normal builds carry zero hooks |
| Web UI (`callsight ui`) | ✅ done | full API-driven cycle (browse → config → build → run → report) on both fixtures |
| UI Config Builder (folder scan → checkbox selection → `trace.config`) | ✅ done | ctags/regex symbol enumeration, dry-run preview, apply-to-disk; smoke-tested on matrixlab (35 files / 315 functions) |
| Bundled ctags (`callsight provision`, UI auto-download) | ✅ done | static universal-ctags 6.2.1 release assets (x86_64/aarch64), sha256-verified install into `~/.callsight/bin`; regex fallback when unavailable |
| Remote streaming (`TRACE_SHM` → `trace_stream` → zstd/TCP → `callsight serve`) | ✅ done | 500k events / 26 threads over localhost, `unmatched_exits=0`; drop-counting verified under ring overflow |
| Docs site (GitHub Pages) | ✅ done | hand-built static site in `docs/` (landing, getting started, configuration, analysis, web UI, streaming, architecture, reference); link/anchor checker gates every deploy |
| CI | ✅ done | unit + 3 end-to-end smoke jobs on every push |
| Release pipeline | ✅ wired | tag `v*` → GitHub Release + PyPI (trusted publishing) |
| First PyPI release | ✅ done | v0.1.0 on PyPI via trusted publishing; releases are tag-driven (`git tag vX.Y.Z && git push --tags`) |

## Roadmap

**Next up**
- Live stream view in the web UI (watch a remote device's trace arrive)
- Render the folded export as a flame graph inside the web UI (the CLI
  already emits it; the UI still shows tables only)
- `-finstrument-functions-after-inlining` opt-in for Clang builds

**Explored, on deck** (see the
[compiler-mechanism survey](https://harshithsunku.github.io/callsight/architecture.html#mechanisms))
- Runtime on/off without rebuild via `-fpatchable-function-entry`
  (XRay-style sled patching) — the design reference for "trace a live
  production process for 5 seconds"
- `-fsanitize-coverage` as a low-overhead edge-level backend

**Deliberate non-goals**
- GCC plugin API (version-locked), gcov-style coverage counting,
  eBPF/uprobes (root requirements)

## Verify it yourself

```sh
python3 -m unittest discover -s tests          # unit tests
cd tests/matrixlab && make clean && make instrument
TRACE_ENABLE=1 TRACE_MAX=1000000 timeout 5 ./bin/matrixlab.instr
uv run callsight analyze traces/ --top 20      # expect unmatched_exits=0
```

Streaming and CMake smoke procedures:
[AGENTS.md](https://github.com/harshithsunku/callsight/blob/main/AGENTS.md).

## Links

- Docs: https://harshithsunku.github.io/callsight/
- Repo: https://github.com/harshithsunku/callsight
- Compiler-mechanism survey: [architecture — compiler mechanisms](https://harshithsunku.github.io/callsight/architecture.html#mechanisms)
- Contributing/conventions: [AGENTS.md](https://github.com/harshithsunku/callsight/blob/main/AGENTS.md)
