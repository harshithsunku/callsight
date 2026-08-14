---
hide:
  - navigation
  - toc
---

<div class="hero" markdown>
  <div class="hero-title" markdown># callsight</div>
  <p class="hero-tag">Compile-time function tracing for C &amp; C++.<br>
  Zero source edits. One config file. From million-line codebases to embedded devices.</p>
  <div class="hero-actions" markdown>
  [Getting started](getting-started.md){ .md-button .md-button--primary }
  [View on GitHub](https://github.com/harshithsunku/callsight){ .md-button }
  </div>
  <div class="hero-install" markdown>
  `uv tool install callsight`
  </div>
</div>

<div class="feature-grid" markdown>

<div class="feature" markdown>
### Zero-edit adoption
`callsight init` drops the runtime into any GCC/Clang project and prints the
exact Make/CMake wiring. Not one line of your source changes.
</div>

<div class="feature" markdown>
### One config file
`include` / `exclude` / `exclude-func` patterns in `trace.config` become
compiler-level selection — excluded code emits **no hook at all**.
</div>

<div class="feature" markdown>
### Lean by design
Per-thread lock-free buffers, ~30–60 ns per event, no malloc and no I/O in
the hot path. Inert unless `TRACE_ENABLE=1`.
</div>

<div class="feature" markdown>
### Real analysis
Entry/exit events resolve through `addr2line` into calls, inclusive, self
and max time per function — `static` functions included.
</div>

<div class="feature" markdown>
### Web UI
`callsight ui`: browse a project, edit its config, build, run, and read a
sortable hotspot report — no root, one uv command.
</div>

<div class="feature" markdown>
### Device → server streaming
On constrained targets, events flow through a shared-memory ring to a tiny
on-device client and stream ZSTD-compressed over raw TCP. Nothing
accumulates on the device.
</div>

</div>

## Architecture

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

## Three commands in

```sh
uv tool install callsight        # 1 · install
callsight init /your/project     # 2 · adopt (copies runtime, prints wiring)
# build the instrumented profile, then:
TRACE_ENABLE=1 ./yourapp && callsight analyze traces/ --top 20
```

## Built for constrained targets

```sh
# powerful host                        # device
callsight serve --port 9001            ./trace_stream /tracekit0 10.0.0.5 9001 &
                                       TRACE_ENABLE=1 TRACE_SHM=/tracekit0 ./app
```

The traced process never touches disk or the network. If the ring outruns
the network, events are dropped and counted — the workload never stalls.

[Read the streaming guide](streaming.md){ .md-button }
[Compiler-mechanism survey](instrumentation-options.md){ .md-button }
[Status &amp; roadmap](status.md){ .md-button }
