#!/usr/bin/env python3
"""
flags.py — generate selective compile-time instrumentation flags.

Reads a trace.config plus the project's source file list, decides which
translation units get -finstrument-functions, and prints the resulting
compiler flags. Typical use inside a Makefile:

    $(eval $(shell callsight flags --config trace.config -- $(SRCS)))

or standalone:

    python3 flags.py --config trace.config --scan src --print

Config syntax (one directive per line, '#' comments, blank lines ignored):

    include <pattern>        only instrument matching sources
                             (default: everything is included)
    exclude <pattern>        never instrument matching sources
    exclude-func <name>      never instrument functions whose (mangled) name
                             contains <name>  (compiler substring match)
    include-func <name> [N]  instrument <name> and its whole call subtree
                             (statically resolved via callgraph.py);
                             optional N limits expansion depth
                             (0 = just <name>, 1 = direct callees, ...)
    counter <ev>[,<ev>...]   hardware events to count (max 3), e.g.
                             'instructions,cache-misses'
    counter-func <name> [N]  count <name> (and N levels of its subtree)
    counter-file <pattern>   count every instrumented function defined in
                             matching files
    counter-min auto|<ns>|0  skip counting functions shorter than this;
                             'auto' (default) derives it from the measured
                             cost of one counter read, 0 disables the guard

Pattern matching against a source path (as passed to the compiler, e.g.
'src/utils/rng.c') succeeds when the pattern matches the full path or any
component-wise suffix of it ('src/utils/rng.c' also matches
'../matrixlab/src/utils/rng.c'). For each candidate suffix:

    fnmatch(suffix, pattern)         glob, e.g. 'src/net/**' or '*test*.c'
    suffix == pattern                exact path, e.g. 'src/utils/rng.c'
    suffix.startswith(pattern+'/')   directory, e.g. 'src/sort' or 'src/sort/'

(A bare filename like 'rng.c' is just the shortest suffix.)

Notes:
  - File/folder exclusion maps to -finstrument-functions-exclude-file-list.
    GCC matches it against the file where a function is DEFINED, so header
    files (e.g. 'src/signal/fft.h') can be excluded to silence inline/static
    header helpers.
  - Function exclusion maps to -finstrument-functions-exclude-function-list
    (substring match on symbol names).
  - include-func is compiled down to those two primitives: the files
    defining the subtree get instrumented, and every other function defined
    in those files is added to the function exclude list (with a guard
    against substring collisions — the compiler's exclude match is a
    substring match, so an auto-exclude that is a prefix of a selected
    function would silently disable it).
  - Both compile-time excludes are FREE at runtime: no hook is emitted at
    all. Prefer them over any runtime filtering.
  - Both exclude flags are GCC-only. Clang implements
    -finstrument-functions but not the exclude lists (LLVM issue #15627),
    and its driver rejects unknown arguments, so a selective config cannot
    be compiled with Clang. The compiler is detected from --compiler-cmd /
    $CC and a config that needs exclusions fails here, with an explanation,
    instead of deep inside the build.
"""

import argparse
import fnmatch
import os
import subprocess
import sys
from typing import NamedTuple

try:
    from callsight import callgraph
except ImportError:  # direct execution: python3 src/callsight/flags.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from callsight import callgraph

SOURCE_SUFFIXES = (".c", ".cc", ".cpp")


# --- Hardware counter events ----------------------------------------------
#
# Name -> (perf_event_attr.type, .config). The runtime never sees these
# names: the resolved numbers travel in the counter map, so trace.c needs no
# event table of its own and a new event name here needs no runtime change.
PERF_TYPE_HARDWARE, PERF_TYPE_RAW = 0, 4

