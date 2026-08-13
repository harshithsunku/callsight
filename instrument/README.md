# instrument — compile-time function tracing toolkit

Drop-in infrastructure that adds entry/exit hooks to **every function at
compile time** with **zero edits to the target project's sources**, records
timing/call data with minimal runtime cost, and resolves it offline into
per-function hotspot reports. Selection (which files, folders, or functions
get hooks) is controlled from **one file: `trace.config`**.

Works with GCC and Clang on C and C++. Components:

| file | role |
|---|---|
| `trace.config` | **the only file you edit** — include/exclude files, folders, functions |
| `gen_flags.py` | turns `trace.config` + your source list into compiler flags |
| `trace.c` / `trace.h` | hook runtime, compiled into your binary (self-contained, no deps beyond pthreads) |
| `trace_analyze.py` | offline analyzer: resolves symbols with `addr2line`, prints hotspot tables |

## How it works

1. `gen_flags.py` emits `CFLAGS_INSTRUMENT` = your normal flags plus
   `-finstrument-functions` plus exclude lists derived from `trace.config`.
2. The compiler then emits calls to `__cyg_profile_func_enter/exit` at the
   entry/exit of every selected function — including `static` ones.
   Excluded code emits **no hook calls at all** (compile-time exclusion is
   free at runtime).
3. At runtime the hooks (in `trace.c`, itself compiled without the flag so
   they can't recurse) append 32-byte events to a per-thread buffer —
   no locks, no malloc, no I/O in the hot path — and flush to
   `trace.<pid>.<tid>.bin` when the buffer fills or the thread/process exits.
4. `trace_analyze.py` matches enter/exit events per thread, resolves
   addresses with `addr2line` (handles `static` functions, which `dladdr`
   can't), and reports calls / inclusive / self / max time per function.

## Adopting it in your own project (3 steps)

1. Copy this `instrument/` directory anywhere (repo root is conventional).
2. Edit `trace.config` — see the pattern syntax at the top of that file.
3. Add to your Makefile (requires GNU make, python3):

```make
INSTR_DIR ?= instrument
# Generates CFLAGS_INSTRUMENT from trace.config + your source list.
$(eval $(shell python3 $(INSTR_DIR)/gen_flags.py \
    --config $(INSTR_DIR)/trace.config -- $(SRCS) 2>/dev/null))

TRACE_OBJ = $(BUILDDIR)/instrument_runtime/trace.o

$(TRACE_OBJ): $(INSTR_DIR)/trace.c $(INSTR_DIR)/trace.h | $(BUILDDIR)
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS_SYMBOLS) -c -o $@ $<      # runtime: NO -finstrument-functions

instrument: CFLAGS = $(CFLAGS_INSTRUMENT)
instrument: $(BINDIR)/$(TARGET).instr
$(BINDIR)/$(TARGET).instr: $(OBJS) $(TRACE_OBJ) | $(BINDIR)
	$(CC) $(CFLAGS_INSTRUMENT) -no-pie -o $@ $(OBJS) $(TRACE_OBJ) $(LDFLAGS)
```

`-no-pie` is required: it keeps runtime addresses equal to link addresses so
the analyzer can feed them to `addr2line` directly.

## Running

```sh
make instrument
TRACE_ENABLE=1 TRACE_MAX=2000000 ./bin/yourapp.instr   # collect (inert without TRACE_ENABLE=1)
python3 instrument/trace_analyze.py traces/ --exe bin/yourapp.instr --top 20
```

Runtime knobs (environment variables):

| var | default | meaning |
|---|---|---|
| `TRACE_ENABLE` | off | `1` enables collection; otherwise hooks are inert |
| `TRACE_DIR` | `./traces` | output directory |
| `TRACE_MAX` | `0` (unlimited) | global event cap — safety valve, always set one for long runs |

## Selection strategy (important at scale)

Event volume is the main cost — a call-heavy program easily generates
millions of events per second. The levers, cheapest first:

1. **Compile-time excludes** (`trace.config`): free at runtime. Run wide
   once, sort the analyzer output by `calls`, exclude the chatty leaf
   helpers (`exclude src/utils/rng.c`, `exclude-func rotl`, ...), rebuild.
   Typically cuts volume 10–100× while keeping the structural picture.
2. **`include` directives**: instrument only the subsystem under
   investigation (e.g. `include src/network/`).
3. **Runtime gating**: `TRACE_ENABLE` / `TRACE_MAX` control *when* and *how
   much* you pay; the cap bounds disk usage.
4. **Source-level opt-out** (optional, requires a source edit):
   `__attribute__((no_instrument_function))` on a function.

Analysis guide: sort by **self time** for hotspot leaf functions, by
**inclusive time** for "which high-level operation is slow", by **calls**
to find exclusion candidates.

## Known limitations

- Inlined functions emit no hooks (there's no call boundary). Build the
  instrumented profile with your normal optimization level; expect small,
  very hot helpers to disappear into their callers — that's usually what
  you want, and `-finstrument-functions` follows the actual call graph.
- Overhead is ~30–60 ns per event; on call-dense code expect a noticeable
  slowdown while tracing. This is a profiling build, not a production one.
- Events are buffered per thread; a crashed/killed process loses each
  thread's tail (≤ 8191 events). Clean exits flush everything.
- `exclude-file-list` matching by the compiler is substring-based on the
  definition file's path; `gen_flags.py` passes explicit relative paths to
  stay precise, but unusually overlapping directory names can over-match.
