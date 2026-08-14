# Web UI

```sh
uv tool install 'callsight[ui]'
callsight ui                 # http://127.0.0.1:8321
```

A local web app that walks the whole tracing workflow against any project
on the machine — no root, no system cmake required (fetched ephemerally
via `uvx` when needed).

## The four panels

1. **Project** — browse the filesystem (directories badged `make`/`cmake`),
   open a project; shows source count, detected build system, and
   instrumented binaries.
2. **trace.config** — editor with save, plus a selection preview
   (`N sources: X instrumented, Y excluded`).
3. **Build & run** — one-click instrumented build (`make instrument` or
   the CMake flow) with captured logs, then run the binary with
   `TRACE_ENABLE=1` / `TRACE_MAX` / a timeout.
4. **Report** — the analyzer result as a sortable table (calls, inclusive,
   self, max), with the summary line flagging `unmatched_exits=0` green.

## Notes

- Binds to `127.0.0.1` by default; `--host 0.0.0.0` exposes it on the
  network — only do that on trusted networks: the UI runs builds and
  binaries on the host.
- Everything the UI does goes through the same code as the CLI — no
  behavior differences between driving it from the browser or the shell.
- UI dependencies (FastAPI/uvicorn) live in the `ui` extra; the core
  package stays stdlib-only.
