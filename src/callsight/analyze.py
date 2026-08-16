#!/usr/bin/env python3
"""
Offline analyzer for compile-time traces produced by the callsight runtime.

Reads the binary trace files written by trace.c, resolves function addresses
with addr2line (static functions included), matches enter/exit events per
thread, and reports per-function call counts and inclusive/self times.

Usage:
    callsight analyze [traces/] [--exe path/to/binary] [--top 20]
                      [--format text|json|folded]

If --exe is omitted, a single `*.instr` binary is looked up under ./bin and .
Build instrumented binaries with -no-pie (the Make/CMake integrations do
this) so recorded runtime addresses can be fed to addr2line directly.

Trace files are streamed: events are matched as they are read and never all
held in memory, so a multi-million-event trace costs a few MB here instead of
scaling with the event count.
"""

import argparse
import json
import os
import struct
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

MAGIC = b"MLTRACE\0"
HEADER = struct.Struct("<8sII")
EVENT = struct.Struct("<QQQIB3x")
VERSION = 1  # TRACE_FILE_VERSION in runtime/trace.h

ENTER, EXIT = 0, 1

# Events per read() while streaming a trace file.
READ_BLOCK_EVENTS = 65536
# Addresses per addr2line invocation (a big program's address set would
# otherwise approach ARG_MAX on one command line).
ADDR2LINE_BATCH = 4096


def read_events(path):
    """Yield (tid, kind, func_addr, ts_ns) from one trace file.

    Streams the file in blocks; a bad header, an unknown format version or a
    mismatched event size skips the file with a warning."""
    with open(path, "rb") as f:
        head = f.read(HEADER.size)
        if len(head) < HEADER.size or head[:8] != MAGIC:
            print(f"warning: {path}: bad or missing header, skipped",
                  file=sys.stderr)
            return
        _magic, version, event_size = HEADER.unpack(head)
        if version != VERSION:
            print(f"warning: {path}: trace format version {version} != "
                  f"{VERSION}, skipped", file=sys.stderr)
            return
        if event_size != EVENT.size:
            print(f"warning: {path}: event size {event_size} != {EVENT.size}, "
                  f"skipped", file=sys.stderr)
            return

        block = READ_BLOCK_EVENTS * EVENT.size
        rest = b""
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            if rest:
                chunk = rest + chunk
            usable = len(chunk) - len(chunk) % EVENT.size
            for ts, func, _caller, tid, kind in EVENT.iter_unpack(chunk[:usable]):
                yield tid, kind, func, ts
            rest = chunk[usable:]
        if rest:
            # A killed process can leave a partial record behind; the events
            # before it are still good.
            print(f"warning: {path}: {len(rest)} trailing bytes ignored "
                  f"(truncated final record)", file=sys.stderr)


def resolve(addrs, exe):
    """Resolve addresses to (function, file:line) via batched addr2line runs."""
    addrs = sorted(addrs)
    names = {}
    for start in range(0, len(addrs), ADDR2LINE_BATCH):
        batch = addrs[start:start + ADDR2LINE_BATCH]
        cmd = ["addr2line", "-f", "-C", "-e", str(exe)] + [hex(a) for a in batch]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  check=True)
        except FileNotFoundError:
            raise RuntimeError(
                "addr2line not found on PATH — install binutils "
                "(Debian/Ubuntu: apt install binutils) or put the matching "
                "cross-toolchain addr2line on PATH")
        except OSError as e:
            raise RuntimeError(f"could not run addr2line: {e}")
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip().splitlines()
            raise RuntimeError(
                f"addr2line failed on {exe} (exit {e.returncode})"
                + (f": {detail[0]}" if detail else ""))
        lines = proc.stdout.splitlines()
        for i, a in enumerate(batch):
            fn = lines[2 * i] if 2 * i < len(lines) else "??"
            loc = lines[2 * i + 1] if 2 * i + 1 < len(lines) else "??:0"
            names[a] = (fn, loc)
    return names


