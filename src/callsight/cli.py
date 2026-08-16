#!/usr/bin/env python3
"""
callsight — compile-time function tracing for C/C++ projects.

Subcommands:
  init     adopt callsight into a project (copies runtime + build wiring)
  run      run an instrumented binary with tracing on and report
  scan     show which sources a trace.config would instrument
  select   explore a function's call subtree; emit trace.config lines
  flags    print compiler flags (used by Make/CMake integrations)
  analyze  offline report from a traces/ directory: hotspot tables, JSON,
           folded stacks, a Perfetto timeline or hot call sites
  diff     compare two JSON reports; fail a build on a regression
  doctor   check the toolchain and environment
  ui       local web UI (needs callsight[ui])
  serve    TCP server for remote trace streams (needs callsight[stream])
  provision  download the bundled static ctags into $CALLSIGHT_HOME/bin
"""

import argparse
import os
import shutil
import sys
from contextlib import redirect_stdout as _redirect_stdout
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
    \t$(CC) $(CFLAGS_INSTRUMENT) -o $@ $(OBJS) $(TRACE_OBJ) $(LDFLAGS)

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


def cmd_run(args):
    """Run an instrumented binary with tracing on, then report on it.

    The four-step loop (export, run, find the binary, analyze) is the same
    every time and easy to get subtly wrong — a stale traces/ directory
    silently mixes two runs into one report."""
    import subprocess

    command = list(args.command)
    # REMAINDER hands back the '--' separator itself; it is punctuation for
    # argparse, not the program to run.
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        sys.exit("run: give the command to run, e.g. "
                 "callsight run -- ./bin/app.instr --flag")
    args.command = command

    tracedir = Path(args.dir)
    tracedir.mkdir(parents=True, exist_ok=True)
    stale = sorted(tracedir.glob("trace.*.bin"))
    if stale and not args.keep:
        for f in stale:
            f.unlink()
        print(f"removed {len(stale)} trace file(s) from a previous run in "
              f"{tracedir}/")
    elif stale:
        print(f"keeping {len(stale)} existing trace file(s) in {tracedir}/ "
              f"— the report will cover both runs")

    env = dict(os.environ)
    env["TRACE_ENABLE"] = "1"
    env["TRACE_DIR"] = str(tracedir.resolve())
    env["TRACE_MODE"] = args.mode
    if args.max_mb is not None:
        env["TRACE_MAX_MB"] = str(args.max_mb)
    if args.max_events is not None:
        env["TRACE_MAX"] = str(args.max_events)
    if args.full:
        env["TRACE_FULL"] = args.full
    if args.threads:
        env["TRACE_THREADS"] = args.threads
    if args.clock:
        env["TRACE_CLOCK"] = args.clock

    try:
        proc = subprocess.Popen(args.command, env=env)
    except FileNotFoundError:
        sys.exit(f"run: {args.command[0]}: not found")
    except PermissionError:
        sys.exit(f"run: {args.command[0]}: not executable")

    try:
        proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        # SIGTERM first: a program that exits cleanly flushes each thread's
        # buffered tail, which a SIGKILL would throw away.
        print(f"\n(traced for {args.timeout}s; stopping the program)\n",
              file=sys.stderr)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()
    if proc.returncode not in (0, -15, None):
        # Not fatal: a workload killed by a timeout still produced a trace,
        # and that is often exactly the run worth looking at.
        print(f"\n(the traced program exited with status {proc.returncode}; "
              f"reporting on what it recorded)\n", file=sys.stderr)

    exe = args.exe or args.command[0]
    try:
        if args.out:
            # The traced program writes to the same stdout we do, so a
            # machine-readable report has to go somewhere of its own.
            with open(args.out, "w") as fh:
                with _redirect_stdout(fh):
                    analyze.report(str(tracedir), exe, args.top, args.format,
                                   args.addr2line, args.subtract_overhead)
            print(f"\nwrote {args.format} report to {args.out}",
                  file=sys.stderr)
        else:
            print()
            analyze.report(str(tracedir), exe, args.top, args.format,
                           args.addr2line, args.subtract_overhead)
    except RuntimeError as e:
        sys.exit(str(e))


def cmd_diff(args):
    """Compare two JSON reports so a build can fail on a regression."""
    try:
        rows, worst = analyze.diff(args.base, args.new, args.key,
                                   args.threshold)
    except OSError as e:
        sys.exit(f"diff: {e}")
    except (ValueError, KeyError) as e:
        sys.exit(f"diff: {args.base}/{args.new} are not callsight JSON "
                 f"reports ({e})")
    analyze.print_diff(rows, args.key)
    if args.fail_over is not None and worst > args.fail_over:
        sys.exit(f"\nregression: {worst:.1f}% exceeds the "
                 f"{args.fail_over:.1f}% budget")


