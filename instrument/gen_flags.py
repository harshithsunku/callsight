#!/usr/bin/env python3
"""
gen_flags.py — generate selective compile-time instrumentation flags.

Reads a trace.config plus the project's source file list, decides which
translation units get -finstrument-functions, and prints a Makefile fragment
(CFLAGS_INSTRUMENT) to stdout. Typical use inside a Makefile:

    $(eval $(shell python3 $(INSTR_DIR)/gen_flags.py \
        --config $(INSTR_DIR)/trace.config -- $(SRCS)))

or standalone:

    python3 gen_flags.py --config trace.config --scan src --print

Config syntax (one directive per line, '#' comments, blank lines ignored):

    include <pattern>        only instrument matching sources
                             (default: everything is included)
    exclude <pattern>        never instrument matching sources
    exclude-func <name>      never instrument functions whose (mangled) name
                             contains <name>  (compiler substring match)

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
  - Both compile-time excludes are FREE at runtime: no hook is emitted at
    all. Prefer them over any runtime filtering.
"""

import argparse
import fnmatch
import os
import sys


def parse_config(path):
    includes, excludes, funcs = [], [], []
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
            else:
                sys.exit(f"{path}:{lineno}: unknown directive '{directive}' "
                         f"(want: include, exclude, exclude-func)")
    return includes, excludes, funcs


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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="trace.config path")
    ap.add_argument("--scan", metavar="DIR",
                    help="recursively collect *.c/*.cc/*.cpp under DIR")
    ap.add_argument("--print", action="store_true",
                    help="human-readable selection summary on stderr")
    ap.add_argument("sources", nargs="*", help="source files (after --)")
    args = ap.parse_args()

    sources = list(args.sources)
    if args.scan:
        for root, _dirs, files in os.walk(args.scan):
            for f in files:
                if f.endswith((".c", ".cc", ".cpp")):
                    sources.append(os.path.join(root, f))

    if not sources:
        sys.exit("no sources given (use -- src/... or --scan DIR)")

    includes, excludes, funcs = parse_config(args.config)
    selected, dropped = select(sources, includes, excludes)

    # Header excludes must be passed through verbatim: they don't match any
    # source path, but the compiler matches them against definition files.
    file_excludes = [p for p in excludes] + dropped
    # De-duplicate while keeping order.
    seen = set()
    file_excludes = [p for p in file_excludes if not (p in seen or seen.add(p))]

    flags = "CFLAGS_INSTRUMENT = $(CFLAGS_SYMBOLS) -finstrument-functions"
    if file_excludes:
        flags += " -finstrument-functions-exclude-file-list=" + ",".join(file_excludes)
    if funcs:
        flags += " -finstrument-functions-exclude-function-list=" + ",".join(funcs)
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
        for s in dropped:
            print(f"  excluded: {s}", file=sys.stderr)


if __name__ == "__main__":
    main()
