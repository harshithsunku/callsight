# Compile-time function instrumentation: GCC/Clang options survey

What each compiler mechanism can do, what it costs, and how it maps to
callsight's backends. Overhead figures are order-of-magnitude, per event,
assuming a lean hook (no I/O in the hot path).

## Summary table

| mechanism | compilers | granularity | runtime toggle | overhead/event | callsight verdict |
|---|---|---|---|---|---|
| `-finstrument-functions` | GCC, Clang | function entry/exit | no (compile-time) | ~30–60 ns | **default backend** |
| `-finstrument-functions-after-inlining` | Clang | post-inline entry/exit | no | ~30–60 ns | Clang enhancement |
| `-pg` (mcount/gprof) | GCC, Clang | function entry | no | ~50–100 ns | legacy, rejected |
| `-fpatchable-function-entry` | GCC ≥ 8, Clang ≥ 11 | NOP sleds at entry | **yes** (patch sleds) | ~0 when off, ~few ns on | Phase-3 candidate |
| `-fsanitize-coverage` | Clang, GCC ≥ 12 | edge / PC guard | no | ~5–20 ns | alternative backend, evaluate |
| XRay (`-fxray-instrument`) | Clang | entry/exit sleds | **yes** (official API) | ~0 when off | reference design for streaming |
| `-fprofile-arcs` (gcov) | GCC, Clang | edge counters | no | counter inc only | out of scope (no timing) |
| GCC plugin API | GCC (version-locked) | arbitrary (GIMPLE) | possible | varies | non-goal |

## `-finstrument-functions` — the current backend

Emits a call to `__cyg_profile_func_enter(this_fn, call_site)` /
`__cyg_profile_func_exit(...)` at every function boundary, including
`static` functions. Selection at compile time via
`-finstrument-functions-exclude-file-list=` and
`-finstrument-functions-exclude-function-list=` (both substring-matched,
file list matched against the file a function is *defined* in — headers
work). Excluded code emits no hook at all, so selection is free at runtime.

Caveats: inlined functions emit no hooks (no call boundary exists); hooks
fire even when you don't want data, so runtime gating (`TRACE_ENABLE`) is a
flag check per event; `-no-pie` (or offset bookkeeping) is needed to map
addresses back to symbols.

This is callsight's default: universal (GCC + Clang, C + C++), simple, and
the exclude lists give exactly the "one config file" selection model.

## `-finstrument-functions-after-inlining` (Clang)

Same hooks, but inserted *after* inlining, so functions that survived
inlining get hooked even if they were inlined at some call sites, and you
see the real optimized call graph. Candidate extra flag when the toolchain
is Clang; needs an analyzer-side note that the call graph differs from the
source-level one.

## `-pg` / mcount (gprof)

The original: entry-only hook into `mcount`, plus flat-profile sampling.
Call-graph arcs only (no exit timing per call, timing comes from sampling),
PLT/shared-library blind spots, and effectively unmaintained semantics with
modern optimization. Documented for completeness; callsight does not use it.

## `-fpatchable-function-entry=N,M`

Emits `N` NOPs at function entry (`M` of them before the prologue) plus a
`__patchable_function_entries` section listing their addresses — the
mechanism the Linux kernel's ftrace and uftrace's dynamic mode use. A
runtime can then *patch* sleds into hook calls (or a trampoline) and back,
giving true zero-cost-when-off, toggleable-at-runtime tracing. Strong
candidate for Phase 3 (remote streaming: turn tracing on/off on a live
device). Costs: code-size growth, patching machinery (stop-the-world or
per-cpu patching), and entry-only hooks — exit timing needs a return-address
trampoline, which is the hard part.

## `-fsanitize-coverage=trace-pc-guard` / `edge` / `trace-pc`

compiler-rt coverage callbacks: `__sanitizer_cov_trace_pc_guard` per edge
(with a per-guard toggle word in the guard variant), aimed at fuzzers
(libFuzzer). Leaner than function hooks and gives edge (basic-block
transition) granularity, but: no caller address, no exit event (timing must
be reconstructed), and guard tables need the compiler-rt runtime. Clang has
the full menu (`edge`, `trace-pc-guard`, `inline-8bit-counters`,
`trace-cmp`); GCC ≥ 12 has `trace-pc` and `trace-cmp` only. Worth an
experiment as a low-overhead backend for "which edges ran", not a
replacement for call timing.

## LLVM XRay (`-fxray-instrument`)

Clang-only, the most production-grade design in this list: sleds at function
entry/exit that are **patched at runtime** through a supported API
(`__xray_patch()`), per-function selection at compile time
(`-fxray-instruction-threshold`, attribute/whitelist), and a logging
library (FDR and basic modes) writing binary flight-recorder traces with a
structured trace format and tools (`llvm-xray`). Closest existing model for
callsight's Phase-3 streaming design — study its sled layout and FDR
buffering before designing ours. Not usable as the default: Clang-only.

## gcov / `-fprofile-arcs -ftest-coverage`

Edge *counters* for coverage, not timing. Answers "did this line run, how
often", not "how long did it take". One-day complement for the UI's
coverage view; out of scope for tracing.

## GCC plugin API

Loadable modules running custom GIMPLE/RTL passes: arbitrary injection
(e.g. hook only calls that cross a module boundary, capture arguments).
Maximum control, but plugins are locked to the exact GCC version, C++-API
fragile, Clang has no equivalent, and distribution is a nightmare. Non-goal.

## Not compile-time (context only)

- `perf` sampling — no build integration at all; finds hot functions, not
  call sequences. Complementary, always available.
- eBPF/uprobes, Intel PT — runtime/dynamic tracing with no rebuild; heavy
  machinery, root requirements. Out of scope.
- uftrace — userspace tracer built on `-pg`/`-finstrument-functions` plus
  its own libmcount runtime, with a dynamic mode on
  `-fpatchable-function-entry`. The closest relative project; callsight
  differs in zero-runtime-dependency adoption and the config-file selection
  workflow.

## Conclusions for callsight

1. **Default backend:** `-finstrument-functions` + exclude lists (GCC and
   Clang). Unchanged.
2. **Clang enhancement:** offer `-finstrument-functions-after-inlining` as
   an opt-in flag mode.
3. **Phase 3 (streaming/remote):** evaluate `-fpatchable-function-entry`
   for runtime on/off on embedded targets, using XRay's patching and FDR
   logging as the design reference.
4. **Watch list:** `-fsanitize-coverage` as a future low-overhead edge
   backend; gcov only if a coverage view is added.