def _doctor_check(label, ok, detail, advisory=False):
    """advisory=True reports a fact without calling it a failure — not
    every observation is a problem, and marking one FAIL and then declaring
    'no problems found' is worse than saying nothing."""
    mark = "ok  " if ok else ("note" if advisory else "FAIL")
    print(f"[{mark}] {label}: {detail}")
    return ok or advisory


def cmd_doctor(args):
    """Check everything an instrumented build and analysis depends on."""
    import shutil as _shutil
    import subprocess

    project = Path(args.project).resolve()
    problems = 0

    print(f"callsight {package_version()} — checking {project}")
    print()

    cc = os.environ.get("CC") or "cc"
    compiler = flags.detect_compiler(cc)
    if compiler == "gcc":
        detail = f"{cc} is GCC — selective instrumentation available"
        ok = True
    elif compiler == "clang":
        detail = (f"{cc} is Clang — the exclude-list flags are GCC-only "
                  f"(LLVM #15627), so only an instrument-everything config "
                  f"will build; set CC=gcc for selective coverage")
        ok = False
    else:
        detail = f"could not identify {cc}; callsight will assume GCC"
        ok = True
    problems += not _doctor_check("compiler", ok, detail)

    a2l = analyze.addr2line_cmd()
    found = _shutil.which(a2l)
    problems += not _doctor_check(
        "addr2line", bool(found),
        f"{found}" if found else
        f"{a2l} not on PATH — install binutils, or set "
        f"CALLSIGHT_ADDR2LINE to your cross-toolchain's copy")

    config = project / args.config
    if config.exists():
        try:
            includes, excludes, funcs, include_funcs = \
                flags.parse_config(str(config))
            sources = flags.scan_sources(project)
            if include_funcs:
                selected, dropped = flags.function_selection(
                    include_funcs, sources, includes, excludes)[:2]
            else:
                selected, dropped = flags.select(sources, includes, excludes)
            _doctor_check("trace.config", True,
                          f"{len(sources)} sources, {len(selected)} "
                          f"instrumented, {len(dropped)} excluded")
            if not selected:
                problems += 1
                print("       nothing would be instrumented — check the "
                      "include patterns")
        except Exception as e:  # a bad config should not crash the check
            problems += not _doctor_check("trace.config", False, str(e))
    else:
        _doctor_check("trace.config", True,
                      f"none at {config} (callsight init writes one)")

    tracedir = project / args.dir
    try:
        tracedir.mkdir(parents=True, exist_ok=True)
        probe = tracedir / ".callsight-write-probe"
        probe.write_bytes(b"x")
        probe.unlink()
        writable = True
        detail = f"{tracedir} is writable"
    except OSError as e:
        writable = False
        detail = f"{tracedir}: {e}"
    problems += not _doctor_check("trace directory", writable, detail)

    if writable:
        st = os.statvfs(tracedir)
        free_mb = st.f_bavail * st.f_frsize // (1024 * 1024)
        problems += not _doctor_check(
            "free space", free_mb >= 64,
            f"{free_mb} MB available (the runtime stops below "
            f"TRACE_MIN_FREE_MB, default 64)")

    shm = Path("/dev/shm")
    _doctor_check("shared memory", shm.is_dir() and os.access(shm, os.W_OK),
                  f"{shm} available (needed only for TRACE_SHM streaming)",
                  advisory=True)

    runtime = project / "callsight" / "trace.c"
    _doctor_check("runtime in project", runtime.exists(),
                  f"{runtime}" if runtime.exists() else
                  f"not adopted here yet — run: callsight init {project}",
                  advisory=True)

    try:
        out = subprocess.run([cc, "-fsyntax-only", "-finstrument-functions",
                              "-xc", "-"], input="int main(void){return 0;}",
                             capture_output=True, text=True, timeout=30)
        problems += not _doctor_check(
            "-finstrument-functions", out.returncode == 0,
            "accepted by the compiler" if out.returncode == 0
            else (out.stderr or "").strip().splitlines()[:1])
    except (OSError, subprocess.TimeoutExpired) as e:
        problems += not _doctor_check("-finstrument-functions", False, str(e))

    print()
    if problems:
        sys.exit(f"{problems} problem(s) found.")
    print("No problems found.")


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
        if _ctags_usable(which):
            print(f"ctags found on PATH: {which}")
        else:
            print(f"ctags on PATH ({which}) does not look like Universal "
                  f"Ctags (the UI needs --output-format=json); the bundled "
                  f"copy can be installed with: callsight provision --force")
        return
    bundled = provision.bundled_ctags()
    if not args.force and os.path.isfile(bundled) \
            and os.access(bundled, os.X_OK):
        if _ctags_usable(bundled):
            print(f"ctags already provisioned: {bundled}")
            _print_ctags_version(bundled)
            return
        print(f"bundled ctags ({bundled}) failed its smoke test; "
              f"re-downloading")
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