COUNTER_EVENTS = {
    "cycles":                   (PERF_TYPE_HARDWARE, 0),
    "instructions":             (PERF_TYPE_HARDWARE, 1),
    "cache-references":         (PERF_TYPE_HARDWARE, 2),
    "cache-misses":             (PERF_TYPE_HARDWARE, 3),
    "branch-instructions":      (PERF_TYPE_HARDWARE, 4),
    "branch-misses":            (PERF_TYPE_HARDWARE, 5),
    "bus-cycles":               (PERF_TYPE_HARDWARE, 6),
    "stalled-cycles-frontend":  (PERF_TYPE_HARDWARE, 7),
    "stalled-cycles-backend":   (PERF_TYPE_HARDWARE, 8),
    "ref-cycles":               (PERF_TYPE_HARDWARE, 9),
}

# A PMU has a small number of general-purpose registers. Ask for more events
# than it has and the kernel time-slices them, scaling the counts it returns
# — an estimate, which is the one thing this feature exists not to produce.
# Three fits every PMU callsight is likely to meet and leaves headroom.
COUNTER_MAX_EVENTS = 3


def parse_counter_event(name):
    """(type, config) for an event name, or raise ValueError.

    Raw codes as rNNNN pass whatever the PMU documents straight through, so
    an uncommon event does not need a table entry here."""
    if name in COUNTER_EVENTS:
        return COUNTER_EVENTS[name]
    if len(name) > 1 and name[0] == "r":
        try:
            return (PERF_TYPE_RAW, int(name[1:], 16))
        except ValueError:
            pass
    raise ValueError(
        f"unknown counter event '{name}'; known events are "
        f"{', '.join(sorted(COUNTER_EVENTS))}, or a raw PMU code as rNNNN")


class ConfigSpec(NamedTuple):
    """Everything one trace.config says.

    A named type rather than a tuple because it has grown twice now: the
    counter directives are the second addition, and unpacking by position
    breaks every call site each time.
    """
    includes: list          # 'include' patterns
    excludes: list          # 'exclude' patterns
    funcs: list             # 'exclude-func' names
    include_funcs: list     # 'include-func' (name, depth-or-None)
    counter_events: list    # 'counter' event names, in order
    counter_funcs: list     # 'counter-func' (name, depth-or-None)
    counter_files: list     # 'counter-file' patterns
    counter_min: object     # 'counter-min': "auto", or ns as an int


def _depth(path, lineno, directive, value):
    """Split '<name> [depth]', shared by include-func and counter-func."""
    name, _, depth = value.partition(" ")
    if not depth:
        return name, None
    try:
        return name, int(depth)
    except ValueError:
        sys.exit(f"{path}:{lineno}: {directive} depth must be an integer, "
                 f"got '{depth.strip()}'")


DIRECTIVES = ("include", "exclude", "exclude-func", "include-func",
              "counter", "counter-func", "counter-file", "counter-min")


def parse_config(path):
    includes, excludes, funcs, include_funcs = [], [], [], []
    counter_events, counter_funcs, counter_files = [], [], []
    counter_min = "auto"
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                sys.exit(f"{path}:{lineno}: expected '<directive> <value>'")
            directive, value = parts[0], parts[1].strip()
            if directive == "include":
                includes.append(value)
            elif directive == "exclude":
                excludes.append(value)
            elif directive == "exclude-func":
                funcs.append(value)
            elif directive == "include-func":
                include_funcs.append(_depth(path, lineno, directive, value))
            elif directive == "counter":
                for ev in value.replace(",", " ").split():
                    try:
                        parse_counter_event(ev)
                    except ValueError as e:
                        sys.exit(f"{path}:{lineno}: {e}")
                    if ev not in counter_events:
                        counter_events.append(ev)
            elif directive == "counter-func":
                counter_funcs.append(_depth(path, lineno, directive, value))
            elif directive == "counter-file":
                counter_files.append(value)
            elif directive == "counter-min":
                counter_min = _parse_counter_min(path, lineno, value)
            else:
                sys.exit(f"{path}:{lineno}: unknown directive '{directive}' "
                         f"(want: {', '.join(DIRECTIVES)})")

    if len(counter_events) > COUNTER_MAX_EVENTS:
        sys.exit(
            f"{path}: {len(counter_events)} counter events requested "
            f"({', '.join(counter_events)}), at most {COUNTER_MAX_EVENTS} "
            f"are supported.\nA PMU has a few general-purpose registers; "
            f"asking for more makes the kernel time-slice them and scale "
            f"the counts, which turns exact numbers into estimates. Record "
            f"two runs instead.")
    return ConfigSpec(includes, excludes, funcs, include_funcs,
                      counter_events, counter_funcs, counter_files,
                      counter_min)


