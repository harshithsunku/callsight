#!/usr/bin/env python3
"""Check that files written by `callsight serve` describe their own clock.

The device streams raw event records; how to read their timestamps lives
only in the handshake. If that metadata is lost on the way, raw cycle counts
land in the file marked as nanoseconds and every duration in the report is
wrong by the clock ratio — with nothing to show for it. So CI asserts the
headers rather than trusting that the protocol was wired up.

Usage: check_stream_headers.py <dir-with-trace.stream.*.bin>
"""

import glob
import struct
import sys

HEADER = struct.Struct("<8sIIIIQQQQQIIQ")
MAGIC = b"MLTRACE\0"
HF_TICKS = 0x1


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    files = sorted(glob.glob(f"{argv[1]}/trace.stream.*.bin"))
    if not files:
        sys.exit(f"no stream files in {argv[1]}")

    for path in files:
        with open(path, "rb") as f:
            raw = f.read(HEADER.size)
        if len(raw) < HEADER.size:
            sys.exit(f"{path}: shorter than one header")
        magic, version, event_size, header_size, flags = HEADER.unpack(raw)[:5]
        tick_hz = HEADER.unpack(raw)[6]
        if magic != MAGIC:
            sys.exit(f"{path}: bad magic {magic!r}")
        if version != 2:
            sys.exit(f"{path}: format version {version}, expected 2")
        if header_size != HEADER.size:
            sys.exit(f"{path}: header_size {header_size} != {HEADER.size}")
        if event_size != 32:
            sys.exit(f"{path}: event_size {event_size} != 32")
        if (flags & HF_TICKS) and tick_hz <= 0:
            sys.exit(f"{path}: timestamps are raw ticks but no tick rate was "
                     f"carried over the wire — the handshake lost it")
    print(f"{len(files)} stream segment(s) carry a readable clock")


if __name__ == "__main__":
    main(sys.argv)
