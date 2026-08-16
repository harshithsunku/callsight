"""TCP server for callsight remote tracing.

Receives ZSTD-compressed event streams from on-device trace_stream clients
(see runtime/trace_shm.h for the wire protocol), decompresses them, and
writes standard trace.<...>.bin files that `callsight analyze` and the web
UI consume unchanged.

The handshake carries the device's clock calibration and PIE load bias,
which are written into every file header: raw cycle counts and nanoseconds
are indistinguishable once they reach the wire, so the server must be told
rather than guess.

Output is bounded like the on-device capture is. A device that streams for
an hour would otherwise fill the analysis host, which is the same failure
this project refuses to have on the device.

Requires the optional 'stream' extra: uv tool install 'callsight[stream]'.

Usage: callsight serve [--host 0.0.0.0] [--port 9001] [--out traces/]
                       [--max-mb 4096] [--seg-mb 256]
"""

import socket
import struct
import sys
import threading
from pathlib import Path

from callsight import analyze

MAGIC = b"TKSTREAM"
VERSION = 2

CHUNK_EVENTS = 0
CHUNK_NOTICE = 1

# magic, version, event_size, flags, pad, load_bias, tick_hz, t0_ticks,
# t0_ns, hook_ns
_STREAM_HEADER = struct.Struct("<8sIIIIQQQQQ")
_CHUNK_HEADER = struct.Struct("<III")


def _recv_all(conn, n):
    buf = bytearray()
    while len(buf) < n:
        part = conn.recv(n - len(buf))
        if not part:
            return None
        buf.extend(part)
    return bytes(buf)


class _SegmentWriter:
    """Rotating writer for one connection.

    Segments are capped so a long-running stream cannot grow one unbounded
    file, and the connection's total is capped so it cannot fill the disk.
    """

    def __init__(self, outdir, stem, meta, seg_bytes, max_bytes):
        self.outdir = outdir
        self.stem = stem
        self.meta = meta
        self.seg_bytes = seg_bytes
        self.max_bytes = max_bytes
        self.total = 0
        self.written = 0
        self.seq = 0
        self.f = None
        self.stopped = False
        self._open()

    def _header(self):
        return analyze.HEADER.pack(
            analyze.MAGIC, analyze.VERSION, analyze.EVENT.size,
            analyze.HEADER.size, self.meta["flags"], self.meta["load_bias"],
            self.meta["tick_hz"], self.meta["t0_ticks"], self.meta["t0_ns"],
            self.meta["hook_ns"], self.meta["pid"], self.seq, 0)

    def _open(self):
        path = self.outdir / f"{self.stem}.{self.seq}.bin"
        self.f = open(path, "wb")
        self.f.write(self._header())
        self.written = analyze.HEADER.size
        self.total += analyze.HEADER.size

    def write(self, raw):
        if self.stopped:
            return False
        if self.max_bytes and self.total + len(raw) > self.max_bytes:
            self.stop(analyze.MARK_BUDGET, self.max_bytes)
            return False
        if self.seg_bytes and self.written + len(raw) > self.seg_bytes:
            self.f.close()
            self.seq += 1
            self._open()
        self.f.write(raw)
        self.written += len(raw)
        self.total += len(raw)
        return True

    def stop(self, code, payload):
        """Record why collection ended, in-band, the way the runtime does."""
        if self.stopped or self.f is None:
            return
        self.f.write(analyze.EVENT.pack(0, code, payload, 0, analyze.MARKER))
        self.stopped = True

    def close(self):
        if self.f is not None:
            self.f.close()
            self.f = None


def _handle(conn, addr, outdir, dctx, conn_id, seg_bytes, max_bytes):
    """One device connection -> one rotating set of trace files."""
    header = _recv_all(conn, _STREAM_HEADER.size)
    if header is None:
        return
    (magic, version, event_size, flags, _pad, load_bias, tick_hz, t0_ticks,
     t0_ns, hook_ns) = _STREAM_HEADER.unpack(header)
    if magic != MAGIC or version != VERSION:
        print(f"serve: {addr}: bad stream header (magic/version), dropped",
              file=sys.stderr)
        return
    if event_size != analyze.EVENT.size:
        print(f"serve: {addr}: event size {event_size} != "
              f"{analyze.EVENT.size}, dropped", file=sys.stderr)
        return

    meta = {"flags": flags, "load_bias": load_bias, "tick_hz": tick_hz,
            "t0_ticks": t0_ticks, "t0_ns": t0_ns, "hook_ns": hook_ns,
            "pid": conn_id}
    writer = _SegmentWriter(outdir, f"trace.stream.{conn_id}", meta,
                            seg_bytes, max_bytes)
    events = 0
    try:
        while True:
            hdr = _recv_all(conn, _CHUNK_HEADER.size)
            if hdr is None:
                break
            ctype, raw_len, zstd_len = _CHUNK_HEADER.unpack(hdr)
            payload = _recv_all(conn, zstd_len)
            if payload is None:
                break
            try:
                raw = dctx.decompress(payload, max_output_size=raw_len)
            except Exception as e:  # corrupt chunk: report, keep serving
                print(f"serve: {addr}: undecodable chunk: {e}", file=sys.stderr)
                continue
            if ctype == CHUNK_EVENTS:
                if writer.write(raw):
                    events += raw_len // event_size
                else:
                    print(f"serve: {addr}: output budget reached, "
                          f"discarding the rest of this stream",
                          file=sys.stderr)
            elif ctype == CHUNK_NOTICE:
                dropped = struct.unpack("<Q", raw[:8])[0]
                print(f"serve: {addr}: device dropped {dropped} events "
                      f"(ring full — client too slow or ring too small)")
    finally:
        writer.close()
    print(f"serve: {addr} -> {writer.stem}.*.bin ({events} events, "
          f"{writer.seq + 1} segment(s))")


def serve(host, port, outdir, max_mb=4096, seg_mb=256):
    import zstandard  # optional dep; presence checked by the CLI

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dctx = zstandard.ZstdDecompressor()
    seg_bytes = seg_mb * 1024 * 1024
    max_bytes = max_mb * 1024 * 1024

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(8)
    print(f"callsight serve: listening on {host}:{port}, "
          f"writing {outdir}/trace.stream.*.bin "
          f"(up to {max_mb} MB per connection)")
    conn_id = 0
    try:
        while True:
            conn, addr = sock.accept()
            conn_id += 1
            threading.Thread(target=_handle,
                             args=(conn, addr, outdir, dctx, conn_id,
                                   seg_bytes, max_bytes),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\ncallsight serve: stopped")
    finally:
        sock.close()