def _parse_counter_min(path, lineno, value):
    """'auto', or a duration: bare ns, or with a ns/us/ms/s suffix."""
    text = value.strip().lower()
    if text == "auto":
        return "auto"
    for suffix, scale in (("ms", 1000000), ("us", 1000), ("ns", 1),
                          ("s", 1000000000)):
        if text.endswith(suffix):
            text, mult = text[:-len(suffix)].strip(), scale
            break
    else:
        mult = 1
    try:
        n = float(text)
    except ValueError:
        sys.exit(f"{path}:{lineno}: counter-min wants 'auto', 0, or a "
                 f"duration like '5us' — got '{value}'")
    if n < 0:
        sys.exit(f"{path}:{lineno}: counter-min cannot be negative")
    return int(n * mult)


def render_config(excluded_files=(), include_funcs=(), excluded_funcs=(),
                  counter_events=(), counter_funcs=(), counter_files=(),
                  counter_min=None):
    """Render trace.config text from web-UI config-builder selections.

    excluded_files: project-relative paths -> 'exclude' lines (the default is
        include-everything, so only exclusions need lines).
    include_funcs: iterable of (name, depth-or-None) -> 'include-func' lines.
    excluded_funcs: iterable of names -> 'exclude-func' lines.
    counter_events: event names -> one 'counter' line.
    counter_funcs: iterable of (name, depth-or-None) -> 'counter-func' lines.
    counter_files: patterns -> 'counter-file' lines.
    counter_min: None omits the line and takes the default.
    Order within each group is preserved; duplicates are dropped.

    Raises ValueError on values that would produce an unparseable config:
    control characters in a path (spaces are fine — parse_config splits on
    the first whitespace only), any whitespace in a function name
    ('include-func <name> [depth]' is whitespace-delimited), and depths
    that are not non-negative ints."""
    def dedupe(items):
        seen = set()
        return [i for i in items if not (i in seen or seen.add(i))]

    def check_path(path):
        if not isinstance(path, str) or not path \
                or any(not c.isprintable() for c in path):
            raise ValueError(f"invalid file path {path!r}: paths must be "
                             f"non-empty text without newlines, tabs or "
                             f"other control characters (spaces are fine)")

    def check_name(name):
        if not isinstance(name, str) or not name \
                or any(not c.isprintable() or c.isspace() for c in name):
            raise ValueError(f"invalid function name {name!r}: names must "
                             f"be non-empty and contain no whitespace")

    def check_depth(depth):
        if depth is not None and (not isinstance(depth, int)
                                  or isinstance(depth, bool) or depth < 0):
            raise ValueError(f"invalid include-func depth {depth!r}: "
                             f"must be a non-negative integer or None")

    lines = [
        "# trace.config — generated by the callsight web UI config builder.",
        "# Syntax: include <pattern> | exclude <pattern> |",
        "#         exclude-func <name> | include-func <name> [depth]",
        "#         counter <events> | counter-func <name> [depth] |",
        "#         counter-file <pattern> | counter-min auto|<ns>|0",
        "# Exclude always wins over include. Edit freely; see",
        "# https://github.com/harshithsunku/callsight for the full reference.",
        "",
    ]
    for path in dedupe(excluded_files):
        check_path(path)
        lines.append(f"exclude {path}")
    for name in dedupe(excluded_funcs):
        check_name(name)
        lines.append(f"exclude-func {name}")
    # Entries may arrive as lists (JSON); normalize to (name, depth) tuples.
    funcs = [tuple(f) for f in include_funcs]
    for name, depth in dedupe(funcs):
        check_name(name)
        check_depth(depth)
        lines.append(f"include-func {name}" +
                     (f" {depth}" if depth is not None else ""))

    events = dedupe(counter_events)
    cfuncs = [tuple(f) for f in counter_funcs]
    cfiles = dedupe(counter_files)
    if events or cfuncs or cfiles or counter_min is not None:
        lines.append("")
        lines.append("# Hardware counters. Only instrumented functions can "
                     "be counted, and only")
        lines.append("# functions long enough to outweigh a counter read — "
                     "see counter-min.")
    for ev in events:
        try:
            parse_counter_event(ev)
        except ValueError as e:
            raise ValueError(str(e))
    if len(events) > COUNTER_MAX_EVENTS:
        raise ValueError(
            f"{len(events)} counter events requested, at most "
            f"{COUNTER_MAX_EVENTS} are supported: more than a PMU has "
            f"registers makes the kernel scale the counts, which turns "
            f"exact numbers into estimates")
    if events:
        lines.append("counter " + ",".join(events))
    for pattern in cfiles:
        check_path(pattern)
        lines.append(f"counter-file {pattern}")
    for name, depth in dedupe(cfuncs):
        check_name(name)
        check_depth(depth)
        lines.append(f"counter-func {name}" +
                     (f" {depth}" if depth is not None else ""))
    if counter_min is not None:
        if counter_min != "auto" and (not isinstance(counter_min, int)
                                      or isinstance(counter_min, bool)
                                      or counter_min < 0):
            raise ValueError(f"invalid counter-min {counter_min!r}: want "
                             f"'auto' or a non-negative number of ns")
        lines.append(f"counter-min {counter_min}")
    return "\n".join(lines) + "\n"


