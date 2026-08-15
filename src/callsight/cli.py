#!/usr/bin/env python3
"""
callsight — compile-time function tracing for C/C++ projects.

Subcommands:
  init     adopt callsight into a project (copies runtime + build wiring)
  scan     show which sources a trace.config would instrument
  flags    print compiler flags (used by Make/CMake integrations)
  analyze  offline hotspot report from a traces/ directory
  provision  download the bundled static ctags into $CALLSIGHT_HOME/bin
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

try:
    from callsight import analyze, flags
except ImportError:  # direct execution: python3 src/callsight/cli.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from callsight import analyze, flags

PACKAGE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = PACKAGE_DIR / "runtime"
SHARE_DIR = PACKAGE_DIR / "share"
CMAKE_DIR = PACKAGE_DIR / "cmake"
STREAM_DIR = PACKAGE_DIR / "stream"

CONFIG_TEMPLATE = """\
# trace.config — selective instrumentation configuration.
#
# One directive per line. '#' starts a comment, blank lines are ignored.
#
#   include <pattern>     only instrument matching sources
#                         (if no include lines: everything is included)
#   exclude <pattern>     never instrument matching sources (or headers —
#                         the compiler matches the file a function is
#                         DEFINED in, so header paths work too)
#   exclude-func <name>   never instrument functions whose name contains
#                         <name> (compiler substring match)
#   include-func <name> [depth]
#                         instrument <name> and its whole call subtree
#                         (statically resolved); optional depth limits
#                         expansion (0 = just <name>, 1 = direct callees)
#
# A pattern matches a source path when it matches the full path or any
# trailing part of it: glob ('src/net/**', '*test*.c'), exact path
# ('src/utils/rng.c'), or directory prefix ('src/sort', 'src/sort/').
#
# Excluded code emits NO hook calls at all — compile-time exclusion is free
# at runtime. Strategy: run wide once, look at "calls" in the analyzer, then
# exclude the chatty leaf helpers and re-run.
#
# Examples:
#   include src/network/          # instrument only one subsystem
#   exclude src/utils/rng.c       # drop a noisy file
#   exclude-func crc32_update     # drop one hot function by name
#   include-func handle_request   # only handle_request + everything it calls
#   include-func handle_request 2 # same, but at most 2 call levels deep
#
# Runtime thread filter (no rebuild needed):
#   TRACE_THREADS="worker-*" TRACE_ENABLE=1 ./yourapp
#
# Explore what a function's subtree contains:
#   callsight select src/ --function handle_request
"""

MAKE_WIRING = """\
Add to your Makefile (after SRCS/OBJS/CFLAGS_SYMBOLS are defined):

    CALLSIGHT_DIR ?= callsight
    include $(CALLSIGHT_DIR)/Makefile.callsight

    instrument: CFLAGS = $(CFLAGS_INSTRUMENT)
    instrument: $(BINDIR)/$(TARGET).instr
    $(BINDIR)/$(TARGET).instr: $(OBJS) $(TRACE_OBJ) | $(BINDIR)
    \t$(CC) $(CFLAGS_INSTRUMENT) -no-pie -o $@ $(OBJS) $(TRACE_OBJ) $(LDFLAGS)

The fragment expects SRCS, BUILDDIR, BINDIR, TARGET, CC, CFLAGS_SYMBOLS and
LDFLAGS from your Makefile; see callsight/Makefile.callsight for details."""

CMAKE_WIRING = """\
Add to your CMakeLists.txt after the target is defined:

    list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/callsight")
    include(CallSight)
    callsight_instrument(<your-target>)

