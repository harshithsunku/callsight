#!/usr/bin/env python3
"""Measure what callsight actually costs, per hook and per workload.

Publishing an overhead number without a way to reproduce it is just a
claim. This builds the same workload twice — plain and instrumented — runs
every capture mode against it, and prints the per-event cost and the bytes
each mode put on disk.

    python3 tests/bench/run_bench.py [--iters 2000000] [--repeat 5]

Numbers are machine-specific. What should hold anywhere: an instrumented
build with tracing off costs close to nothing, the cycle-counter clock beats
clock_gettime, and summary mode writes a constant number of bytes however
long the run is.
"""

import argparse
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIME = ROOT / "src" / "callsight" / "runtime"
BENCH = Path(__file__).resolve().parent / "bench.c"
CC = os.environ.get("CC", "cc")


def build(tmp, instrumented):
    """Both builds get identical optimization; only the hooks differ."""
    out = Path(tmp) / ("bench.instr" if instrumented else "bench.plain")
    common = [CC, "-std=c11", "-O2", "-g", f"-I{RUNTIME}"]
    objs = []
    flags = ["-finstrument-functions"] if instrumented else []
    subprocess.run(common + flags + ["-c", "-o", f"{out}.main.o", str(BENCH)],
                   check=True)
    objs.append(f"{out}.main.o")
    if instrumented:
        subprocess.run(common + ["-c", "-o", f"{out}.trace.o",
                                 str(RUNTIME / "trace.c")], check=True)
        objs.append(f"{out}.trace.o")
    subprocess.run([CC, "-o", str(out)] + objs + ["-lpthread"], check=True)
    return str(out)


def run_once(exe, iters, env=None, tracedir=None, work=0):
    full = dict(os.environ)
    full.pop("TRACE_ENABLE", None)
    if tracedir:
        full["TRACE_DIR"] = str(tracedir)
    full.update(env or {})
    proc = subprocess.run([exe, str(iters), str(work)], env=full,
                          capture_output=True, text=True, check=True)
    m = re.search(r"elapsed_ns=(\d+).*hooks=(\d+)", proc.stdout)
    if not m:
        raise RuntimeError(f"unexpected output: {proc.stdout!r}")
    return int(m.group(1)), int(m.group(2))


def measure(exe, iters, repeat, env=None, tmp=None, label="", work=0):
    """Best-of-N: the minimum is the run least disturbed by the rest of the
    machine, which is what we want to compare."""
    times, hooks, written = [], 0, 0
    for i in range(repeat):
        tracedir = None
        if env and env.get("TRACE_ENABLE") == "1":
            tracedir = Path(tmp) / f"t{label}{i}"
            tracedir.mkdir(parents=True, exist_ok=True)
        ns, hooks = run_once(exe, iters, env, tracedir, work)
        times.append(ns)
        if tracedir:
            written = sum(f.stat().st_size for f in tracedir.glob("*.bin"))
            shutil.rmtree(tracedir, ignore_errors=True)
    return min(times), statistics.median(times), hooks, written


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iters", type=int, default=2000000)
    ap.add_argument("--repeat", type=int, default=5)
    args = ap.parse_args()

    if shutil.which(CC) is None:
        sys.exit(f"no C compiler ({CC}) on PATH")

    tmp = tempfile.mkdtemp(prefix="callsight-bench-")
    try:
        print(f"building (iters={args.iters:,}, best of {args.repeat})...")
        plain = build(tmp, instrumented=False)
        instr = build(tmp, instrumented=True)

        base, _med, hooks, _w = measure(plain, args.iters, args.repeat)
        print(f"\nbaseline (no hooks compiled in): {base / 1e6:.2f} ms "
              f"for {args.iters:,} iterations\n")

        scenarios = [
            ("instrumented, tracing off", {}),
            ("TRACE_CLOCK=tsc (default here)",
             {"TRACE_ENABLE": "1", "TRACE_CLOCK": "tsc", "TRACE_MAX_MB": "0"}),
            ("TRACE_CLOCK=mono",
             {"TRACE_ENABLE": "1", "TRACE_CLOCK": "mono", "TRACE_MAX_MB": "0"}),
            ("TRACE_CLOCK=raw",
             {"TRACE_ENABLE": "1", "TRACE_CLOCK": "raw", "TRACE_MAX_MB": "0"}),
            ("TRACE_MODE=summary",
             {"TRACE_ENABLE": "1", "TRACE_MODE": "summary"}),
        ]

        header = (f"{'mode':<34}{'total':>10}{'vs plain':>10}"
                  f"{'ns/hook':>10}{'on disk':>10}")
        print(header)
        print("-" * len(header))
        print(f"{'plain build (no hooks)':<34}{base / 1e6:>9.1f}ms"
              f"{1.0:>10.2f}{'-':>10}{'-':>10}")

        for i, (label, env) in enumerate(scenarios):
            ns, _med, hooks, written = measure(instr, args.iters, args.repeat,
                                               env, tmp, label=str(i))
            per_hook = (ns - base) / hooks
            print(f"{label:<34}{ns / 1e6:>9.1f}ms{ns / base:>10.2f}"
                  f"{per_hook:>10.1f}"
                  f"{human(written) if written else '-':>10}")

        print(f"\n{hooks:,} hook calls per run "
              f"({args.iters:,} iterations x 2 functions x enter+exit)")
        print("The 'vs plain' column above is the worst case by "
              "construction: these\nfunctions do nothing, so the hooks are "
              "the entire cost. Below is the\nsame measurement where each "
              "call does real work.")

        # A ratio is only meaningful against a function that does something.
        # Report the slowdown at a few realistic per-call costs.
        print(f"\n{'work per call':<20}{'plain':>12}{'traced':>12}"
              f"{'slowdown':>10}")
        print("-" * 54)
        for work in (8, 32, 128, 512):
            iters = max(args.iters // 8, 50000)
            p, _m, _h, _w = measure(plain, iters, max(3, args.repeat // 2),
                                    work=work)
            t, _m, _h, _w = measure(instr, iters, max(3, args.repeat // 2),
                                    {"TRACE_ENABLE": "1", "TRACE_MAX_MB": "0"},
                                    tmp, label=f"w{work}", work=work)
            per_call = p / (iters * 2)
            print(f"{f'~{per_call:.0f} ns/call':<20}{p / 1e6:>11.1f}ms"
                  f"{t / 1e6:>11.1f}ms{t / p:>9.2f}x")

        # Constant-memory claim, stated as a measurement rather than a
        # promise: ten times the calls must not mean ten times the bytes.
        small = Path(tmp) / "sum-small"
        big = Path(tmp) / "sum-big"
        small.mkdir(exist_ok=True)
        big.mkdir(exist_ok=True)
        senv = {"TRACE_ENABLE": "1", "TRACE_MODE": "summary"}
        run_once(instr, args.iters // 10, senv, small)
        run_once(instr, args.iters, senv, big)
        s = sum(f.stat().st_size for f in small.glob("*.bin"))
        b = sum(f.stat().st_size for f in big.glob("*.bin"))
        print(f"\nsummary mode on disk: {human(s)} for "
              f"{args.iters // 10:,} iterations, {human(b)} for "
              f"{args.iters:,} — {'constant' if s == b else 'NOT CONSTANT'}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
