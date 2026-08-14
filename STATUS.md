# callscope — project status

**callscope** adds compile-time entry/exit tracing to any C/C++ project
(GCC/Clang, Make/CMake) with zero source edits: selective coverage from one
`trace.config`, a lean per-thread runtime, hotspot analysis, a web UI, and
remote streaming from constrained devices over ZSTD-compressed TCP.

## Where we are

| component | state | evidence |
|---|---|---|
| Core engine (`flags`, `analyze`) | ✅ done | 17/17 unit tests |
| CLI (`init` / `scan` / `flags` / `analyze`) | ✅ done | adoption drill on a foreign copy of matrixlab: build → run → analyze clean |
| Make integration | ✅ done | matrixlab e2e: `unmatched_exits=0` |
| CMake integration | ✅ done | `tests/cmake_demo` e2e: `unmatched_exits=0`; normal builds carry zero hooks |
| Web UI (`callscope ui`) | ✅ done | full API-driven cycle (browse → config → build → run → report) on both fixtures |
| Remote streaming (`TRACE_SHM` → `trace_stream` → zstd/TCP → `callscope serve`) | ✅ done | 500k events / 26 threads over localhost, `unmatched_exits=0`; drop-counting verified under ring overflow |
| Docs site (GitHub Pages) | ✅ done | MkDocs Material, auto-deployed by CI |
| CI | ✅ done | unit + 3 end-to-end smoke jobs on every push |
| Release pipeline | ✅ wired | tag `v*` → GitHub Release + PyPI (trusted publishing) |
| First PyPI release | ⏳ pending | one-time pending-publisher setup on pypi.org, then push tag `v0.1.0` |

## Roadmap

**Next up**
- Live stream view in the web UI (watch a remote device's trace arrive)
- Flame graph / call-graph report views (the events carry caller addresses)
- `-finstrument-functions-after-inlining` opt-in for Clang builds

**Explored, on deck** (see the
[compiler-mechanism survey](https://github.com/harshithsunku/callscope/blob/main/docs/instrumentation-options.md))
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
uv run callscope analyze traces/ --top 20      # expect unmatched_exits=0
```

Streaming and CMake smoke procedures:
[AGENTS.md](https://github.com/harshithsunku/callscope/blob/main/AGENTS.md).

## Links

- Docs: https://harshithsunku.github.io/callscope/
- Repo: https://github.com/harshithsunku/callscope
- Compiler-mechanism survey: [docs/instrumentation-options.md](https://github.com/harshithsunku/callscope/blob/main/docs/instrumentation-options.md)
- Contributing/conventions: [AGENTS.md](https://github.com/harshithsunku/callscope/blob/main/AGENTS.md)
