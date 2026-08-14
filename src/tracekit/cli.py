#!/usr/bin/env python3
"""
tracekit — compile-time function tracing for C/C++ projects.

Subcommands:
  init     adopt tracekit into a project (copies runtime + build wiring)
  scan     show which sources a trace.config would instrument
  flags    print compiler flags (used by Make/CMake integrations)
  analyze  offline hotspot report from a traces/ directory
"""

import argparse
import shutil
import sys
from pathlib import Path

try:
    from tracekit import analyze, flags
except ImportError:  # direct execution: python3 src/tracekit/cli.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tracekit import analyze, flags

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
"""

MAKE_WIRING = """\
Add to your Makefile (after SRCS/OBJS/CFLAGS_SYMBOLS are defined):

    TRACEKIT_DIR ?= tracekit
    include $(TRACEKIT_DIR)/Makefile.tracekit

    instrument: CFLAGS = $(CFLAGS_INSTRUMENT)
    instrument: $(BINDIR)/$(TARGET).instr
    $(BINDIR)/$(TARGET).instr: $(OBJS) $(TRACE_OBJ) | $(BINDIR)
    \t$(CC) $(CFLAGS_INSTRUMENT) -no-pie -o $@ $(OBJS) $(TRACE_OBJ) $(LDFLAGS)

The fragment expects SRCS, BUILDDIR, BINDIR, TARGET, CC, CFLAGS_SYMBOLS and
LDFLAGS from your Makefile; see tracekit/Makefile.tracekit for details."""

CMAKE_WIRING = """\
Add to your CMakeLists.txt after the target is defined:

    list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/tracekit")
    include(TraceKit)
    tracekit_instrument(<your-target>)

Then configure an instrumented build with:

    cmake -DTRACEKIT_INSTRUMENT=ON -B build-instr && cmake --build build-instr"""


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

    dest = project / "tracekit"
    dest.mkdir(exist_ok=True)
    for f in ("trace.c", "trace.h", "trace_shm.h"):
        shutil.copy2(RUNTIME_DIR / f, dest / f)
    if args.stream:
        for f in ("trace_stream.c", "zstd.c", "zstd.h", "zstd_errors.h",
                  "zstd.LICENSE"):
            shutil.copy2(STREAM_DIR / f, dest / f)
    if build == "make":
        shutil.copy2(SHARE_DIR / "Makefile.tracekit", dest / "Makefile.tracekit")
    else:
        shutil.copy2(CMAKE_DIR / "TraceKit.cmake", dest / "TraceKit.cmake")

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
        print("    cc -O2 -o tracekit/trace_stream tracekit/trace_stream.c "
              "tracekit/zstd.c")
    print()
    print("Then: build, run with TRACE_ENABLE=1, and 'tracekit analyze traces/'.")


def cmd_scan(args):
    sources = flags.scan_sources(args.directory)
    if not sources:
        sys.exit(f"no C/C++ sources under {args.directory}")
    includes, excludes, funcs = flags.parse_config(args.config)
    selected, dropped = flags.select(sources, includes, excludes)
    print(f"{len(sources)} sources: {len(selected)} instrumented, "
          f"{len(dropped)} excluded (config: {args.config})")
    for s in dropped:
        print(f"  excluded: {s}")


def cmd_ui(args):
    try:
        import uvicorn
    except ImportError:
        sys.exit("the web UI needs the optional dependencies — "
                 "install with: uv tool install 'tracekit[ui]'")
    from tracekit.ui.app import app
    uvicorn.run(app, host=args.host, port=args.port)


def cmd_serve(args):
    try:
        import zstandard  # noqa: F401
    except ImportError:
        sys.exit("the streaming server needs the optional dependencies — "
                 "install with: uv tool install 'tracekit[stream]'")
    from tracekit.serve import serve
    serve(args.host, args.port, args.out)


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
        prog="tracekit", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="adopt tracekit into a project")
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

    p_ui = sub.add_parser("ui", help="start the web UI (needs tracekit[ui])")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8321)
    p_ui.set_defaults(func=cmd_ui)

    p_serve = sub.add_parser("serve", help="TCP server for remote trace "
                                           "streams (needs tracekit[stream])")
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
