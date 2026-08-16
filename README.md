<h1 align="center">callsight</h1>

<p align="center">
  <strong>Exact per-call timing for the C/C++ code you choose — and zero cost for the code you don't.</strong>
</p>

<p align="center">
  <a href="https://github.com/harshithsunku/callsight/actions/workflows/ci.yml"><img alt="ci" src="https://github.com/harshithsunku/callsight/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://harshithsunku.github.io/callsight/"><img alt="docs" src="https://github.com/harshithsunku/callsight/actions/workflows/pages.yml/badge.svg"></a>
  <a href="https://pypi.org/project/callsight/"><img alt="PyPI" src="https://img.shields.io/pypi/v/callsight.svg?color=22d3ee"></a>
  <a href="https://pypi.org/project/callsight/"><img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-818cf8"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg"></a>
</p>

<p align="center">
  <a href="https://harshithsunku.github.io/callsight/">Documentation</a> ·
  <a href="https://harshithsunku.github.io/callsight/getting-started.html">Getting started</a> ·
  <a href="https://harshithsunku.github.io/callsight/configuration.html">Configuration</a> ·
  <a href="https://harshithsunku.github.io/callsight/capture.html">Capture limits</a> ·
  <a href="https://harshithsunku.github.io/callsight/analysis.html">Analysis</a> ·
  <a href="https://harshithsunku.github.io/callsight/architecture.html">Architecture</a> ·
  <a href="https://harshithsunku.github.io/callsight/reference.html">Reference</a>
</p>

<p align="center">
  <img alt="Flame graph of a multi-threaded C workload traced with callsight" src="https://harshithsunku.github.io/callsight/assets/flamegraph.png">
</p>

<p align="center">
  <em>1,000,000 events across 26 threads, exported with <code>callsight analyze --format folded</code>.</em>
</p>

---

Profiling a large C/C++ codebase usually means a bad trade: sample it and miss
the rare-but-slow path, or instrument everything and drown in millions of
events per second.

callsight moves that decision to **compile time**. One `trace.config` next to
your code says which files, folders, or call subtrees get entry/exit hooks.
Selected code is traced exactly — every call counted and timed. Everything else
emits **no hook at all**: no call, no flag check, no branch. Not a filter that
runs fast — an instruction that was never generated.

Adopting it changes **no line of your source**: one config file, one include in
your build, and a separate instrumented profile beside your normal build.

## Quick start

```sh
uv tool install callsight              # 1. install (Python stdlib only)

cd /path/to/your/project
callsight init .                       # 2. adopt — prints your build wiring

make instrument                        # 3. build the instrumented profile
callsight run -- ./bin/yourapp.instr   # 4. trace it and read the report
```

