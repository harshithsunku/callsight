---
hide:
  - navigation
  - toc
---

<div class="hero" markdown>
  <p class="hero-eyebrow">Open source · MIT</p>
  <div class="hero-title" markdown># callsight</div>
  <p class="hero-tag">Compile-time function tracing for C &amp; C++ —<br>
  zero source edits, one <code>trace.config</code>.<br>
  From million-line codebases to embedded devices.</p>
  <div class="hero-install" markdown>
  `uv tool install callsight`
  </div>
  <div class="hero-actions" markdown>
  [Getting started](getting-started.md){ .md-button .md-button--primary }
  [View on GitHub](https://github.com/harshithsunku/callsight){ .md-button }
  </div>
</div>

<p class="kicker">What it does</p>

## One config file, the whole tracing pipeline.

<div class="feature-grid" markdown>

<div class="feature" markdown>
<span class="feature-icon">⚡</span>
### Compile-time instrumentation
Entry/exit hooks are injected with `-finstrument-functions` at compile
time. Not one line of your source changes — and excluded code emits
**no hook at all**.
</div>

<div class="feature" markdown>
<span class="feature-icon">🎯</span>
### One `trace.config`
`include` / `exclude` / `exclude-func` patterns in a single file become
compiler-level selection, recomputed at every build.
</div>

<div class="feature" markdown>
<span class="feature-icon">📡</span>
### Remote streaming
Events flow through a POSIX shared-memory ring to a tiny on-device
client, then ZSTD-compressed over raw TCP to `callsight serve`. Ring
full? Events are dropped and counted — the workload **never stalls**.
</div>

<div class="feature" markdown>
<span class="feature-icon">💻</span>
### Web UI
`callsight ui`: a visual config builder with call-subtree selection,
one-click instrumented builds, traced runs, and a sortable hotspot
report — no root, one uv command.
</div>

<div class="feature" markdown>
<span class="feature-icon">🌲</span>
### Call-subtree selection
`include-func handle_request 3` expands through a static call graph —
trace exactly one feature's subtree instead of the whole binary.
</div>

<div class="feature" markdown>
<span class="feature-icon">🛠</span>
### Make &amp; CMake integrations
`callsight init` drops in a Makefile fragment or CMake module and prints
the exact wiring. Normal builds stay untouched; instrumentation is
opt-in per configure.
</div>

</div>

<p class="kicker">How it works</p>

## Compile, run, analyze.

1. **Instrument at compile time** — hooks plus exclude lists are
   generated from your `trace.config` and source list; the runtime is
   compiled without the flag so hooks cannot recurse.
2. **Run** — with `TRACE_ENABLE=1`, per-thread lock-free buffers write
   events to `trace.*.bin` files, or into a shared-memory ring when
   `TRACE_SHM` is set.
3. **Stream (optional)** — the on-device `trace_stream` client drains
   the ring and ships ZSTD-compressed chunks over TCP to
   `callsight serve`, which writes standard trace files on the host.
4. **Analyze** — `callsight analyze` or the web UI resolves events
   through `addr2line` into calls, inclusive, self and max time per
   function — `static` functions included.

<div class="arch" markdown>

<svg viewBox="0 0 980 300" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="callsight architecture">
  <defs>
    <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" class="arch-arrow"/>
    </marker>
  </defs>

  <!-- compile time -->
  <text x="150" y="28" class="arch-phase">COMPILE TIME</text>
  <rect x="20" y="45" width="120" height="52" rx="8" class="arch-box"/>
  <text x="80" y="66" class="arch-label">your sources</text>
  <text x="80" y="84" class="arch-sub">(untouched)</text>

  <rect x="200" y="45" width="150" height="52" rx="8" class="arch-box accent"/>
  <text x="275" y="66" class="arch-label">-finstrument-functions</text>
  <text x="275" y="84" class="arch-sub">+ exclude lists</text>

  <rect x="410" y="45" width="120" height="52" rx="8" class="arch-box"/>
  <text x="470" y="66" class="arch-label">trace.config</text>
  <text x="470" y="84" class="arch-sub">one file selects</text>

  <line x1="140" y1="71" x2="196" y2="71" class="arch-line" marker-end="url(#arr)"/>
  <line x1="410" y1="71" x2="354" y2="71" class="arch-line" marker-end="url(#arr)"/>

  <!-- device runtime -->
  <text x="150" y="140" class="arch-phase">DEVICE · RUNTIME</text>
  <rect x="20" y="157" width="150" height="52" rx="8" class="arch-box"/>
  <text x="95" y="178" class="arch-label">__cyg_profile hooks</text>
  <text x="95" y="196" class="arch-sub">per-thread buffers</text>

  <rect x="230" y="157" width="130" height="52" rx="8" class="arch-box"/>
  <text x="295" y="178" class="arch-label">file sink</text>
  <text x="295" y="196" class="arch-sub">trace.*.bin</text>

  <rect x="420" y="157" width="140" height="52" rx="8" class="arch-box accent"/>
  <text x="490" y="178" class="arch-label">shm ring</text>
  <text x="490" y="196" class="arch-sub">drop-counted</text>

  <rect x="620" y="157" width="140" height="52" rx="8" class="arch-box accent"/>
  <text x="690" y="178" class="arch-label">trace_stream</text>
  <text x="690" y="196" class="arch-sub">zstd · raw TCP</text>

  <line x1="95" y1="97" x2="95" y2="153" class="arch-line" marker-end="url(#arr)"/>
  <line x1="170" y1="183" x2="226" y2="183" class="arch-line" marker-end="url(#arr)"/>
  <line x1="170" y1="196" x2="416" y2="196" class="arch-line" marker-end="url(#arr)"/>
  <line x1="560" y1="183" x2="616" y2="183" class="arch-line" marker-end="url(#arr)"/>

  <!-- server -->
  <text x="860" y="140" class="arch-phase">SERVER</text>
  <rect x="800" y="157" width="150" height="52" rx="8" class="arch-box"/>
  <text x="875" y="178" class="arch-label">callsight serve</text>
  <text x="875" y="196" class="arch-sub">trace.stream.*.bin</text>

  <rect x="690" y="240" width="260" height="48" rx="8" class="arch-box good"/>
  <text x="820" y="261" class="arch-label">callsight analyze · web UI</text>
  <text x="820" y="279" class="arch-sub">hotspots · self/inclusive time · threads</text>

  <line x1="760" y1="183" x2="796" y2="183" class="arch-line" marker-end="url(#arr)"/>
  <line x1="295" y1="209" x2="295" y2="264" class="arch-line" marker-end="url(#arr)"/>
  <line x1="295" y1="264" x2="686" y2="264" class="arch-line" marker-end="url(#arr)"/>
  <line x1="875" y1="209" x2="875" y2="236" class="arch-line" marker-end="url(#arr)"/>
</svg>

</div>

<p class="kicker">Quick start</p>

## Three commands in.

=== "Make"

    ```sh
    uv tool install callsight
    cd /your/project && callsight init .   # prints the Makefile wiring
    make instrument                        # clean first when switching profiles
    TRACE_ENABLE=1 ./bin/yourapp.instr     # inert without TRACE_ENABLE=1
    callsight analyze traces/ --exe bin/yourapp.instr --top 20
    ```

=== "CMake"

    ```sh
    uv tool install callsight
    cd /your/project && callsight init .
    # CMakeLists.txt: include(CallSight) + callsight_instrument(<target>)
    cmake -DCALLSIGHT_INSTRUMENT=ON -B build-instr
    cmake --build build-instr
    TRACE_ENABLE=1 ./build-instr/yourapp
    callsight analyze traces/ --exe build-instr/yourapp
    ```

=== "Remote streaming"

    ```sh
    # analysis host                        # device (after init --stream)
    uv tool install 'callsight[stream]'    cc -O2 -o callsight/trace_stream \
    callsight serve --port 9001 \              callsight/trace_stream.c callsight/zstd.c
        --out traces/                      ./callsight/trace_stream /tracekit0 <host-ip> 9001 &
                                           TRACE_ENABLE=1 TRACE_SHM=/tracekit0 ./app.instr
    ```

A clean run reports `unmatched_exits=0`. On constrained targets the
traced process never touches disk or the network — if the ring outruns
the network, events are dropped and counted, never stalled.

[Read the streaming guide](streaming.md){ .md-button }
[Compiler-mechanism survey](instrumentation-options.md){ .md-button }
[Status &amp; roadmap](status.md){ .md-button }
