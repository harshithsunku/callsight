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
"""

import argparse
import fnmatch
import os
import sys

try:
    from callsight import callgraph
except ImportError:  # direct execution: python3 src/callsight/flags.py
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from callsight import callgraph

SOURCE_SUFFIXES = (".c", ".cc", ".cpp")


def parse_config(path):
    includes, excludes, funcs, include_funcs = [], [], [], []
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
                name, _, depth = value.partition(" ")
                if depth:
                    try:
                        depth = int(depth)
                    except ValueError:
                        sys.exit(f"{path}:{lineno}: include-func depth must "
                                 f"be an integer, got '{depth}'")
                else:
                    depth = None
                include_funcs.append((name, depth))
            else:
                sys.exit(f"{path}:{lineno}: unknown directive '{directive}' "
                         f"(want: include, exclude, exclude-func, "
                         f"include-func)")
    return includes, excludes, funcs, include_funcs


def render_config(excluded_files=(), include_funcs=(), excluded_funcs=()):
    """Render trace.config text from web-UI config-builder selections.

    excluded_files: project-relative paths -> 'exclude' lines (the default is
        include-everything, so only exclusions need lines).
    include_funcs: iterable of (name, depth-or-None) -> 'include-func' lines.
    excluded_funcs: iterable of names -> 'exclude-func' lines.
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


def instrument_flags(includes, excludes, funcs, sources):
    """Return the flag string: -finstrument-functions plus exclude lists."""
    _selected, dropped = select(sources, includes, excludes)

    # Header excludes must be passed through verbatim: they don't match any
    # source path, but the compiler matches them against definition files.
    return format_flags([p for p in excludes] + dropped, funcs)


def function_selection(include_funcs, sources, includes, excludes):
    """Expand include-func seeds into a file/function selection.

    Returns (selected, dropped, extra_exclude_funcs, reachable, warnings):
    the source selection, additional exclude-func entries, the reachable
    function set, and human-readable warnings (unknown seeds, substring
    collisions dropped from the auto-excludes)."""
    # Headers are parsed too: inline/static helpers DEFINED in a header are
    # compiled into every including TU, so file-level exclusion of the .c
    # files does not silence them — they can only be excluded by name.
    headers = []
    seen_dirs = set()
    for s in sources:
        d = os.path.dirname(s) or "."
        if d and d not in seen_dirs:
            seen_dirs.add(d)
            for ext in (".h", ".hpp", ".hh"):
                headers.extend(os.path.join(d, f)
                               for f in os.listdir(d) if f.endswith(ext))
    graph = callgraph.build_graph(list(sources) + headers)
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
    ap.add_argument("sources", nargs="*", help="source files (after --)")
    args = ap.parse_args(argv)

    sources = list(args.sources)
    if args.scan:
        sources.extend(scan_sources(args.scan))

    if not sources:
        sys.exit("no sources given (use -- src/... or --scan DIR)")

    includes, excludes, funcs, include_funcs = parse_config(args.config)
    reachable, warnings = None, []
    if include_funcs:
        selected, dropped, auto_funcs, reachable, warnings = \
            function_selection(include_funcs, sources, includes, excludes)
        flags = format_flags(list(excludes) + dropped,
                             list(funcs) + auto_funcs)
    else:
        selected, dropped = select(sources, includes, excludes)
        flags = instrument_flags(includes, excludes, funcs, sources)

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
