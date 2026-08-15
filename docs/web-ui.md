# Web UI

```sh
uv tool install 'callsight[ui]'
callsight ui                 # http://127.0.0.1:8321
```

A local web app that walks the whole tracing workflow against any project
on the machine — no root, no system cmake required (fetched ephemerally
via `uvx` when needed). The UI is organized into two tabs: **Workflow**
and **Config Builder**.

## Workflow tab

The end-to-end pipeline in four steps:

1. **Project** — browse the filesystem (directories badged
   `make`/`cmake`), open a project; shows source count, detected build
   system, and instrumented binaries.
2. **trace.config** — editor with save, a selection preview
   (`N sources: X instrumented, Y excluded`), and a call-subtree helper:
   enter a function name (and optional depth) to resolve its subtree
   through the static call graph and append the matching
   `include-func` line.
3. **Build & run** — one-click instrumented build (`make instrument` or
   the CMake flow) with captured logs, then run the binary with
   `TRACE_ENABLE=1` / `TRACE_MAX` / a timeout.
4. **Report** — the analyzer result as a sortable table (calls,
   inclusive, self, max), with the summary line flagging
   `unmatched_exits=0` green.

## Config Builder tab

A visual editor for `trace.config` — no pattern syntax to memorize:

1. **Pick a folder and scan** — the builder enumerates the project's
   source files and the functions defined in them (via `ctags` when
   available, with a regex fallback otherwise; a `symbols:` badge next
   to the Scan button shows which backend served the scan — system
   ctags, the bundled static copy auto-downloaded on first scan, or the
   regex fallback).
2. **Searchable checkbox panes** — one for files, one for functions;
   filter either list and tick what you care about.
3. **Per-function actions** — for each selected function choose
   *include-subtree* (expands through the static call graph, like
   `include-func`) or *exclude*.
4. **Preview** — a dry run of the selection: how many sources would be
   instrumented vs. excluded with the current choices.
5. **Apply** — writes the resulting `trace.config` to the project,
   ready for the next instrumented build.

## Notes

- Binds to `127.0.0.1` by default; `--host 0.0.0.0` exposes it on the
  network — only do that on trusted networks: the UI runs builds and
  binaries on the host.
- Everything the UI does goes through the same code as the CLI — no
  behavior differences between driving it from the browser or the shell.
- UI dependencies (FastAPI/uvicorn) live in the `ui` extra; the core
  package stays stdlib-only.