def _ctags_usable(path):
    """Smoke-run '<path> --version': a usable ctags exits 0 and mentions
    Ctags (Exuberant ctags lacks --output-format=json, which the UI's
    config builder needs)."""
    import subprocess
    try:
        proc = subprocess.run([path, "--version"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    out = (proc.stdout + proc.stderr).lower()
    return proc.returncode == 0 and "ctags" in out


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
    serve(args.host, args.port, args.out, args.max_mb, args.seg_mb)


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

    p_run = sub.add_parser("run", help="run an instrumented binary with "
                                       "tracing on and report")
    p_run.add_argument("--dir", default="traces",
                       help="trace directory (default: traces)")
    p_run.add_argument("--keep", action="store_true",
                       help="keep trace files from earlier runs instead of "
                            "clearing them first")
    p_run.add_argument("--exe", default=None,
                       help="binary to symbolize with (default: the command)")
    p_run.add_argument("--mode", choices=("events", "summary"),
                       default="events",
                       help="events records every call; summary aggregates "
                            "in-process, for runs of any length")
    p_run.add_argument("--max-mb", type=int, default=None,
                       help="on-disk budget (TRACE_MAX_MB)")
    p_run.add_argument("--max-events", type=int, default=None,
                       help="global event cap (TRACE_MAX)")
    p_run.add_argument("--full", choices=("stop", "wrap"), default=None,
                       help="what to do at the budget: keep the start (stop) "
                            "or the end (wrap)")
    p_run.add_argument("--timeout", type=float, default=None,
                       help="stop the program after this many seconds and "
                            "report on what it recorded")
    p_run.add_argument("--threads", default=None,
                       help="only trace threads matching these globs")
    p_run.add_argument("--clock", choices=("auto", "mono", "raw", "tsc"),
                       default=None, help="timestamp source")
    p_run.add_argument("--top", type=int, default=20)
    p_run.add_argument("--format", choices=("text", "json", "folded",
                                            "chrome", "callers"),
                       default="text")
    p_run.add_argument("--addr2line", default=None)
    p_run.add_argument("--subtract-overhead", action="store_true")
    p_run.add_argument("--out", default=None,
                       help="write the report to this file instead of "
                            "stdout, which the traced program shares")
    p_run.add_argument("command", nargs=argparse.REMAINDER,
                       help="the command to run (put it after --)")
    p_run.set_defaults(func=cmd_run)

    p_diff = sub.add_parser("diff", help="compare two JSON reports")
    p_diff.add_argument("base", help="baseline --format json report")
    p_diff.add_argument("new", help="new --format json report")
    p_diff.add_argument("--key", default="self_ms",
                        help="metric to compare (default: self_ms)")
    p_diff.add_argument("--threshold", type=float, default=0.0,
                        help="ignore changes smaller than this, in --key "
                             "units")
    p_diff.add_argument("--fail-over", type=float, default=None,
                        help="exit non-zero if any function regresses by "
                             "more than this percentage")
    p_diff.set_defaults(func=cmd_diff)

    p_doc = sub.add_parser("doctor", help="check the toolchain and "
                                          "environment")
    p_doc.add_argument("project", nargs="?", default=".")
    p_doc.add_argument("--config", default="trace.config")
    p_doc.add_argument("--dir", default="traces")
    p_doc.set_defaults(func=cmd_doctor)

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
    p_serve.add_argument("--max-mb", type=int, default=4096,
                         help="per-connection output budget (default 4096); "
                              "a device streaming for an hour must not fill "
                              "the analysis host either")
    p_serve.add_argument("--seg-mb", type=int, default=256,
                         help="segment size for rotation (default 256)")
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("flags", help="print compiler flags "
                                 "(build-system integration)")
    sub.add_parser("analyze", help="report from traces/: hotspot tables, "
                                   "--format json, or --format folded "
                                   "(flame graphs)")

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
