"""TCP server for callsight remote tracing.

Receives ZSTD-compressed event streams from on-device trace_stream clients
(see runtime/trace_shm.h for the wire protocol), decompresses them, and
writes standard trace.<...>.bin files that `callsight analyze` and the web
UI consume unchanged.

Requires the optional 'stream' extra: uv tool install 'callsight[stream]'.

Usage: callsight serve [--host 0.0.0.0] [--port 9001] [--out traces/]
"""

import socket
import struct
import sys
import threading
from pathlib import Path

from callsight import analyze

MAGIC = b"TKSTREAM"
VERSION = 1

CHUNK_EVENTS = 0
CHUNK_NOTICE = 1

_STREAM_HEADER = struct.Struct("<8sII")
_CHUNK_HEADER = struct.Struct("<III")


def _recv_all(conn, n):
    buf = bytearray()
    while len(buf) < n:
        part = conn.recv(n - len(buf))
        if not part:
            return None
        buf.extend(part)
    return bytes(buf)


def _handle(conn, addr, outdir, dctx, conn_id):
    """One device connection -> one trace.stream.<id>.bin file."""
    header = _recv_all(conn, _STREAM_HEADER.size)
    if header is None:
        return
    magic, version, event_size = _STREAM_HEADER.unpack(header)
    if magic != MAGIC or version != VERSION:
        print(f"serve: {addr}: bad stream header, dropped", file=sys.stderr)
        return
    if event_size != analyze.EVENT.size:
        print(f"serve: {addr}: event size {event_size} != "
              f"{analyze.EVENT.size}, dropped", file=sys.stderr)
        return

    out = outdir / f"trace.stream.{conn_id}.bin"
    events = 0
    with open(out, "wb") as f:
        f.write(analyze.HEADER.pack(analyze.MAGIC, 1, event_size))
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
                f.write(raw)
                events += raw_len // event_size
            elif ctype == CHUNK_NOTICE:
                dropped = struct.unpack("<Q", raw[:8])[0]
                print(f"serve: {addr}: device dropped {dropped} events "
                      f"(ring full — client too slow or ring too small)")
    print(f"serve: {addr} -> {out} ({events} events)")


def serve(host, port, outdir):
    import zstandard  # optional dep; presence checked by the CLI

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    dctx = zstandard.ZstdDecompressor()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(8)
    print(f"callsight serve: listening on {host}:{port}, "
          f"writing {outdir}/trace.stream.*.bin")
    conn_id = 0
    try:
        while True:
            conn, addr = sock.accept()
            conn_id += 1
            threading.Thread(target=_handle,
                             args=(conn, addr, outdir, dctx, conn_id),
                             daemon=True).start()
    except KeyboardInterrupt:
        print("\ncallsight serve: stopped")
    finally:
        sock.close()
