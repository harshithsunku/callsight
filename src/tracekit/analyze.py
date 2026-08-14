#!/usr/bin/env python3
"""
Offline analyzer for compile-time traces produced by the tracekit runtime.

Reads the binary trace files written by trace.c, resolves function addresses
with addr2line (static functions included), matches enter/exit events per
thread, and reports per-function call counts and inclusive/self times.

Usage:
    tracekit analyze [traces/] [--exe path/to/binary] [--top 20]

If --exe is omitted, a single `*.instr` binary is looked up under ./bin and .
Build instrumented binaries with -no-pie (the Make/CMake integrations do
this) so recorded runtime addresses can be fed to addr2line directly.
"""

import argparse
import struct
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

MAGIC = b"MLTRACE\0"
HEADER = struct.Struct("<8sII")
EVENT = struct.Struct("<QQQIB3x")

ENTER, EXIT = 0, 1


def read_events(path):
    """Yield (tid, kind, func_addr, ts_ns) from one trace file."""
    data = path.read_bytes()
    if len(data) < HEADER.size or data[:8] != MAGIC:
        print(f"warning: {path}: bad or missing header, skipped", file=sys.stderr)
        return
    hdr = HEADER.unpack_from(data, 0)
    if hdr[2] != EVENT.size:
        print(f"warning: {path}: event size {hdr[2]} != {EVENT.size}, skipped",
              file=sys.stderr)
        return
    body = memoryview(data)[HEADER.size:]
    n = len(body) // EVENT.size
    for i in range(n):
        ts, func, _caller, tid, kind = EVENT.unpack_from(body, i * EVENT.size)
        yield tid, kind, func, ts


def resolve(addrs, exe):
    """Resolve addresses to (function, file:line) via one batched addr2line run."""
    addrs = sorted(addrs)
    names = {}
    if not addrs:
        return names
    proc = subprocess.run(
        ["addr2line", "-f", "-C", "-e", str(exe)] + [hex(a) for a in addrs],
        capture_output=True, text=True, check=True)
    lines = proc.stdout.splitlines()
    for i, a in enumerate(addrs):
        fn = lines[2 * i] if 2 * i < len(lines) else "??"
        loc = lines[2 * i + 1] if 2 * i + 1 < len(lines) else "??:0"
        names[a] = (fn, loc)
    return names


def analyze(events):
    """Match enter/exit per thread; return per-function stats and thread info."""
    calls = defaultdict(int)
    incl = defaultdict(int)   # inclusive ns
    self_t = defaultdict(int)  # self ns (inclusive minus children)
    max_t = defaultdict(int)
    stacks = defaultdict(list)  # tid -> [(func, enter_ts, child_ns)]
    unmatched_exits = 0
    first_ts = {}
    last_ts = {}

    for tid, kind, func, ts in events:
        first_ts.setdefault(tid, ts)
        last_ts[tid] = ts
        st = stacks[tid]
        if kind == ENTER:
            st.append([func, ts, 0])
        else:
            # find nearest unmatched enter for this function
            idx = next((i for i in range(len(st) - 1, -1, -1)
                        if st[i][0] == func), None)
            if idx is None:
                unmatched_exits += 1
                continue
            # close any dangling frames above (e.g. after longjmp/truncation)
            for _ in range(len(st) - 1 - idx):
                st.pop()
            f, enter_ts, child_ns = st.pop()
            dur = ts - enter_ts
            calls[f] += 1
            incl[f] += dur
            self_t[f] += dur - child_ns
            if dur > max_t[f]:
                max_t[f] = dur
            if st:
                st[-1][2] += dur

    stats = {f: (calls[f], incl[f], self_t[f], max_t[f]) for f in calls}
    threads = {t: (first_ts[t], last_ts[t]) for t in first_ts}
    open_frames = sum(len(s) for s in stacks.values())
    return stats, threads, unmatched_exits, open_frames


def find_exe(arg):
    if arg:
        return arg
    candidates = list(Path("bin").glob("*.instr")) + list(Path(".").glob("*.instr"))
    if len(candidates) == 1:
        return str(candidates[0])
    sys.exit("--exe not given and "
             + ("no *.instr binary found under ./bin or ." if not candidates
                else f"multiple *.instr binaries found: {', '.join(map(str, candidates))}"))


def collect(tracedir, exe):
    """Analyze all trace files in tracedir; return structured report data.

    Dict with summary counters, a 'rows' list (one per function: calls,
    incl/self/max ms, resolved name/location) and 'per_thread' timing."""
    files = sorted(Path(tracedir).glob("trace.*.bin"))
    if not files:
        raise RuntimeError(f"no trace files in {tracedir}")

    events = []
    for f in files:
        events.extend(read_events(f))
    if not events:
        raise RuntimeError("trace files contained no events")

    names = resolve({ev[2] for ev in events}, exe)
    stats, threads, unmatched, open_frames = analyze(events)

    span_ns = max(t[1] for t in threads.values()) - min(t[0] for t in threads.values())
    rows = []
    for func, (calls, incl, self_t, max_t) in stats.items():
        fn, loc = names.get(func, ("??", "??:0"))
        rows.append({"function": fn, "location": loc, "calls": calls,
                     "incl_ms": incl / 1e6, "self_ms": self_t / 1e6,
                     "max_ms": max_t / 1e6})
    per_tid = defaultdict(int)
    for tid, *_ in events:
        per_tid[tid] += 1
    per_thread = [{"tid": tid, "events": per_tid[tid],
                   "span_ms": (threads[tid][1] - threads[tid][0]) / 1e6}
                  for tid in sorted(per_tid)]
    return {"events": len(events), "threads": len(threads),
            "functions": len(stats), "span_ms": span_ns / 1e6,
            "unmatched_exits": unmatched, "unclosed_enters": open_frames,
            "rows": rows, "per_thread": per_thread}


def report(tracedir, exe, top):
    """Analyze all trace files in tracedir and print the hotspot report.

    Returns the unmatched_exits count (0 means a clean trace)."""
    exe = find_exe(exe)
    data = collect(tracedir, exe)

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

    return data["unmatched_exits"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracedir", nargs="?", default="traces",
                    help="directory with trace.<pid>.<tid>.bin files")
    ap.add_argument("--exe", default=None,
                    help="instrumented binary for addr2line "
                         "(default: the single *.instr under ./bin or .)")
    ap.add_argument("--top", type=int, default=20, help="rows per table")
    args = ap.parse_args(argv)
    try:
        report(args.tracedir, args.exe, args.top)
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
