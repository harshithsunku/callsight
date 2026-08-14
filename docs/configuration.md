# Configuration — trace.config

One directive per line. `#` starts a comment, blank lines are ignored.

```
include <pattern>     only instrument matching sources
                      (if no include lines: everything is included)
exclude <pattern>     never instrument matching sources (or headers —
                      the compiler matches the file a function is
                      DEFINED in, so header paths work too)
exclude-func <name>   never instrument functions whose name contains
                      <name> (compiler substring match)
include-func <name> [depth]
                      instrument <name> and its whole call subtree —
                      resolved statically from the sources (0 = just
                      <name>, 1 = direct callees, default: full depth)
```

## Selection levels

callsight selects at three levels, from coarsest to finest:

1. **File/folder** — `include` / `exclude` patterns (below).
2. **Function + call subtree ("task level")** — `include-func` names an
   entry point (a task, a request handler, a workload); callsight parses
   the sources, builds a static call graph, and instruments exactly the
   reachable subtree. Everything else defined in those files — including
   helpers defined in headers — is excluded at compile time. Explore
   before you commit:
   ```sh
   callsight select src/ --function workload_sort          # full subtree
   callsight select src/ --function workload_sort --depth 1
   callsight select src/ --list                            # all functions
   ```
   Static resolution does not follow function pointers, macro-generated
   functions, or C++ dynamic dispatch — those callees simply don't get
   hooks.
3. **Thread** — runtime filter, no rebuild:
   `TRACE_THREADS="sort-*,worker-1"`. Matched against the thread name
   (`pthread_setname_np`) once per thread; threads that set their name
   after starting are picked up by periodic re-checks. With late-named
   threads, expect a handful of `unmatched_exits` at activation — events
   from before the thread matched are absent by design.

A pattern matches a source path when it matches the full path or any
trailing part of it — so `src/utils/rng.c` also matches
`../project/src/utils/rng.c`:

| pattern kind | examples |
|---|---|
| glob | `src/net/**`, `*test*.c`, `rng.c` |
| exact path | `src/utils/rng.c` |
| directory prefix | `src/sort`, `src/sort/` |

Excluded code emits **no hook calls at all** — compile-time exclusion is
free at runtime. Prefer it over any runtime filtering.

Preview what a config selects without building:

```sh
callsight scan src/ --config trace.config
```

## Selection strategy at scale

Event volume is the main cost — a call-heavy program easily generates
millions of events per second. The levers, cheapest first:

1. **Compile-time excludes.** Run wide once, sort the analyzer output by
   `calls`, exclude the chatty leaf helpers, rebuild. Typically cuts volume
   10–100× while keeping the structural picture.
2. **`include` directives.** Instrument only the subsystem under
   investigation: `include src/network/`.
3. **Runtime gating.** `TRACE_ENABLE` / `TRACE_MAX` control *when* and *how
   much* you pay; the cap bounds disk/memory use.
4. **Source opt-out** (requires a source edit):
   `__attribute__((no_instrument_function))`.

## Runtime knobs

| variable | default | meaning |
|---|---|---|
| `TRACE_ENABLE` | off | `1` enables collection; hooks are inert otherwise |
| `TRACE_DIR` | `./traces` | output directory (file mode) |
| `TRACE_MAX` | `0` (unlimited) | global event cap — always set one for long runs |
| `TRACE_SHM` | unset | streaming mode: POSIX shm ring name, e.g. `/tracekit0` |
| `TRACE_SHM_SIZE` | 16 MiB | ring capacity in bytes |
| `TRACE_THREADS` | unset (all) | comma-separated globs on thread names, e.g. `sort-*` |

## Example

```
# the demo selection used by tests/matrixlab
exclude src/utils/rng.c        # RNG helpers dominate call volume: pure noise
exclude src/signal/fft.h       # header-inline helpers defined in fft.h
exclude-func crc32_update      # one hot function, by name
```
