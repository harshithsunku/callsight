# callsight — project status

**callsight** adds compile-time entry/exit tracing to any C/C++ project
(Make/CMake, Linux) with zero source edits: selective coverage from one
`trace.config`, a lean per-thread runtime, exact call counts and latency
percentiles, flame-graph and Perfetto export, a web UI, and remote streaming
from constrained devices over ZSTD-compressed TCP. Capture is bounded by
default and says so in the report when a bound is reached. Selective coverage
requires **GCC** — the `-finstrument-functions-exclude-*` flags it relies on
are GCC-only ([LLVM #15627](https://github.com/llvm/llvm-project/issues/15627));
Clang can only instrument everything, and callsight says so before the build
starts.

## Where we are

| component | state | evidence |
|---|---|---|
| Core engine (`flags`, `analyze`) | ✅ done | 259 unit tests, run on Python 3.9 and 3.13 |
| Bounded capture (`TRACE_MAX_MB`, `TRACE_FULL`, free-space floor) | ✅ done | budget of 4 MB holds under a run that would write ~66 MB; `wrap` keeps the tail; every stop reason reported in-band |
| Summary mode (`TRACE_MODE=summary`) | ✅ done | constant memory and constant output: 2.8 KB for a run 10× longer than one writing 244 MB of events |
| Latency percentiles (p50/p90/p99 + exact min/max) | ✅ done | log-scale histogram per function in both modes; bracket a known 2 ms sleep in the runtime suite |
| Trace format v2 (PIE bias, clock anchors, markers) | ✅ done | `header_size`-gated so later versions stay readable; v1 files still analyze |
| PIE support (no `-no-pie` requirement) | ✅ done | cmake_demo builds a PIE by default and resolves every symbol in CI |
| Fast clock (invariant TSC / `cntvct_el0`) | ✅ done | 12.0 ns/hook vs 16.2 for `clock_gettime`, measured by `tests/bench/run_bench.py` |
| Runtime correctness (tid reuse, fork, write errors, races) | ✅ done | 28 end-to-end runtime tests + a ThreadSanitizer leg over the threaded paths |
| Portable agents (32-bit, big-endian) | ✅ done | agent writes its native byte order and the host detects it; ARMv7 / PowerPC32 / s390x cross-built in CI, run under qemu, analyzed on x86 — exact call counts, not just "it parsed" |
| Export formats (`folded`, `json`, `chrome`, `callers`) | ✅ done | folded total matches the text report exactly; chrome opens in ui.perfetto.dev |
| Regression gate (`callsight diff --fail-over`) | ✅ done | exact call counts make the comparison real; exits non-zero over budget |
| Ergonomics (`callsight run`, `callsight doctor`) | ✅ done | one command from binary to report; doctor checks toolchain, config, disk |
| Streaming analyzer | ✅ done | events matched as they are read; 1M-event trace: 190 MB → 14 MB peak RSS |
| Toolchain check (GCC-only exclude flags) | ✅ done | compiler detected from `$CC` / `CMAKE_C_COMPILER`; a selective config under clang fails with an explanation, not a driver error |
| Function/task-level selection (`include-func` + call graph) | ✅ done | `include-func workload_sort` e2e: only the 31-function subtree traced |
| Thread-level selection (`TRACE_THREADS`) | ✅ done | `TRACE_THREADS='sort-*'` e2e: 3 of 26 threads traced |
| Make / CMake integration | ✅ done | both fixtures e2e: `unmatched_exits=0`; normal builds carry zero hooks |
| Hardware counters per function | ✅ done | exact instructions/cache-misses/branch-misses for named functions; 223.0 instr/call identical across 5 runs where wall time moved 5x; all 28 runtime tests pass on aarch64 |
| Counter overhead, measured | ✅ done | `tests/bench/run_bench.py` gained counted rows: on aarch64, counting every call of a do-nothing function costs 1970 ns/hook against 98 uncounted — the worst case, and why the guard rail exists |
| Counter guard rail + honesty checks | ✅ done | refuses a counter that never reaches hardware; demotes functions shorter than ~20x a read and names them; refuses a 4th event rather than let the kernel scale counts |
| Live view (`callsight ui` → Live) | ✅ done | incremental Accumulator + per-file offsets over SSE; local run or a `callsight serve` directory |
| Web UI (`callsight ui`) | ✅ done | full API-driven cycle, percentiles and capture notices in the table, interactive flame graph with zoom |
| UI Config Builder | ✅ done | ctags/regex symbol enumeration, dry-run preview, apply-to-disk |
| Bundled ctags (`callsight provision`) | ✅ done | static universal-ctags 6.2.1 (x86_64/aarch64), sha256-verified |
| Remote streaming (`TRACE_SHM` → `trace_stream` → zstd/TCP → `callsight serve`) | ✅ done | 400k events / 26 threads, `unmatched_exits=0`; handshake carries the device clock; server output rotates and is capped |
| Docs site (GitHub Pages) | ✅ done | hand-built static site in `docs/`; link/anchor checker gates every deploy |
| CI | ✅ done | unit + UI + runtime + TSan + 3 cross-architecture (qemu) + 3 end-to-end smoke jobs on every push |
| Release pipeline | ✅ wired | tag `v*` → GitHub Release + PyPI (trusted publishing) |

## Roadmap

**Next up**
- Pure-Python ELF/DWARF symbolizer (optional extra) to drop the binutils
  dependency for cross-compiled targets entirely. The symbol half already
  exists — `elf.py` reads ELF32/ELF64 in both byte orders and is checked
  against `readelf` on real ARM, PowerPC and s390x binaries — so what remains
  is DWARF line tables.
- Counter support on the streaming path end to end. The records and markers
  already travel through the ring and the wire, but the only cross-endian
  proof today is the file path, not the socket path.
- Per-call counter outliers in the UI: event mode already records a value per
  call, so "which single call did 10x the instructions" is answerable and not
  yet surfaced.

**Explored, on deck** (see the
[compiler-mechanism survey](https://harshithsunku.github.io/callsight/architecture.html#mechanisms))
- Runtime on/off without rebuild via `-fpatchable-function-entry`
  (XRay-style sled patching) — the design reference for "trace a live
  production process for 5 seconds"
- `-fsanitize-coverage` as a low-overhead edge-level backend
- `-finstrument-functions-after-inlining` opt-in

**Deliberate non-goals**
- GCC plugin API (version-locked), gcov-style coverage counting,
  eBPF/uprobes (root requirements)
- Kernel time, off-CPU time, and whole-system profiling — that is `perf`'s
  ground and callsight does not try to take it

## Verify it yourself

```sh
python3 -m unittest discover -s tests          # unit tests
python3 tests/runtime/test_runtime.py          # the C runtime, end to end
python3 tests/bench/run_bench.py               # the overhead numbers above

cd tests/matrixlab && make clean && make instrument
callsight run --timeout 5 -- ./bin/matrixlab.instr    # expect unmatched_exits=0
```

Streaming and CMake smoke procedures:
[AGENTS.md](https://github.com/harshithsunku/callsight/blob/main/AGENTS.md).

## Links

- Docs: https://harshithsunku.github.io/callsight/
- Repo: https://github.com/harshithsunku/callsight
- Capture limits: [capture](https://harshithsunku.github.io/callsight/capture.html)
- Hardware counters: [counters](https://harshithsunku.github.io/callsight/counters.html)
- Compiler-mechanism survey: [architecture — compiler mechanisms](https://harshithsunku.github.io/callsight/architecture.html#mechanisms)
- Contributing/conventions: [AGENTS.md](https://github.com/harshithsunku/callsight/blob/main/AGENTS.md)
