# Build integrations

Both integrations do the same three things: generate
`-finstrument-functions` plus exclude lists from your `trace.config` and
source list, compile the runtime **without** the flag (so the hooks cannot
recurse), and link with `-no-pie` (keeps runtime addresses equal to link
addresses so the analyzer can feed them to `addr2line` directly).

## GNU Make

```make
CALLSIGHT_DIR ?= callsight
include $(CALLSIGHT_DIR)/Makefile.callsight
```

The fragment expects `SRCS`, `BUILDDIR`, `BINDIR`, `TARGET`, `CC`,
`CFLAGS_SYMBOLS` and `LDFLAGS` from your Makefile, and defines:

- `CFLAGS_INSTRUMENT` — your `CFLAGS_SYMBOLS` + hooks + exclude lists
- `TRACE_OBJ` — the runtime object (built without instrumentation)

You then add an `instrument` target linking `$(TRACE_OBJ)` with `-no-pie`
(the `callsight init` output prints it ready to paste).

Overridable variables: `CALLSIGHT_DIR` (where the runtime lives),
`CALLSIGHT_CONFIG` (default `./trace.config`), `CALLSIGHT` (the command
used to generate flags — point it at `python3 .../cli.py` to run from a
source checkout without installing).

## CMake

```cmake
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/callsight")
include(CallSight)
callsight_instrument(<target>)
```

Normal builds are untouched; instrumentation is opt-in per configure:

```sh
cmake -DCALLSIGHT_INSTRUMENT=ON -B build-instr
cmake --build build-instr
```

Cache variables:

| variable | default | meaning |
|---|---|---|
| `CALLSIGHT_INSTRUMENT` | `OFF` | apply hooks to `callsight_instrument()` targets |
| `CALLSIGHT_CONFIG` | `<src>/trace.config` | selection config |
| `CALLSIGHT_COMMAND` | `callsight` | flag generator; a `;`-list works, e.g. `-D"CALLSIGHT_COMMAND=python3;/path/cli.py"` |

!!! note
    `CALLSIGHT_COMMAND` is a cache variable — pass it with `-D`. A plain
    `set()` before `include(CallSight)` gets shadowed by the cache
    definition (CMP0126).

## Notes

- Inlined functions emit no hooks — there is no call boundary. Build the
  instrumented profile at your normal optimization level; the hottest tiny
  helpers fold into their callers, which is usually what you want.
- The selection is recomputed at build time, so editing `trace.config` and
  rebuilding is the whole workflow.