def matches(path, pattern):
    """Match pattern against the path or any of its component-wise suffixes,
    so 'src/utils/rng.c' also matches '../matrixlab/src/utils/rng.c'."""
    pat = pattern.rstrip("/")
    parts = path.split("/")
    for i in range(len(parts)):
        tail = "/".join(parts[i:])
        if fnmatch.fnmatch(tail, pattern) or tail == pat or tail.startswith(pat + "/"):
            return True
    return False


def select(sources, includes, excludes):
    selected, dropped = [], []
    for src in sources:
        ok = True if not includes else any(matches(src, p) for p in includes)
        if ok and any(matches(src, p) for p in excludes):
            ok = False
        (selected if ok else dropped).append(src)
    return selected, dropped


# Directories never worth scanning for project sources.
SKIP_DIRS = {".git", ".svn", ".hg", "node_modules", ".venv", "venv",
             "__pycache__"}


def scan_sources(directory):
    """Recursively collect C/C++ sources under directory.

    Skips VCS/dependency dirs and build output dirs (build, build-*)."""
    sources = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS
                   and d != "build" and not d.startswith("build-")]
        for f in files:
            if f.endswith(SOURCE_SUFFIXES):
                sources.append(os.path.join(root, f))
    return sources


_compiler_cache = {}


def detect_compiler(cmd):
    """Return 'clang', 'gcc' or None for the compiler invoked as `cmd`.

    None means "could not tell" (command missing, unreadable banner) — the
    caller treats that as GCC, because guessing wrong must never break a
    build that would otherwise work."""
    if not cmd:
        return None
    if cmd in _compiler_cache:
        return _compiler_cache[cmd]
    kind = None
    try:
        proc = subprocess.run(cmd.split() + ["--version"], capture_output=True,
                              text=True, timeout=30)
        banner = (proc.stdout + proc.stderr).lower()
        if proc.returncode == 0:
            if "clang" in banner:
                kind = "clang"
            elif "gcc" in banner or "free software foundation" in banner:
                kind = "gcc"
    except (OSError, subprocess.SubprocessError):
        kind = None
    _compiler_cache[cmd] = kind
    return kind


