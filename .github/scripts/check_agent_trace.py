#!/usr/bin/env python3
"""Validate a trace produced by a foreign-architecture agent.

The agent (32-bit, big-endian, or both) runs `runtime_probe spin N` under
qemu; this runs on the x86 host and checks that what came back is not merely
parseable but *exactly right*. The probe's call graph is fixed —

    probe_top   once
    probe_mid   N times
    probe_leaf  8N times

— so the same source traced on any architecture must produce the same three
numbers. Anything else means the header, the addresses or the timestamps were
read the wrong way round, and byte-order bugs specifically do not announce
themselves: they produce plausible-looking garbage.

Usage:
    check_agent_trace.py TRACEDIR EXE N [--addr2line CMD] [--expect-endian be|le]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from callsight import analyze


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tracedir")
    ap.add_argument("exe")
    ap.add_argument("n", type=int, help="the N passed to `spin`")
    ap.add_argument("--addr2line", help="the target toolchain's addr2line")
    ap.add_argument("--expect-endian", choices=("le", "be"),
                    help="fail unless the agent recorded this byte order")
    args = ap.parse_args()

    metas = analyze._open_metas(analyze._event_files(args.tracedir))
    if not metas:
        sys.exit(f"no readable trace files in {args.tracedir}")
    big = any(m.get("big_endian") for m in metas)
    order = "be" if big else "le"
    print(f"agent byte order: {'big' if big else 'little'}-endian "
          f"({len(metas)} segment(s))")
    if args.expect_endian and order != args.expect_endian:
        sys.exit(f"expected a {args.expect_endian}-endian trace, got {order}")
    # The flag and the layout detection are independent mechanisms; if they
    # disagree, one of them is broken and the answer cannot be trusted.
    flagged = any(m["flags"] & analyze.HF_BIGENDIAN for m in metas
                  if m["version"] >= 2)
    if any(m["version"] >= 2 for m in metas) and flagged != big:
        sys.exit(f"TRACE_HF_BIGENDIAN={flagged} contradicts the detected "
                 f"layout ({order}) — one of the two is wrong")

    data = analyze.collect(args.tracedir, args.exe, addr2line=args.addr2line)
    counts = {r["function"]: r["calls"] for r in data["rows"]}
    expected = {"probe_top": 1, "probe_mid": args.n,
                "probe_leaf": 8 * args.n}

    bad = []
    for name, want in expected.items():
        got = counts.get(name)
        if got != want:
            bad.append(f"  {name}: expected {want} calls, got {got}")
    if data["unmatched_exits"]:
        bad.append(f"  unmatched_exits={data['unmatched_exits']}, expected 0")
    if bad:
        print("trace does not match the probe's known call graph:")
        print("\n".join(bad))
        print(f"\nresolved functions: {sorted(counts)}")
        sys.exit(1)

    print(f"exact call counts match on a foreign agent: "
          f"probe_top=1 probe_mid={args.n} probe_leaf={8 * args.n}, "
          f"unmatched_exits=0")


if __name__ == "__main__":
    main()
