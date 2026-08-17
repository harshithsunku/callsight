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
| Core engine (`flags`, `analyze`) | ✅ done | 186 unit tests, run on Python 3.9 and 3.13 |
| Bounded capture (`TRACE_MAX_MB`, `TRACE_FULL`, free-space floor) | ✅ done | budget of 4 MB holds under a run that would write ~66 MB; `wrap` keeps the tail; every stop reason reported in-band |
| Summary mode (`TRACE_MODE=summary`) | ✅ done | constant memory and constant output: 2.8 KB for a run 10× longer than one writing 244 MB of events |
| Latency percentiles (p50/p90/p99 + exact min/max) | ✅ done | log-scale histogram per function in both modes; bracket a known 2 ms sleep in the runtime suite |
| Trace format v2 (PIE bias, clock anchors, markers) | ✅ done | `header_size`-gated so later versions stay readable; v1 files still analyze |
| PIE support (no `-no-pie` requirement) | ✅ done | cmake_demo builds a PIE by default and resolves every symbol in CI |
| Fast clock (invariant TSC / `cntvct_el0`) | ✅ done | 12.0 ns/hook vs 16.2 for `clock_gettime`, measured by `tests/bench/run_bench.py` |
| Runtime correctness (tid reuse, fork, write errors, races) | ✅ done | 20 end-to-end runtime tests + a ThreadSanitizer leg over the threaded paths |
| Export formats (`folded`, `json`, `chrome`, `callers`) | ✅ done | folded total matches the text report exactly; chrome opens in ui.perfetto.dev |
| Regression gate (`callsight diff --fail-over`) | ✅ done | exact call counts make the comparison real; exits non-zero over budget |
| Ergonomics (`callsight run`, `callsight doctor`) | ✅ done | one command from binary to report; doctor checks toolchain, config, disk |
| Streaming analyzer | ✅ done | events matched as they are read; 1M-event trace: 190 MB → 14 MB peak RSS |
| Toolchain check (GCC-only exclude flags) | ✅ done | compiler detected from `$CC` / `CMAKE_C_COMPILER`; a selective config under clang fails with an explanation, not a driver error |
| Function/task-level selection (`include-func` + call graph) | ✅ done | `include-func workload_sort` e2e: only the 31-function subtree traced |
| Thread-level selection (`TRACE_THREADS`) | ✅ done | `TRACE_THREADS='sort-*'` e2e: 3 of 26 threads traced |
| Make / CMake integration | ✅ done | both fixtures e2e: `unmatched_exits=0`; normal builds carry zero hooks |
| Web UI (`callsight ui`) | ✅ done | full API-driven cycle, percentiles and capture notices in the table, interactive flame graph with zoom |
| UI Config Builder | ✅ done | ctags/regex symbol enumeration, dry-run preview, apply-to-disk |
| Bundled ctags (`callsight provision`) | ✅ done | static universal-ctags 6.2.1 (x86_64/aarch64), sha256-verified |
| Remote streaming (`TRACE_SHM` → `trace_stream` → zstd/TCP → `callsight serve`) | ✅ done | 400k events / 26 threads, `unmatched_exits=0`; handshake carries the device clock; server output rotates and is capped |
| Docs site (GitHub Pages) | ✅ done | hand-built static site in `docs/`; link/anchor checker gates every deploy |
| CI | ✅ done | unit + UI + runtime + TSan + 3 end-to-end smoke jobs on every push |
| Release pipeline | ✅ wired | tag `v*` → GitHub Release + PyPI (trusted publishing) |

## Roadmap

**Next up**
- **Hardware counters for selected functions** — `perf_event_open` in
  per-thread counting mode: exact instructions retired and cache misses for
  the functions you name, chosen in `trace.config` the same way hooks
  already are.

  Measured before committing to a design, because the platform story is
  narrower than it looks:

  | | x86-64 (in an LXC container) | aarch64 (Snapdragon 845, bare metal) |
  |---|---|---|
  | `perf_event_open` succeeds | yes | yes |
  | counters actually count | **no — silently zero** | yes, exactly |
  | userspace fast path | `cap_user_rdpmc=1` but never scheduled | **unavailable** (`cap_user_rdpmc=0`) |
  | cost per read | — | **1381 ns** (syscall only) |

  Linux does not enable userspace counter reads on arm64, so every read
  there is a syscall — 12x the whole hook cost, twice per call. Per-*call*
  counters are therefore a bare-metal-x86 feature; the portable design is
  per-*function* selection, where 2.8 us against a 1 ms function is 0.3%.
  The bulk of the work is refusing to report a number when the counter is
  not really running: on the x86 box above, a naive implementation would
  report zero instructions for every function and look perfectly healthy.

  What survives the scrutiny is the payoff. The same loop run six times
  varied by **one instruction**, against +/-0.24% for wall time on an idle
  machine — four orders of magnitude steadier, which is what would make
  `callsight diff` a regression gate you can trust rather than one you have
  to eyeball.
- Live stream view in the web UI (watch a remote device's trace arrive)
- Pure-Python ELF/DWARF symbolizer (optional extra) to drop the binutils
  dependency for cross-compiled targets entirely

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
- Compiler-mechanism survey: [architecture — compiler mechanisms](https://harshithsunku.github.io/callsight/architecture.html#mechanisms)
- Contributing/conventions: [AGENTS.md](https://github.com/harshithsunku/callsight/blob/main/AGENTS.md)