def check_compiler(compiler, file_excludes, funcs):
    """Fail early when the selection needs GCC-only exclude flags.

    Clang has -finstrument-functions but no -finstrument-functions-exclude-*
    (https://github.com/llvm/llvm-project/issues/15627), and rejects unknown
    driver arguments, so it would fail with an opaque 'unknown argument'
    once per translation unit."""
    if compiler != "clang" or not (file_excludes or funcs):
        return
    sys.exit(
        f"this trace.config needs selective exclusion "
        f"({len(file_excludes)} file pattern(s), {len(funcs)} function "
        f"name(s)), which requires GCC.\n"
        f"clang implements -finstrument-functions but not "
        f"-finstrument-functions-exclude-file-list/-exclude-function-list "
        f"(LLVM issue #15627).\n"
        f"Options: build with GCC (e.g. make CC=gcc, or cmake "
        f"-DCMAKE_C_COMPILER=gcc), or remove the include/exclude directives "
        f"to instrument every function (that alone works on clang).")


def format_flags(file_excludes, funcs):
    """Assemble the compiler flag string from final exclude lists."""
    # De-duplicate while keeping order.
    seen = set()
    file_excludes = [p for p in file_excludes if not (p in seen or seen.add(p))]
    funcs = [f for f in funcs if not (f in seen or seen.add(f))]

    flags = "-finstrument-functions"
    if file_excludes:
        flags += " -finstrument-functions-exclude-file-list=" + ",".join(file_excludes)
    if funcs:
        flags += " -finstrument-functions-exclude-function-list=" + ",".join(funcs)
    return flags


def exclude_lists(excludes, funcs, dropped):
    """Final (file_excludes, func_excludes) for a set of dropped sources.

    Header excludes must be passed through verbatim: they don't match any
    source path, but the compiler matches them against definition files.
    main() needs the two lists separately (check_compiler inspects them), so
    this is the one place that assembles them."""
    return list(excludes) + list(dropped), list(funcs)


def instrument_flags(includes, excludes, funcs, sources):
    """Return the flag string: -finstrument-functions plus exclude lists."""
    _selected, dropped = select(sources, includes, excludes)
    return format_flags(*exclude_lists(excludes, funcs, dropped))


def with_headers(sources):
    """sources plus the headers beside them.

    Inline/static helpers DEFINED in a header are compiled into every
    including TU, so file-level exclusion of the .c files does not silence
    them — they can only be excluded by name, which means the call graph has
    to know about them."""
    headers = []
    seen_dirs = set()
    for s in sources:
        d = os.path.dirname(s) or "."
        if d and d not in seen_dirs:
            seen_dirs.add(d)
            for ext in (".h", ".hpp", ".hh"):
                try:
                    headers.extend(os.path.join(d, f)
                                   for f in os.listdir(d) if f.endswith(ext))
                except OSError:
                    continue
    return list(sources) + headers


