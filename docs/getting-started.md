# Getting started

## Install

```sh
uv tool install callscope          # CLI
uv tool install 'callscope[ui]'    # + web UI
uv tool install 'callscope[stream]'# + streaming server
```

Requires [uv](https://docs.astral.sh/uv/). The core is Python stdlib-only;
the injected runtime is dependency-free C.

## Adopt a project

```sh
cd /path/to/your/project
callscope init .            # add --stream for the remote-streaming client
```

`init` creates a `callscope/` directory with the runtime (`trace.c`,
`trace.h`, `trace_shm.h`), a starter `trace.config`, and the build
integration for your build system — then prints the exact lines to paste:

=== "Make"

    ```make
    CALLSCOPE_DIR ?= callscope
    include $(CALLSCOPE_DIR)/Makefile.callscope

    instrument: CFLAGS = $(CFLAGS_INSTRUMENT)
    instrument: $(BINDIR)/$(TARGET).instr
    $(BINDIR)/$(TARGET).instr: $(OBJS) $(TRACE_OBJ) | $(BINDIR)
        $(CC) $(CFLAGS_INSTRUMENT) -no-pie -o $@ $(OBJS) $(TRACE_OBJ) $(LDFLAGS)
    ```

=== "CMake"

    ```cmake
    list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/callscope")
    include(CallScope)
    callscope_instrument(<your-target>)
    ```

    then configure an instrumented build:

    ```sh
    cmake -DCALLSCOPE_INSTRUMENT=ON -B build-instr && cmake --build build-instr
    ```

## Collect and analyze

```sh
make instrument                                  # or the CMake build above
TRACE_ENABLE=1 TRACE_MAX=1000000 ./yourapp       # inert without TRACE_ENABLE=1
callscope analyze traces/ --exe ./yourapp --top 20
```

A clean run reports `unmatched_exits=0`. Sort by **self time** for hot leaf
functions, by **inclusive time** for "which high-level operation is slow",
by **calls** to find exclusion candidates.

## Next steps

- Tune coverage: [configuration](configuration.md)
- Drive it from a browser: [web UI](web-ui.md)
- Trace a device remotely: [remote streaming](streaming.md)