Then configure an instrumented build with:

    cmake -DCALLSIGHT_INSTRUMENT=ON -B build-instr && cmake --build build-instr"""


def cmd_init(args):
    project = Path(args.project).resolve()
    if not project.is_dir():
        sys.exit(f"{project}: not a directory")

    build = args.build
    if build is None:
        if (project / "CMakeLists.txt").exists():
            build = "cmake"
        else:
            build = "make"

    dest = project / "callsight"
    dest.mkdir(exist_ok=True)
    for f in ("trace.c", "trace.h", "trace_shm.h"):
        shutil.copy2(RUNTIME_DIR / f, dest / f)
    if args.stream:
        for f in ("trace_stream.c", "zstd.c", "zstd.h", "zstd_errors.h",
                  "zstd.LICENSE"):
            shutil.copy2(STREAM_DIR / f, dest / f)
    if build == "make":
        shutil.copy2(SHARE_DIR / "Makefile.callsight", dest / "Makefile.callsight")
    else:
        shutil.copy2(CMAKE_DIR / "CallSight.cmake", dest / "CallSight.cmake")

    config = project / "trace.config"
    if config.exists():
        print(f"kept existing {config}")
    else:
        config.write_text(CONFIG_TEMPLATE)
        print(f"wrote {config}")

    n_sources = len(flags.scan_sources(project))
    print(f"copied runtime + {build} integration into {dest}/ "
          f"({n_sources} sources found under {project})")
    print()
    print(CMAKE_WIRING if build == "cmake" else MAKE_WIRING)
    if args.stream:
        print()
        print("Streaming client (build on the device):")
        print("    cc -O2 -o callsight/trace_stream callsight/trace_stream.c "
              "callsight/zstd.c")
    print()
    print("Then: build, run with TRACE_ENABLE=1, and 'callsight analyze traces/'.")


def cmd_scan(args):
    sources = flags.scan_sources(args.directory)
    if not sources:
        sys.exit(f"no C/C++ sources under {args.directory}")
    includes, excludes, funcs, include_funcs = flags.parse_config(args.config)
    if include_funcs:
        selected, dropped, auto_funcs, reachable, warnings = \
            flags.function_selection(include_funcs, sources, includes,
                                     excludes)
        print(f"{len(sources)} sources: {len(selected)} instrumented, "
              f"{len(dropped)} excluded; subtree: {len(reachable)} "
              f"functions, {len(auto_funcs)} auto-excluded "
              f"(config: {args.config})")
        for w in warnings:
            print(f"  warning: {w}")
    else:
        selected, dropped = flags.select(sources, includes, excludes)
        print(f"{len(sources)} sources: {len(selected)} instrumented, "
              f"{len(dropped)} excluded (config: {args.config})")
    for s in dropped:
        print(f"  excluded: {s}")


def cmd_select(args):
    """Explore functions/call subtrees and generate trace.config lines."""
    from callsight import callgraph
    sources = flags.scan_sources(args.directory)
    if not sources:
        sys.exit(f"no C/C++ sources under {args.directory}")
    graph = callgraph.build_graph(sources)

    if args.list:
        for name in sorted(graph):
            files = ", ".join(sorted(set(graph[name]["files"])))
            print(f"{name}  ({files})")
        return

    if not args.function:
        sys.exit("select: give --function NAME (or --list)")

    lines = []
    for seed in args.function:
        if seed not in graph:
            sys.exit(f"'{seed}' not defined in the scanned sources "
                     f"(try: callsight select {args.directory} --list)")
        sub = callgraph.expand(graph, [seed], args.depth)
        files = sorted({f for fn in sub for f in graph[fn]["files"]})
        dstr = "full depth" if args.depth is None else f"depth={args.depth}"
        print(f"{seed}: {len(sub)} functions across {len(files)} files "
              f"({dstr})")
        for fn in sorted(sub):
            print(f"    {fn}")
        lines.append(f"include-func {seed}"
                     + (f" {args.depth}" if args.depth is not None else ""))

    print()
    print("# add to trace.config:")
    for line in lines:
        print(line)
    if args.threads:
        print()
        print("# and to trace only matching threads at runtime:")
        print(f'TRACE_THREADS="{args.threads}" TRACE_ENABLE=1 ./yourapp')


def cmd_provision(args):
    """Show where ctags comes from, or download the bundled static copy."""
    from callsight import provision
    which = shutil.which("ctags")
    if which and not args.force:
        print(f"ctags found on PATH: {which}")
        return
    bundled = provision.bundled_ctags()
    if not args.force and os.path.isfile(bundled) \
            and os.access(bundled, os.X_OK):
        print(f"ctags already provisioned: {bundled}")
        _print_ctags_version(bundled)
        return
    if which:
        print(f"ctags on PATH ({which}); --force: installing bundled copy")
    try:
        path = provision.download_ctags()
    except RuntimeError as e:
        sys.exit(f"provision failed: {e}\n"
                 f"(the UI falls back to the built-in regex parser — "
                 f"everything still works without ctags)")
    print(f"installed {path}")
    _print_ctags_version(path)


def _print_ctags_version(path):
    import subprocess
    try:
        proc = subprocess.run([path, "--version"], capture_output=True,
                              text=True, timeout=30)
        first = (proc.stdout or proc.stderr).splitlines()[0]
        print(first)
    except (OSError, subprocess.TimeoutExpired, IndexError):
        pass


def cmd_ui(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("the web UI needs the optional dependencies — "
                 "install with: uv tool install 'callsight[ui]'")
    from callsight.ui.app import app
    uvicorn.run(app, host=args.host, port=args.port)


def cmd_serve(args):
    try:
        import zstandard  # noqa: F401
    except ImportError:
        sys.exit("the streaming server needs the optional dependencies — "
                 "install with: uv tool install 'callsight[stream]'")
    from callsight.serve import serve
    serve(args.host, args.port, args.out)


def package_version():
    """Installed dist version; falls back to the package constant."""
    try:
        from importlib.metadata import version
        return version("callsight")
    except Exception:
        from callsight import __version__
        return __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # flags/analyze own their full argparse interfaces (used verbatim by the
    # Make/CMake integrations); forward everything after the subcommand.
    if argv and argv[0] == "flags":
        flags.main(argv[1:] or ["--help"])
        return
    if argv and argv[0] == "analyze":
        analyze.main(argv[1:])
        return

    ap = argparse.ArgumentParser(
        prog="callsight", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {package_version()}")
    sub = ap.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="adopt callsight into a project")
    p_init.add_argument("project", help="project root directory")
    p_init.add_argument("--build", choices=("make", "cmake"), default=None,
                        help="build system (default: auto-detect)")
    p_init.add_argument("--stream", action="store_true",
                        help="also copy the remote-streaming client "
                             "(trace_stream.c + vendored single-file zstd)")
    p_init.set_defaults(func=cmd_init)

    p_scan = sub.add_parser("scan", help="show instrumentation selection")
    p_scan.add_argument("directory", help="source tree to scan")
    p_scan.add_argument("--config", default="trace.config")
    p_scan.set_defaults(func=cmd_scan)

    p_sel = sub.add_parser("select", help="explore functions/subtrees and "
                                          "generate trace.config lines")
    p_sel.add_argument("directory", help="source tree to scan")
    p_sel.add_argument("--function", "-f", action="append",
                       help="seed function (repeatable)")
    p_sel.add_argument("--depth", "-d", type=int, default=None,
                       help="limit call-subtree expansion depth "
                            "(default: full subtree)")
    p_sel.add_argument("--threads", "-t", default=None,
                       help="also print a TRACE_THREADS runtime hint")
    p_sel.add_argument("--list", "-l", action="store_true",
                       help="list all defined functions")
    p_sel.set_defaults(func=cmd_select)

    p_ui = sub.add_parser("ui", help="start the web UI (needs callsight[ui])")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8321)
    p_ui.set_defaults(func=cmd_ui)

    p_prov = sub.add_parser("provision", help="download the bundled static "
                                              "ctags used by the UI config "
                                              "builder")
    p_prov.add_argument("--force", action="store_true",
                        help="install the bundled copy even when a ctags "
                             "is already available")
    p_prov.set_defaults(func=cmd_provision)

    p_serve = sub.add_parser("serve", help="TCP server for remote trace "
                                           "streams (needs callsight[stream])")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=9001)
    p_serve.add_argument("--out", default="traces",
                         help="output directory for trace.stream.*.bin")
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("flags", help="print compiler flags "
                                 "(build-system integration)")
    sub.add_parser("analyze", help="hotspot report from traces/")

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