def function_selection(include_funcs, sources, includes, excludes):
    """Expand include-func seeds into a file/function selection.

    Returns (selected, dropped, extra_exclude_funcs, reachable, warnings):
    the source selection, additional exclude-func entries, the reachable
    function set, and human-readable warnings (unknown seeds, substring
    collisions dropped from the auto-excludes)."""
    graph = callgraph.build_graph(with_headers(sources))
    warnings = []
    seeds = []
    for name, depth in include_funcs:
        if name not in graph:
            sys.exit(f"include-func: '{name}' not found in the scanned "
                     f"sources (function pointers and macro-generated calls "
                     f"are not followed; defined functions are listed by "
                     f"'callsight select --list')")
        seeds.append(name)
    # Expand each seed with its own depth limit.
    reachable = set()
    for name, depth in include_funcs:
        reachable |= callgraph.expand(graph, [name], depth)

    # Headers contribute names to the graph but are not compile units:
    # file-level selection only covers actual sources.
    files_needed = {f for fn in reachable for f in graph[fn]["files"]
                    if f.endswith(SOURCE_SUFFIXES)}
    selected = set(files_needed)
    if includes:  # explicit file includes union with the subtree files
        selected |= {s for s in sources
                     if any(matches(s, p) for p in includes)}
    selected = {s for s in selected
                if not any(matches(s, p) for p in excludes)}
    dropped = [s for s in sources if s not in selected]

    # Everything defined in the selected files but NOT reachable from the
    # seeds must not be traced: auto-exclude it by name. Header-defined
    # functions are auto-excluded too regardless of location — they are
    # compiled into arbitrary (possibly excluded) TUs and would leak.
    auto = sorted({fn for fn in graph
                   if fn not in reachable
                   and any(f in selected or not f.endswith(SOURCE_SUFFIXES)
                           for f in graph[fn]["files"])})
    # The compiler's exclude-function-list is a SUBSTRING match: an
    # auto-exclude that is a substring of a selected function's name would
    # silently disable the selected function. Drop such entries and warn.
    guarded = []
    for a in auto:
        victim = next((r for r in reachable if a != r and a in r), None)
        if victim:
            warnings.append(f"auto-exclude '{a}' skipped: it is a substring "
                            f"of selected function '{victim}' (compiler "
                            f"substring matching would disable both)")
        else:
            guarded.append(a)
    return sorted(selected), dropped, guarded, reachable, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="trace.config path")
    ap.add_argument("--scan", metavar="DIR",
                    help="recursively collect *.c/*.cc/*.cpp under DIR")
    ap.add_argument("--format", choices=("make", "raw"), default="make",
                    help="'make' prints a CFLAGS_INSTRUMENT assignment for "
                         "$(eval $(shell ...)), 'raw' prints only the flags")
    ap.add_argument("--print", action="store_true",
                    help="human-readable selection summary on stderr")
    ap.add_argument("--compiler", choices=("auto", "gcc", "clang"),
                    default="auto",
                    help="target toolchain; 'auto' detects it by running "
                         "--compiler-cmd (selective exclusion needs GCC)")
    ap.add_argument("--compiler-cmd", default=None, metavar="CC",
                    help="compiler command used for detection "
                         "(default: $CC, else cc)")
    ap.add_argument("sources", nargs="*", help="source files (after --)")
    args = ap.parse_args(argv)

    sources = list(args.sources)
    if args.scan:
        sources.extend(scan_sources(args.scan))

    if not sources:
        sys.exit("no sources given (use -- src/... or --scan DIR)")

    spec = parse_config(args.config)
    includes, excludes, funcs, include_funcs = (
        spec.includes, spec.excludes, spec.funcs, spec.include_funcs)
    reachable, warnings = None, []
    if include_funcs:
        selected, dropped, auto_funcs, reachable, warnings = \
            function_selection(include_funcs, sources, includes, excludes)
        file_excludes, func_excludes = exclude_lists(
            excludes, list(funcs) + auto_funcs, dropped)
    else:
        selected, dropped = select(sources, includes, excludes)
        file_excludes, func_excludes = exclude_lists(excludes, funcs, dropped)

    compiler = args.compiler
    if compiler == "auto":
        compiler = detect_compiler(args.compiler_cmd or os.environ.get("CC")
                                   or "cc")
    check_compiler(compiler, file_excludes, func_excludes)
    flags = format_flags(file_excludes, func_excludes)

    if args.format == "make":
        print(f"CFLAGS_INSTRUMENT = $(CFLAGS_SYMBOLS) {flags}")
    else:
        print(flags)

    if args.print:
        print(f"config:    {args.config}", file=sys.stderr)
        print(f"sources:   {len(sources)} total, {len(selected)} instrumented, "
              f"{len(dropped)} excluded", file=sys.stderr)
        if includes:
            print(f"includes:  {', '.join(includes)}", file=sys.stderr)
        if excludes:
            print(f"excludes:  {', '.join(excludes)}", file=sys.stderr)
        if funcs:
            print(f"functions: {', '.join(funcs)}", file=sys.stderr)
        if include_funcs:
            spec = ", ".join(n if d is None else f"{n} depth={d}"
                             for n, d in include_funcs)
            print(f"include-func: {spec} -> {len(reachable)} functions in "
                  f"subtree", file=sys.stderr)
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)
        for s in dropped:
            print(f"  excluded: {s}", file=sys.stderr)


if __name__ == "__main__":
    main()