class Accumulator:
    """Incremental enter/exit matcher.

    feed() one event at a time in per-thread order (events from different
    threads may interleave); every counter the report needs is maintained on
    the way through, so no event list is ever materialized. With
    folded=True it also accumulates self time per distinct call path, keyed
    by the tuple of addresses on the stack, for flame-graph output."""

    def __init__(self, folded=False):
        self.calls = defaultdict(int)
        self.incl = defaultdict(int)    # inclusive ns
        self.self_t = defaultdict(int)  # self ns (inclusive minus children)
        self.max_t = defaultdict(int)
        self.stacks = defaultdict(list)  # tid -> [[func, enter_ts, child_ns]]
        self.unmatched_exits = 0
        self.events = 0
        self.first_ts = {}
        self.last_ts = {}
        self.per_tid = defaultdict(int)
        self.addrs = set()
        self.folded = defaultdict(int) if folded else None

    def feed(self, tid, kind, func, ts):
        self.events += 1
        self.first_ts.setdefault(tid, ts)
        self.last_ts[tid] = ts
        self.per_tid[tid] += 1
        self.addrs.add(func)

        st = self.stacks[tid]
        if kind == ENTER:
            st.append([func, ts, 0])
            return

        # find nearest unmatched enter for this function
        idx = next((i for i in range(len(st) - 1, -1, -1)
                    if st[i][0] == func), None)
        if idx is None:
            self.unmatched_exits += 1
            return
        # close any dangling frames above (e.g. after longjmp/truncation)
        del st[idx + 1:]
        f, enter_ts, child_ns = st.pop()
        dur = ts - enter_ts
        self.calls[f] += 1
        self.incl[f] += dur
        self.self_t[f] += dur - child_ns
        if dur > self.max_t[f]:
            self.max_t[f] = dur
        if self.folded is not None:
            path = tuple(frame[0] for frame in st) + (f,)
            self.folded[path] += dur - child_ns
        if st:
            st[-1][2] += dur

    def finish(self):
        """Return (stats, threads, unmatched_exits, open_frames)."""
        stats = {f: (self.calls[f], self.incl[f], self.self_t[f], self.max_t[f])
                 for f in self.calls}
        threads = {t: (self.first_ts[t], self.last_ts[t]) for t in self.first_ts}
        open_frames = sum(len(s) for s in self.stacks.values())
        return stats, threads, self.unmatched_exits, open_frames


def analyze(events):
    """Match enter/exit per thread; return per-function stats and thread info."""
    acc = Accumulator()
    for tid, kind, func, ts in events:
        acc.feed(tid, kind, func, ts)
    return acc.finish()


def find_exe(arg):
    if arg:
        return arg
    candidates = list(Path("bin").glob("*.instr")) + list(Path(".").glob("*.instr"))
    if len(candidates) == 1:
        return str(candidates[0])
    sys.exit("--exe not given and "
             + ("no *.instr binary found under ./bin or ." if not candidates
                else f"multiple *.instr binaries found: {', '.join(map(str, candidates))}"))


def collect(tracedir, exe, folded=False):
    """Analyze all trace files in tracedir; return structured report data.

    Dict with summary counters, a 'rows' list (one per function: calls,
    incl/self/max ms, resolved name/location) and 'per_thread' timing. With
    folded=True it also carries 'folded': [(path, self_ns)] collapsed-stack
    rows, highest self time first."""
    files = sorted(Path(tracedir).glob("trace.*.bin"))
    if not files:
        raise RuntimeError(f"no trace files in {tracedir}")

    # One accumulator across all files: in streaming mode a thread's events
    # can be split over several trace.stream.*.bin files.
    acc = Accumulator(folded=folded)
    for f in files:
        for tid, kind, func, ts in read_events(f):
            acc.feed(tid, kind, func, ts)
    if acc.events == 0:
        raise RuntimeError("trace files contained no events")

    names = resolve(acc.addrs, exe)
    _warn_if_unresolved(names, exe)
    stats, threads, unmatched, open_frames = acc.finish()

    span_ns = max(t[1] for t in threads.values()) - min(t[0] for t in threads.values())
    rows = []
    for func, (calls, incl, self_t, max_t) in stats.items():
        fn, loc = names.get(func, ("??", "??:0"))
        rows.append({"function": fn, "location": loc, "calls": calls,
                     "incl_ms": incl / 1e6, "self_ms": self_t / 1e6,
                     "max_ms": max_t / 1e6})
    per_thread = [{"tid": tid, "events": acc.per_tid[tid],
                   "span_ms": (threads[tid][1] - threads[tid][0]) / 1e6}
                  for tid in sorted(acc.per_tid)]
    data = {"events": acc.events, "threads": len(threads),
            "functions": len(stats), "span_ms": span_ns / 1e6,
            "unmatched_exits": unmatched, "unclosed_enters": open_frames,
            "rows": rows, "per_thread": per_thread}
    if folded:
        data["folded"] = sorted(
            ((";".join(names.get(a, ("??", ""))[0] for a in path), ns)
             for path, ns in acc.folded.items()),
            key=lambda r: r[1], reverse=True)
    return data


