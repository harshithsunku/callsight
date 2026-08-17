"""Big-endian agents: a trace written by a device whose byte order differs
from the analysis host's must analyze to exactly the same answer.

The agent writes its native order and records which one that is (see the
byte-order note in runtime/trace.h); the host detects and swaps. These tests
build each on-disk and on-wire structure in BOTH orders from identical
logical content and assert the reports match, so the swapping is proven
rather than assumed — and they are pure Python, so they run everywhere, not
only where a cross toolchain and qemu happen to be installed.

The failure this guards against is not a crash. Read little-endian, a
big-endian trace yields plausible-looking garbage: addresses that resolve to
nothing and timestamps in the wrong millennium.
"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import analyze, serve


def pack_header(lay, version=analyze.VERSION, magic=analyze.MAGIC,
                event_size=None, flags=0, load_bias=0, tick_hz=0, t0_ticks=0,
                t0_ns=0, hook_ns=0, pid=0, seq=0):
    """A trace file header in the byte order of `lay`."""
    size = lay.event.size if event_size is None else event_size
    if version == 1:
        return lay.header_v1.pack(magic, 1, size)
    return lay.header.pack(magic, version, size, lay.header.size, flags,
                           load_bias, tick_hz, t0_ticks, t0_ns, hook_ns,
                           pid, seq, 0)


def pack_events(lay, events, **header):
    out = pack_header(lay, **header)
    for tid, kind, func, ts, caller in events:
        out += lay.event.pack(ts, func, caller, tid, kind)
    return out


def pack_summary(lay, records, flags=0, load_bias=0, tick_hz=0, hook_ns=0,
                 pid=1, tid=1, span=0, truncated=0):
    out = lay.sum_header.pack(analyze.SUM_MAGIC, 1, lay.sum_record.size,
                              lay.sum_header.size, flags, load_bias, tick_hz,
                              0, 0, hook_ns, pid, tid, len(records), span,
                              truncated)
    for r in records:
        hist = r.get("hist") or [0] * analyze.HIST_BUCKETS
        out += lay.sum_record.pack(r["func"], r["calls"], r["incl"],
                                   r["self"], r["min"], r["max"], *hist)
    return out


# One nested call pair per thread, with a caller address, so the comparison
# covers every field width in the record: u64 timestamps and addresses, the
# u32 tid and the u8 kind.
EVENTS = [
    (7, analyze.ENTER, 0x401B40, 1000, 0x401AA0),
    (7, analyze.ENTER, 0x401C90, 1200, 0x401B60),
    (7, analyze.EXIT, 0x401C90, 5200, 0x401B60),
    (7, analyze.EXIT, 0x401B40, 9000, 0x401AA0),
]

SUM_RECORDS = [
    {"func": 0x401B40, "calls": 200, "incl": 800000, "self": 300000,
     "min": 3000, "max": 9000},
    {"func": 0x401C90, "calls": 200, "incl": 500000, "self": 500000,
     "min": 2000, "max": 4000},
]


def _resolver(addrs, exe, cmd=None):
    """Stand in for addr2line: these addresses belong to no real binary."""
    return {a: (f"fn_{a:x}", f"src.c:{a & 0xFF}") for a in addrs}


class EndianFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self._real_resolve = analyze.resolve
        analyze.resolve = _resolver
        self.addCleanup(setattr, analyze, "resolve", self._real_resolve)

    def collect_bytes(self, name, blob):
        d = self.dir / name
        d.mkdir()
        (d / ("trace.summary.1.1.bin" if b"MLSUMRY" in blob[:8]
              else "trace.1.1.0.bin")).write_bytes(blob)
        return analyze.collect(d, "/nonexistent/exe")

    def assertSameReport(self, little, big):
        """Everything a user reads must match; only the notice differs."""
        for key in ("events", "threads", "functions", "span_ms",
                    "unmatched_exits", "unclosed_enters", "mode"):
            self.assertEqual(little[key], big[key], f"{key} differs")
        self.assertEqual(sorted(little["rows"], key=lambda r: r["function"]),
                         sorted(big["rows"], key=lambda r: r["function"]))


class TestEventFiles(EndianFixture):
    def test_v2_trace_reads_the_same_either_way(self):
        le = self.collect_bytes("le", pack_events(analyze.LE, EVENTS, pid=42))
        be = self.collect_bytes(
            "be", pack_events(analyze.BE, EVENTS, pid=42,
                              flags=analyze.HF_BIGENDIAN))
        self.assertSameReport(le, be)
        # 4 ms outer, 4 ms inner: real numbers, not just equal garbage.
        rows = {r["function"]: r for r in be["rows"]}
        self.assertAlmostEqual(rows["fn_401b40"]["incl_ms"], 0.008)
        self.assertAlmostEqual(rows["fn_401c90"]["incl_ms"], 0.004)

    def test_version_1_trace_from_a_big_endian_agent(self):
        """v1 has no flags field at all, so the version peek is the only
        thing that can tell — which is exactly why detection uses it."""
        le = self.collect_bytes("le", pack_events(analyze.LE, EVENTS,
                                                  version=1))
        be = self.collect_bytes("be", pack_events(analyze.BE, EVENTS,
                                                  version=1))
        self.assertSameReport(le, be)

    def test_big_endian_capture_says_so(self):
        be = self.collect_bytes(
            "be", pack_events(analyze.BE, EVENTS,
                              flags=analyze.HF_BIGENDIAN))
        self.assertTrue(any("big-endian" in n for n in be["notices"]),
                        be["notices"])

    def test_load_bias_and_tick_clock_survive_the_swap(self):
        """The fields most likely to be silently wrong: a byte-swapped bias
        or tick rate produces a report that looks fine and is nonsense."""
        kw = dict(flags=analyze.HF_TICKS, load_bias=0x400000,
                  tick_hz=1000000000, t0_ticks=0, t0_ns=0)
        le = self.collect_bytes("le", pack_events(analyze.LE, EVENTS, **kw))
        kw["flags"] |= analyze.HF_BIGENDIAN
        be = self.collect_bytes("be", pack_events(analyze.BE, EVENTS, **kw))
        self.assertSameReport(le, be)
        self.assertIn("fn_1b40", [r["function"] for r in be["rows"]])


class TestSummaryFiles(EndianFixture):
    def test_summary_reads_the_same_either_way(self):
        le = self.collect_bytes("le", pack_summary(analyze.LE, SUM_RECORDS))
        be = self.collect_bytes(
            "be", pack_summary(analyze.BE, SUM_RECORDS,
                               flags=analyze.HF_BIGENDIAN))
        self.assertSameReport(le, be)
        self.assertEqual(sum(r["calls"] for r in be["rows"]), 400)

    def test_histogram_u32_array_survives_the_swap(self):
        """160 u32s per record — the one place the record is not all u64,
        and percentiles come straight out of it."""
        hist = [0] * analyze.HIST_BUCKETS
        hist[analyze.hist_bucket(3000)] = 200
        recs = [dict(SUM_RECORDS[0], hist=hist)]
        le = self.collect_bytes("le", pack_summary(analyze.LE, recs))
        be = self.collect_bytes("be", pack_summary(analyze.BE, recs))
        self.assertSameReport(le, be)
        self.assertEqual(be["rows"][0]["p50_ns"], le["rows"][0]["p50_ns"])
        self.assertGreater(be["rows"][0]["p50_ns"], 0)


class TestStreamWire(unittest.TestCase):
    """The handshake decides the byte order of the files the server writes.

    Getting this wrong does not fail loudly: it produces a little-endian
    header in front of big-endian events, a file that is internally
    inconsistent and that no reader can be right about.
    """

    HANDSHAKE = dict(load_bias=0x1000, tick_hz=19200000, t0_ticks=99,
                     t0_ns=12345, hook_ns=12)

    def handshake(self, wire, flags=0):
        return wire.stream_header.pack(
            serve.MAGIC, serve.VERSION, analyze.EVENT.size, flags, 0,
            self.HANDSHAKE["load_bias"], self.HANDSHAKE["tick_hz"],
            self.HANDSHAKE["t0_ticks"], self.HANDSHAKE["t0_ns"],
            self.HANDSHAKE["hook_ns"])

    def test_byte_order_detected_from_the_handshake(self):
        self.assertEqual(analyze.byte_order(self.handshake(serve._WIRE_LE)),
                         "<")
        self.assertEqual(analyze.byte_order(self.handshake(serve._WIRE_BE)),
                         ">")

    def test_relayed_file_header_matches_the_payload_order(self):
        meta = dict(self.HANDSHAKE, flags=analyze.HF_BIGENDIAN, pid=3)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            w = serve._SegmentWriter(out, "trace.stream.3", meta,
                                     0, 0, serve._WIRE_BE)
            # Event bytes are relayed untouched, so they are big-endian too.
            w.write(analyze.BE.event.pack(1000, 0x401B40, 0, 7,
                                          analyze.ENTER))
            w.write(analyze.BE.event.pack(2000, 0x401B40, 0, 7,
                                          analyze.EXIT))
            w.close()
            path = out / "trace.stream.3.0.bin"

            self.assertEqual(analyze.byte_order(path.read_bytes()), ">",
                             "header must be written in the device's order")
            meta_read = analyze.read_header(path)
            self.assertTrue(meta_read["big_endian"])
            self.assertEqual(meta_read["load_bias"],
                             self.HANDSHAKE["load_bias"])
            self.assertEqual(meta_read["tick_hz"], self.HANDSHAKE["tick_hz"])
            # And the relayed events come back out intact.
            evs = list(analyze.read_events(path, meta_read))
            self.assertEqual([e[1] for e in evs],
                             [analyze.ENTER, analyze.EXIT])
            self.assertEqual({e[2] for e in evs}, {0x401B40 - 0x1000})

    def test_notice_chunk_word_follows_the_wire_order(self):
        dropped = 0x0102030405060708
        self.assertEqual(
            serve._WIRE_BE.u64.unpack(struct.pack(">Q", dropped))[0], dropped)
        self.assertEqual(
            serve._WIRE_LE.u64.unpack(struct.pack("<Q", dropped))[0], dropped)


if __name__ == "__main__":
    unittest.main()
