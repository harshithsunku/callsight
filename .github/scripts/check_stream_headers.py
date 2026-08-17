#!/usr/bin/env python3
"""Check that files written by `callsight serve` describe their own clock.

The device streams raw event records; how to read their timestamps lives
only in the handshake. If that metadata is lost on the way, raw cycle counts
land in the file marked as nanoseconds and every duration in the report is
wrong by the clock ratio — with nothing to show for it. So CI asserts the
headers rather than trusting that the protocol was wired up.

A big-endian device is checked the same way: the header must be readable in
the device's byte order, and mixed orders within one file — a host-endian
header in front of relayed device-endian events — are exactly what this
catches, because analyze.read_header would then report the wrong everything.

Usage: check_stream_headers.py <dir-with-trace.stream.*.bin>
"""

import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from callsight import analyze


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    files = sorted(glob.glob(f"{argv[1]}/trace.stream.*.bin"))
    if not files:
        sys.exit(f"no stream files in {argv[1]}")

    for path in files:
        # read_header is the real reader, byte order and all; checking with
        # anything else would only prove the check agrees with itself.
        meta = analyze.read_header(path)
        if meta is None:
            sys.exit(f"{path}: unreadable header")
        if meta["version"] != analyze.VERSION:
            sys.exit(f"{path}: format version {meta['version']}, "
                     f"expected {analyze.VERSION}")
        if meta["header_size"] != analyze.HEADER.size:
            sys.exit(f"{path}: header_size {meta['header_size']} != "
                     f"{analyze.HEADER.size}")
        if (meta["flags"] & analyze.HF_TICKS) and meta["tick_hz"] <= 0:
            sys.exit(f"{path}: timestamps are raw ticks but no tick rate was "
                     f"carried over the wire — the handshake lost it")
        # The events behind the header must be in the same order as the
        # header itself: a segment whose two halves disagree parses into
        # nonsense with no error anywhere.
        flagged = bool(meta["flags"] & analyze.HF_BIGENDIAN)
        if flagged != meta["big_endian"]:
            sys.exit(f"{path}: header byte order ({meta['big_endian']}) "
                     f"contradicts TRACE_HF_BIGENDIAN ({flagged}) — the "
                     f"server wrote a header in the wrong order")
    orders = {("big" if analyze.read_header(p)["big_endian"] else "little")
              for p in files}
    print(f"{len(files)} stream segment(s) carry a readable clock "
          f"({'/'.join(sorted(orders))}-endian)")


if __name__ == "__main__":
    main(sys.argv)