def _warn_if_unresolved(names, exe):
    """A PIE binary records load-time addresses addr2line cannot map back,
    which yields a full report of '??' instead of an error. Say so."""
    if not names:
        return
    unresolved = sum(1 for fn, _loc in names.values() if fn == "??")
    if unresolved * 2 >= len(names):
        print(f"warning: {unresolved}/{len(names)} addresses did not resolve "
              f"in {exe} — if it was linked as a position-independent "
              f"executable, relink with -no-pie (the Make/CMake integrations "
              f"do) so runtime addresses match link addresses",
              file=sys.stderr)


def _version():
    try:
        from callsight import __version__
        return __version__
    except Exception:
        return "unknown"


def print_text(data, top):
    print(f"events={data['events']} threads={data['threads']} "
          f"functions={data['functions']} span={data['span_ms']:.1f}ms "
          f"unmatched_exits={data['unmatched_exits']} "
          f"unclosed_enters={data['unclosed_enters']}")
    print()

    hdr = f"{'calls':>10} {'incl_ms':>12} {'self_ms':>12} {'max_ms':>12}  function (first location)"
    for label, key in (("TOP BY SELF TIME", "self_ms"),
                       ("TOP BY INCLUSIVE TIME", "incl_ms")):
        print(f"== {label} ==")
        print(hdr)
        rows = sorted(data["rows"], key=lambda r: r[key], reverse=True)[:top]
        for r in rows:
            print(f"{r['calls']:>10} {r['incl_ms']:>12.3f} {r['self_ms']:>12.3f} "
                  f"{r['max_ms']:>12.3f}  {r['function']} ({r['location']})")
        print()

    print("== PER-THREAD SUMMARY ==")
    print(f"{'tid':>8} {'events':>10} {'span_ms':>12}")
    for t in data["per_thread"]:
        print(f"{t['tid']:>8} {t['events']:>10} {t['span_ms']:>12.3f}")


def print_json(data, top):
    out = dict(data)
    out["tool"] = "callsight"
    out["version"] = _version()
    out["rows"] = sorted(data["rows"], key=lambda r: r["self_ms"], reverse=True)
    if top > 0:
        out["rows"] = out["rows"][:top]
    json.dump(out, sys.stdout, indent=2)
    print()


def print_folded(data):
    """Collapsed stacks: '<caller>;<callee> <self_ns>', one per call path.

    The input format of flamegraph.pl and speedscope; values are nanoseconds
    of self time."""
    for path, ns in data["folded"]:
        print(f"{path} {ns}")


def report(tracedir, exe, top, fmt="text"):
    """Analyze all trace files in tracedir and print the report.

    Returns the unmatched_exits count (0 means a clean trace)."""
    exe = find_exe(exe)
    data = collect(tracedir, exe, folded=(fmt == "folded"))

    if fmt == "json":
        print_json(data, top)
    elif fmt == "folded":
        print_folded(data)
    else:
        print_text(data, top)

    return data["unmatched_exits"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracedir", nargs="?", default="traces",
                    help="directory with trace.<pid>.<tid>.bin files")
    ap.add_argument("--exe", default=None,
                    help="instrumented binary for addr2line "
                         "(default: the single *.instr under ./bin or .)")
    ap.add_argument("--top", type=int, default=20,
                    help="rows per table (json: 0 means all rows)")
    ap.add_argument("--format", choices=("text", "json", "folded"),
                    default="text",
                    help="text tables (default), json for tooling, or "
                         "folded collapsed stacks for flamegraph.pl and "
                         "speedscope")
    args = ap.parse_args(argv)
    try:
        report(args.tracedir, args.exe, args.top, args.format)
    except RuntimeError as e:
        sys.exit(str(e))
    except BrokenPipeError:
        # `callsight analyze --format folded | head` and friends. Redirect
        # stdout to devnull so the interpreter's shutdown flush cannot raise
        # a second time (see the note in the Python stdlib docs on SIGPIPE).
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)


if __name__ == "__main__":
    main()
