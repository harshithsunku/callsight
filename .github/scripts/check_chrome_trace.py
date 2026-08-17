#!/usr/bin/env python3
"""Check that `callsight analyze --format chrome` emits a loadable trace.

The Chrome trace format is what ui.perfetto.dev reads. A malformed one
fails silently in the viewer — an empty timeline looks the same as a trace
with no events — so CI parses it and checks the shape instead.

Usage: check_chrome_trace.py <trace.json>
"""

import json
import sys


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: check_chrome_trace.py <trace.json>")
    with open(argv[1]) as f:
        data = json.load(f)

    events = data.get("traceEvents")
    if not events:
        sys.exit(f"{argv[1]}: no traceEvents")
    for e in events[:1000]:
        if e.get("ph") != "X":
            sys.exit(f"{argv[1]}: unexpected phase {e.get('ph')!r}")
        if e.get("ts", -1) < 0 or e.get("dur", -1) < 0:
            sys.exit(f"{argv[1]}: negative ts/dur in {e}")
        if not e.get("name"):
            sys.exit(f"{argv[1]}: unnamed span in {e}")
    print(f"{len(events)} spans, {len({e['tid'] for e in events})} threads")


if __name__ == "__main__":
    main(sys.argv)