`callsight init` copies a dependency-free C runtime and the Make or CMake
wiring into `callsight/`, writes a starter `trace.config`, and prints the exact
snippet to paste. Your normal `make` still produces a binary with zero hooks in
it. Full walkthrough: **[Getting started](https://harshithsunku.github.io/callsight/getting-started.html)**.

## What you get

```
events=850059 threads=24 functions=84 span=6.6ms unmatched_exits=0 unclosed_enters=127

== TOP BY SELF TIME ==
     calls      incl_ms      self_ms       p50       p99       max  function (first location)
        12       13.170       13.170  983.04us    1.97ms    1.97ms  timer_sleep_us (src/utils/timer.c:38)
     16111       11.098        6.518      71ns    3.84us    1.59ms  qs_partition (src/sort/quicksort.c:23)
    358383        4.738        4.738       8ns      14ns  352.74us  qs_swap (src/sort/quicksort.c:5)
         4        2.335        2.230  180.22us    1.70ms    1.79ms  matrix_multiply_blocked (src/matrix/matrix_multiply.c:24)
       384        1.450        1.450    4.61us    5.63us    7.75us  matrix_lu_solve (src/matrix/matrix_decomp.c:48)
```

Calls, inclusive and self time per function, matched per thread, with symbols
— `static` functions included — resolved through `addr2line`.
`unmatched_exits=0` means the trace is clean.

**Every call is timed, so the percentiles are measurements.** `qs_swap` above
normally finishes in 8 ns; one call took 352 µs because the thread was
descheduled mid-call. A mean hides both numbers, and a sampling profiler
would almost certainly never catch that one call at all.

Four more output modes, for when a table isn't the right shape:

```sh
callsight analyze traces/ --exe ./app --format folded > out.folded
flamegraph.pl out.folded > out.svg          # or open out.folded in speedscope.app

callsight analyze traces/ --exe ./app --format chrome > trace.json   # ui.perfetto.dev
callsight analyze traces/ --exe ./app --format callers               # hot call sites
callsight analyze traces/ --exe ./app --format json --top 0 | jq '.rows[0]'
```

Two JSON reports can be compared, which makes callsight usable as a CI
performance gate — exact call counts make the comparison real rather than two
samples that happened to land differently:

```sh
callsight diff base.json new.json --fail-over 10    # exit 1 on a >10% regression
```

Traces are streamed rather than loaded, so a multi-million-event run costs a
few MB of analyzer memory instead of gigabytes. More on reading the numbers:
**[Analysis](https://harshithsunku.github.io/callsight/analysis.html)**.

## It cannot fill your disk

Tracing writes 32 bytes per entry and 32 per exit, and a call-heavy program
reaches millions of events per second. Unbounded, that is not a slow leak —
it is a device that stops working in the middle of your investigation. So
capture is bounded by default, and reaching a bound is **reported in the
trace** rather than left for you to infer.

```sh
TRACE_MAX_MB=256 ./app.instr                  # stop at 256 MB (default 512)
TRACE_MAX_MB=256 TRACE_FULL=wrap ./app.instr  # flight recorder: keep the LAST 256 MB
TRACE_MODE=summary ./app.instr                # aggregate in-process: hours, kilobytes
```

`wrap` is the answer to *what was this doing just before it hung?*
`summary` is the answer to *this needs to run for an hour*: it keeps counts,
inclusive/self time and a latency histogram per function inside the process,
so memory and output track the number of **functions**, not the number of
calls. On the bundled benchmark a run that writes 244 MB of events writes
**2.8 KB** as a summary — and the same 2.8 KB when the run is ten times
longer.

There is also a free-space floor (default 64 MB) and a checked `write()`, so
a full disk stops the capture and says so instead of silently truncating it.
**[Capture limits](https://harshithsunku.github.io/callsight/capture.html)**.

## Choosing what to trace

Event volume is the whole game. `trace.config` is where you win it:

```sh
include src/network/          # only this subsystem
exclude src/network/crc.c     # except the chatty helper
exclude-func log_printf       # and this one, by name

include-func handle_request   # or: one entry point + everything it calls
```

`include-func` resolves your call graph statically from the sources, so naming
one entry point instruments exactly its subtree and nothing else. Check any
selection before you build:

```sh
callsight scan . --config trace.config          # 35 sources: 34 instrumented, 1 excluded
callsight select src/ --function handle_request # 31 functions across 6 files
```

The practical loop: run wide once, sort the report by `calls`, exclude the
chatty leaf helpers, rebuild. That typically cuts event volume 10–100× while
the structural picture gets *clearer*. Every directive and pattern rule:
**[Configuration](https://harshithsunku.github.io/callsight/configuration.html)**.

## How it works

1. **`callsight flags`** turns `trace.config` plus your source list into
   `-finstrument-functions` and the matching compile-time exclude lists. Your
   build integration calls it on every build, so the selection is never stale.
2. **The compiler** emits `__cyg_profile_func_enter/exit` calls at the
   boundaries of selected functions. Excluded code emits nothing.
3. **The runtime** (`trace.c`, itself compiled without the flag) appends 32-byte
   events to a per-thread buffer — no locks, no malloc, no I/O on the hot path —
   flushing to `trace.<pid>.<tid>.<seq>.bin` when full or at exit. It stays
   inert unless `TRACE_ENABLE=1`.
4. **`callsight analyze`** streams those files, matches enter/exit per thread,
   resolves symbols, and prints, exports, or serves the result.

Timestamps come from the invariant cycle counter where the hardware has one
(`rdtsc` on x86-64, `cntvct_el0` on aarch64), which is what makes a hook
~12 ns instead of ~16. Ticks are converted offline using anchors the runtime
records at startup and at exit, so the rate is measured across the whole run
rather than guessed from a startup window.

Internals, the event record, and a survey of every GCC/Clang instrumentation
mechanism: **[Architecture](https://harshithsunku.github.io/callsight/architecture.html)**.

## Web UI

```sh
uv tool install 'callsight[ui]'
callsight ui                           # http://127.0.0.1:8321
```

[![callsight web UI](https://harshithsunku.github.io/callsight/screenshots/02-report.png)](https://harshithsunku.github.io/callsight/web-ui.html)

The whole loop in a browser: browse to a project, edit `trace.config` with a
live selection preview, build, run with tracing on, and read a sortable hotspot
table. A second tab builds the config *by clicking* — it enumerates every source
file and function (via `ctags`, auto-downloaded when the system has none, with a
regex fallback) into searchable checkbox panes. No root; binds to localhost.
**[More](https://harshithsunku.github.io/callsight/web-ui.html)**.

## Remote streaming

On a constrained device you can't accumulate trace files. Streaming mode keeps
**nothing** on the device: the runtime flushes into a POSIX shared-memory ring,
and a tiny static C client ships it ZSTD-compressed over raw TCP.

```sh
callsight serve                        # analysis host; needs callsight[stream]

callsight init --stream /path/to/project           # adds trace_stream.c + zstd.c
cc -O2 -o callsight/trace_stream callsight/trace_stream.c callsight/zstd.c
./callsight/trace_stream /callsight0 <server-ip> 9001 &
TRACE_ENABLE=1 TRACE_SHM=/callsight0 ./yourapp.instr
```

The traced process does no disk or network I/O — only a shared-memory memcpy per
batch. If the ring fills faster than the client drains it, events are **dropped
and counted**, never blocking your workload, and the server reports the count.
The server writes standard trace files, so analysis is unchanged.
**[More](https://harshithsunku.github.io/callsight/streaming.html)**.

## How it compares

| tool | granularity | selection | needs |
|---|---|---|---|
| **callsight** | every entry/exit, exact timing | **compile time, from one config file** — excluded code emits no hook at all | rebuild with GCC |
| [uftrace](https://github.com/namhyung/uftrace) | same mechanism, richer live TUI and replay | mostly *runtime* filters (`-F`/`-N`), so filtered functions still pay for the hook | rebuild (`-pg` / `-finstrument-functions`) |
| `perf record` | sampled, statistical | none needed | no rebuild; often root or `perf_event_paranoid` |
| gprof (`-pg`) | sampled + call counts | none | rebuild; single-threaded accounting |
| Clang XRay | entry/exit with runtime patching | per-function attributes and lists | rebuild, Clang only |

Reach for `perf` first when you want a cheap statistical profile of a whole
system, kernel and off-CPU time, or hardware counters — callsight does none of
those. Reach for callsight when you need **exactness** for code you chose:
every call counted, real p99 and max per function rather than estimates, and
the functions you *didn't* choose costing exactly nothing.

Overhead, measured by a benchmark that ships with the repo
([`tests/bench/run_bench.py`](tests/bench/run_bench.py)):

| | ns per hook | on disk |
|---|---|---|
| instrumented build, tracing **off** | 0.6 | — |
| `TRACE_CLOCK=tsc` (default where available) | 12.0 | 244 MB |
| `TRACE_CLOCK=mono` | 16.2 | 244 MB |
| `TRACE_MODE=summary` | 8.0 | **2.8 KB** |

In slowdown terms, on functions that do real work: 1.19× at ~208 ns/call,
1.52× at ~59 ns/call. On a function that does nothing at all it is 20×, which
is the honest worst case and the reason selection matters.

## Requirements

- **Linux.** The runtime uses `SYS_gettid`, `pthread_getname_np` and POSIX
  shared memory.
- **GCC** for selective instrumentation. Clang implements
  `-finstrument-functions` but not the exclude lists it builds on
  ([LLVM #15627](https://github.com/llvm/llvm-project/issues/15627)), so under
  Clang only an unfiltered "instrument everything" config compiles. callsight
  detects the toolchain and tells you before the build starts instead of failing
  one file at a time.
- **binutils** (`addr2line`) for symbol resolution, and **Python 3.9+** with
  [uv](https://docs.astral.sh/uv/). The core is stdlib-only; the web UI and the
  streaming server are optional extras. Cross-compiled targets need their own
  toolchain's `addr2line` — pass `--addr2line arm-none-eabi-addr2line`.
- **`-no-pie` is not required.** The runtime records the PIE load bias in every
  trace header, so a position-independent executable symbolizes correctly.
  Forcing `-no-pie` would mean profiling a binary built differently from the
  one you ship.

## CLI

| command | purpose |
|---|---|
| `callsight init <dir> [--build make\|cmake] [--stream]` | adopt into a project |
| `callsight run [--timeout N] [--mode summary] -- <cmd>` | trace a binary and report, in one step |
| `callsight scan <dir> [--config c]` | preview what a config selects |
| `callsight select <dir> --function F [--depth N]` | show a call subtree; emit config lines |
| `callsight analyze [traces/] [--exe bin] [--format text\|json\|folded\|chrome\|callers]` | report, JSON, collapsed stacks, Perfetto timeline, or hot call sites |
| `callsight diff base.json new.json [--fail-over PCT]` | compare two runs; gate a build on a regression |
| `callsight doctor [project]` | check the toolchain, config, trace dir and free space |
| `callsight flags --config c -- srcs...` | print compiler flags (build integrations use this) |
| `callsight ui [--host H] [--port P]` | web UI (needs `callsight[ui]`) |
| `callsight serve [--port P] [--out dir] [--max-mb N]` | TCP server for remote streams (needs `callsight[stream]`) |
| `callsight provision [--force]` | download the bundled static ctags |

Every flag: **[Reference](https://harshithsunku.github.io/callsight/reference.html)**.

## Known limitations

- **Inlined functions emit no hooks** — there is no call boundary to hook.
- **~12 ns per event** (see the table above). This is a profiling build, not a
  production one.
- **A crashed or killed process loses each thread's buffered tail.** Clean exits
  flush everything; `callsight run --timeout` sends `SIGTERM` for that reason.
- **The compiler's exclude matching is substring-based**, so unusually
  overlapping directory or symbol names can over-match. callsight guards its
  auto-generated function excludes against this and warns.
- **Summary mode has no call paths** — it records per-function totals, so flame
  graphs, the timeline export and hot call sites need event mode.
- **Static call-graph resolution** does not follow function pointers,
  macro-generated calls, or C++ dynamic dispatch.
- **No kernel time, no off-CPU time, no hardware counters.** That is `perf`'s
  ground, and callsight does not try to take it.

## Project

- **Docs:** <https://harshithsunku.github.io/callsight/>
- **Status & roadmap:** [STATUS.md](STATUS.md)
- **Conventions, build and smoke-test commands:** [AGENTS.md](AGENTS.md)
- **Try it without adopting anything:** `tests/matrixlab/` is a multi-threaded
  C11 workload that doubles as the end-to-end fixture — the traces and flame
  graph in these docs come from it.
- **Check the claims yourself:** `tests/bench/run_bench.py` measures the
  overhead numbers above; `tests/runtime/test_runtime.py` drives real
  instrumented binaries through budget exhaustion, rotation, `fork`, thread-id
  reuse, write failures and PIE symbolization, and asserts that reported call
  counts and durations match a known ground truth.

Issues and pull requests welcome. MIT licensed.
