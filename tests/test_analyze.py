"""Unit tests for callsight.analyze: trace file parsing (both format
versions), enter/exit matching, clock conversion, capture markers, latency
histograms, summary traces, symbol resolution and the report formats."""

import io
import json
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import analyze


def pack_header(version=analyze.VERSION, magic=analyze.MAGIC, event_size=None,
                flags=0, load_bias=0, tick_hz=0, t0_ticks=0, t0_ns=0,
                hook_ns=0, pid=0, seq=0):
    """Build a trace file header of either format version."""
    size = analyze.EVENT.size if event_size is None else event_size
    if version == 1:
        return analyze.HEADER_V1.pack(magic, 1, size)
    return analyze.HEADER.pack(magic, version, size, analyze.HEADER.size,
                               flags, load_bias, tick_hz, t0_ticks, t0_ns,
                               hook_ns, pid, seq, 0)


def pack_events(events, trailing=b"", **header):
    """Serialize [(tid, kind, func, ts[, caller])] into trace-file bytes."""
    out = pack_header(**header)
    for ev in events:
        tid, kind, func, ts = ev[0], ev[1], ev[2], ev[3]
        caller = ev[4] if len(ev) > 4 else 0
        out += analyze.EVENT.pack(ts, func, caller, tid, kind)
    return out + trailing


def pack_summary(records, magic=analyze.SUM_MAGIC, version=1, record_size=None,
                 flags=0, load_bias=0, tick_hz=0, hook_ns=0, pid=1, tid=1,
                 span=0, truncated=0):
    size = analyze.SUM_RECORD.size if record_size is None else record_size
    out = analyze.SUM_HEADER.pack(magic, version, size,
                                  analyze.SUM_HEADER.size, flags, load_bias,
                                  tick_hz, 0, 0, hook_ns, pid, tid,
                                  len(records), span, truncated)
    for r in records:
        hist = r.get("hist") or [0] * analyze.HIST_BUCKETS
        out += analyze.SUM_RECORD.pack(r["func"], r["calls"], r["incl"],
                                       r["self"], r["min"], r["max"], *hist)
    return out


def enter(tid, func, ts, caller=0):
    return (tid, analyze.ENTER, func, ts, caller)


def leave(tid, func, ts, caller=0):
    return (tid, analyze.EXIT, func, ts, caller)


def marker(code, payload=0, ts=0, tid=1):
    return (tid, analyze.MARKER, code, ts, payload)


class TraceDirFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write_trace(self, name, events, **kwargs):
        path = self.dir / name
        path.write_bytes(pack_events(events, **kwargs))
        return path

    def write_summary(self, name, records, **kwargs):
        path = self.dir / name
        path.write_bytes(pack_summary(records, **kwargs))
        return path


class TestReadEvents(TraceDirFixture):
    def test_roundtrip(self):
        evs = [enter(7, 0x400, 100), leave(7, 0x400, 300)]
        path = self.write_trace("trace.1.7.0.bin", evs)
        self.assertEqual(list(analyze.read_events(path)), evs)

    def test_streams_more_than_one_block(self):
        """The reader loops over fixed-size blocks; cross a block boundary."""
        n = analyze.READ_BLOCK_EVENTS + 5
        evs = [enter(1, 0x10, i) for i in range(n)]
        path = self.write_trace("trace.1.1.0.bin", evs)
        self.assertEqual(list(analyze.read_events(path)), evs)

    def test_bad_magic_skipped(self):
        path = self.write_trace("trace.1.1.0.bin", [enter(1, 0x10, 1)],
                                magic=b"NOPENOPE")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])
        self.assertIn("bad or missing header", err.getvalue())

    def test_unknown_version_skipped(self):
        path = self.write_trace("trace.1.1.0.bin", [enter(1, 0x10, 1)],
                                version=analyze.VERSION + 1)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])
        self.assertIn("newer", err.getvalue())

    def test_event_size_mismatch_skipped(self):
        path = self.write_trace("trace.1.1.0.bin", [enter(1, 0x10, 1)],
                                event_size=analyze.EVENT.size + 8)
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])
        self.assertIn("event size", err.getvalue())

    def test_empty_file_yields_nothing(self):
        path = self.dir / "trace.1.1.0.bin"
        path.write_bytes(b"")
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(list(analyze.read_events(path)), [])

    def test_truncated_final_record_is_tolerated(self):
        """A killed process can leave a partial event; keep the good ones."""
        evs = [enter(1, 0x10, 1), leave(1, 0x10, 2)]
        path = self.write_trace("trace.1.1.0.bin", evs, trailing=b"\x01\x02\x03")
        err = io.StringIO()
        with redirect_stderr(err):
            got = list(analyze.read_events(path))
        self.assertEqual(got, evs)
        self.assertIn("trailing bytes", err.getvalue())


class TestFormatVersions(TraceDirFixture):
    """Version 1 files predate the load bias, the clock anchors and markers.
    They still have to analyze — someone's traces outlive an upgrade."""

    def test_version_1_still_reads(self):
        evs = [enter(1, 0x10, 5), leave(1, 0x10, 9)]
        path = self.write_trace("trace.1.1.bin", evs, version=1)
        self.assertEqual(list(analyze.read_events(path)), evs)

    def test_version_1_header_is_16_bytes(self):
        self.assertEqual(analyze.HEADER_V1.size, 16)
        meta = analyze.read_header(
            self.write_trace("trace.1.1.bin", [], version=1))
        self.assertEqual(meta["header_size"], 16)

    def test_reader_skips_to_header_size(self):
        """Events start at header_size, not at a size this reader assumed —
        that is what lets a later version add fields without breaking us."""
        meta = analyze.read_header(
            self.write_trace("trace.1.1.0.bin", [enter(1, 0x10, 1)]))
        self.assertEqual(meta["header_size"], analyze.HEADER.size)
        self.assertEqual(analyze.HEADER.size, 80)

    def test_load_bias_is_subtracted(self):
        """A PIE records runtime addresses; the reader hands back link
        addresses so they can go straight to addr2line."""
        path = self.write_trace(
            "trace.1.1.0.bin",
            [enter(1, 0x7F0000 + 0x1140, 1, caller=0x7F0000 + 0x1200),
             leave(1, 0x7F0000 + 0x1140, 2)],
            load_bias=0x7F0000)
        got = list(analyze.read_events(path))
        self.assertEqual(got[0][2], 0x1140)
        self.assertEqual(got[0][4], 0x1200)

    def test_zero_caller_stays_zero(self):
        """Bias must not turn 'no call site' into a negative address."""
        path = self.write_trace("trace.1.1.0.bin", [enter(1, 0x2000, 1)],
                                load_bias=0x1000)
        self.assertEqual(list(analyze.read_events(path))[0][4], 0)


class TestClockConversion(TraceDirFixture):
    """Raw cycle counters are the fast path; the reader converts them to
    nanoseconds using the anchors the runtime recorded."""

    def ticks_file(self, events, hz, closing=None, t0_ticks=1000, t0_ns=5000):
        evs = list(events)
        if closing is not None:
            evs.append(marker(analyze.MARK_CLOCK, payload=closing[1],
                              ts=closing[0]))
        return self.write_trace("trace.1.1.0.bin", evs,
                                flags=analyze.HF_TICKS, tick_hz=hz,
                                t0_ticks=t0_ticks, t0_ns=t0_ns)

    def test_header_rate_used_without_a_closing_anchor(self):
        # 2 GHz: 1 tick = 0.5 ns. Enter 200 ticks after t0 -> 100 ns after.
        path = self.ticks_file([enter(1, 0xA, 1200), leave(1, 0xA, 1600)],
                               hz=2_000_000_000)
        got = list(analyze.read_events(path))
        self.assertEqual(got[0][3], 5000 + 100)
        self.assertEqual(got[1][3], 5000 + 300)

    def test_closing_anchor_overrides_the_header_rate(self):
        """The measured rate wins: it spans the whole run rather than a
        startup window, and the header value is only a fallback."""
        # Closing anchor says 1000 ticks elapsed in 1000 ns -> 1 ns/tick,
        # contradicting the (deliberately wrong) 4 GHz header hint.
        path = self.ticks_file([enter(1, 0xA, 1500), leave(1, 0xA, 1700)],
                               hz=4_000_000_000, closing=(2000, 6000))
        got = list(analyze.read_events(path))
        self.assertEqual(got[0][3], 5000 + 500)
        self.assertEqual(got[1][3], 5000 + 700)

    def test_durations_survive_conversion(self):
        path = self.ticks_file([enter(1, 0xA, 1000), leave(1, 0xA, 3000)],
                               hz=2_000_000_000)
        stats, _t, _u, _o = analyze.analyze(analyze.read_events(path))
        self.assertEqual(stats[0xA][1], 1000)  # 2000 ticks at 0.5 ns

    def test_segments_of_one_capture_share_one_rate(self):
        """Only the segment holding the closing anchor can measure the tick
        rate, and a thread's enter and exit routinely land in different
        segments. Converting those with two different rates produced
        negative durations, which is not a thing that can happen."""
        common = dict(flags=analyze.HF_TICKS, tick_hz=4_000_000_000,
                      t0_ticks=1000, t0_ns=5000)
        # Segment 0 has no closing anchor; segment 1 carries it and it says
        # 1 ns/tick, contradicting the 4 GHz header hint.
        self.write_trace("trace.1.1.0.bin", [enter(1, 0xA, 1500)], **common)
        self.write_trace("trace.1.1.1.bin",
                         [leave(1, 0xA, 1700),
                          marker(analyze.MARK_CLOCK, payload=6000, ts=2000)],
                         **common)
        metas = analyze._open_metas(analyze._event_files(self.dir))
        self.assertEqual(len(metas), 2)
        self.assertEqual(metas[0]["anchor"], metas[1]["anchor"])

        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        row = data["rows"][0]
        self.assertGreater(row["incl_ms"], 0)
        self.assertAlmostEqual(row["incl_ms"], 200 / 1e6)  # 200 ticks @ 1ns

    def test_uncalibrated_ticks_refuse_to_guess(self):
        path = self.write_trace("trace.1.1.0.bin", [enter(1, 0xA, 1)],
                                flags=analyze.HF_TICKS, tick_hz=0)
        with self.assertRaises(RuntimeError) as cm:
            list(analyze.read_events(path))
        self.assertIn("calibration", str(cm.exception))


class TestMarkers(TraceDirFixture):
    """Markers are in-band notes from the runtime. Counting one as an exit
    would corrupt the whole match, so they must never reach the matcher."""

    def test_marker_is_not_an_event(self):
        path = self.write_trace("trace.1.1.0.bin", [
            enter(1, 0xA, 0), marker(analyze.MARK_BUDGET, 512 * 1024 * 1024),
            leave(1, 0xA, 10)])
        got = list(analyze.read_events(path))
        self.assertEqual(len(got), 2)
        self.assertEqual([g[1] for g in got], [analyze.ENTER, analyze.EXIT])

    def test_notices_collected(self):
        notices = []
        path = self.write_trace("trace.1.1.0.bin", [
            enter(1, 0xA, 0), leave(1, 0xA, 1),
            marker(analyze.MARK_BUDGET, 512 * 1024 * 1024)])
        list(analyze.read_events(path, notices=notices))
        self.assertEqual(notices[0]["kind"], "budget")

    def test_clock_marker_is_not_a_notice(self):
        notices = []
        path = self.write_trace("trace.1.1.0.bin",
                                [marker(analyze.MARK_CLOCK, 123)])
        list(analyze.read_events(path, notices=notices))
        self.assertEqual(notices, [])

    def test_budget_notice_explains_the_gap(self):
        self.write_trace("trace.1.1.0.bin", [
            enter(1, 0xA, 0), leave(1, 0xA, 10),
            marker(analyze.MARK_BUDGET, 64 * 1024 * 1024)])
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        self.assertTrue(any("64 MB" in n for n in data["notices"]))

    def test_wrap_notice_explains_unmatched_exits(self):
        self.write_trace("trace.1.1.0.bin", [
            leave(1, 0xA, 10), marker(analyze.MARK_WRAP, 4096),
            enter(1, 0xA, 20), leave(1, 0xA, 30)])
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        self.assertEqual(data["unmatched_exits"], 1)
        self.assertTrue(any("wrap" in n and "expected" in n
                            for n in data["notices"]))

    def test_write_error_is_reported(self):
        self.write_trace("trace.1.1.0.bin", [
            enter(1, 0xA, 0), leave(1, 0xA, 1),
            marker(analyze.MARK_WRITE_ERR, 28)])
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        self.assertTrue(any("errno 28" in n for n in data["notices"]))


class TestHistogram(unittest.TestCase):
    def test_buckets_are_monotonic(self):
        last = -1
        for d in list(range(0, 64)) + [10 ** k for k in range(2, 10)]:
            b = analyze.hist_bucket(d)
            self.assertGreaterEqual(b, last)
            last = b

    def test_bucket_bounds_contain_their_values(self):
        """Every duration must fall inside the range its bucket reports —
        otherwise percentile estimates are quietly wrong."""
        for d in list(range(0, 64)) + [1000, 12345, 2 ** 20, 2 ** 30]:
            b = analyze.hist_bucket(d)
            low, high = analyze.bucket_bounds(b)
            self.assertLessEqual(low, d, f"d={d} bucket={b}")
            self.assertGreaterEqual(high, d, f"d={d} bucket={b}")

    def test_bucket_width_stays_bounded(self):
        for b in range(8, analyze.HIST_BUCKETS - 1):
            low, high = analyze.bucket_bounds(b)
            self.assertLessEqual((high - low) / low, 0.26)

    def test_percentile_picks_the_right_bucket(self):
        hist = [0] * analyze.HIST_BUCKETS
        hist[analyze.hist_bucket(100)] = 99
        hist[analyze.hist_bucket(10000)] = 1
        p50 = analyze.percentile(hist, 100, 0.50)
        p99 = analyze.percentile(hist, 100, 0.99)
        self.assertLess(abs(p50 - 100) / 100, 0.2)
        self.assertLess(abs(p99 - 100) / 100, 0.2)
        self.assertGreater(analyze.percentile(hist, 100, 1.0), 5000)

    def test_empty_histogram(self):
        self.assertEqual(analyze.percentile([0] * analyze.HIST_BUCKETS, 0, 0.5), 0)

    def test_negative_duration_does_not_escape_the_bucket_range(self):
        """One bad record should not take down the whole report."""
        for d in (-1, -1000, -(2 ** 40)):
            b = analyze.hist_bucket(d)
            self.assertGreaterEqual(b, 0)
            self.assertLess(b, analyze.HIST_BUCKETS)

    def test_accumulator_tracks_per_call_latency(self):
        acc = analyze.Accumulator()
        for i, dur in enumerate((10, 20, 30, 4000)):
            acc.feed(*enter(1, 0xA, i * 100000))
            acc.feed(*leave(1, 0xA, i * 100000 + dur))
        self.assertEqual(acc.min_t[0xA], 10)
        self.assertEqual(acc.max_t[0xA], 4000)
        self.assertLess(analyze.percentile(acc.hist[0xA], 4, 0.5), 100)


class TestAnalyze(unittest.TestCase):
    def test_single_call(self):
        stats, threads, unmatched, open_frames = analyze.analyze(
            [enter(1, 0xA, 100), leave(1, 0xA, 400)])
        self.assertEqual(stats[0xA], (1, 300, 300, 300))
        self.assertEqual(threads[1], (100, 400))
        self.assertEqual((unmatched, open_frames), (0, 0))

    def test_self_time_excludes_child(self):
        """outer 0..1000 with inner 200..900: self(outer) = 1000 - 700."""
        stats, _t, _u, _o = analyze.analyze([
            enter(1, 0xA, 0), enter(1, 0xB, 200),
            leave(1, 0xB, 900), leave(1, 0xA, 1000)])
        self.assertEqual(stats[0xA], (1, 1000, 300, 1000))
        self.assertEqual(stats[0xB], (1, 700, 700, 700))

    def test_max_time_is_the_slowest_call(self):
        stats, _t, _u, _o = analyze.analyze([
            enter(1, 0xA, 0), leave(1, 0xA, 10),
            enter(1, 0xA, 20), leave(1, 0xA, 120),
            enter(1, 0xA, 200), leave(1, 0xA, 205)])
        calls, incl, self_t, max_t = stats[0xA]
        self.assertEqual((calls, incl, self_t, max_t), (3, 115, 115, 100))

    def test_recursion(self):
        """Nested same-function frames: inclusive double-counts, self doesn't."""
        stats, _t, _u, _o = analyze.analyze([
            enter(1, 0xA, 0), enter(1, 0xA, 100),
            leave(1, 0xA, 400), leave(1, 0xA, 500)])
        calls, incl, self_t, _max = stats[0xA]
        self.assertEqual(calls, 2)
        self.assertEqual(incl, 300 + 500)
        self.assertEqual(self_t, 500)  # 300 inner + (500 - 300) outer

    def test_threads_are_independent(self):
        stats, threads, unmatched, _o = analyze.analyze([
            enter(1, 0xA, 0), enter(2, 0xA, 10),
            leave(1, 0xA, 100), leave(2, 0xA, 300)])
        self.assertEqual(stats[0xA], (2, 100 + 290, 390, 290))
        self.assertEqual(sorted(threads), [1, 2])
        self.assertEqual(unmatched, 0)

    def test_unmatched_exit_counted(self):
        _s, _t, unmatched, _o = analyze.analyze([leave(1, 0xA, 100)])
        self.assertEqual(unmatched, 1)

    def test_unclosed_enter_counted(self):
        _s, _t, _u, open_frames = analyze.analyze(
            [enter(1, 0xA, 0), enter(1, 0xB, 10), leave(1, 0xB, 20)])
        self.assertEqual(open_frames, 1)

    def test_dangling_frames_closed_on_outer_exit(self):
        """An exit for an outer frame discards frames left open above it."""
        stats, _t, unmatched, open_frames = analyze.analyze([
            enter(1, 0xA, 0), enter(1, 0xB, 10), leave(1, 0xA, 100)])
        self.assertIn(0xA, stats)
        self.assertNotIn(0xB, stats)
        self.assertEqual((unmatched, open_frames), (0, 0))


class TestFolded(unittest.TestCase):
    def feed(self, events):
        acc = analyze.Accumulator(folded=True)
        for ev in events:
            acc.feed(*ev)
        return acc

    def test_paths_carry_self_time(self):
        acc = self.feed([enter(1, 0xA, 0), enter(1, 0xB, 200),
                         leave(1, 0xB, 900), leave(1, 0xA, 1000)])
        self.assertEqual(acc.folded[(0xA,)], 300)
        self.assertEqual(acc.folded[(0xA, 0xB)], 700)

    def test_same_callee_under_two_callers_stays_split(self):
        acc = self.feed([enter(1, 0xA, 0), enter(1, 0xC, 0), leave(1, 0xC, 5),
                         leave(1, 0xA, 5),
                         enter(1, 0xB, 10), enter(1, 0xC, 10),
                         leave(1, 0xC, 40), leave(1, 0xB, 40)])
        self.assertEqual(acc.folded[(0xA, 0xC)], 5)
        self.assertEqual(acc.folded[(0xB, 0xC)], 30)

    def test_total_matches_sum_of_self_time(self):
        events = [enter(1, 0xA, 0), enter(1, 0xB, 200),
                  leave(1, 0xB, 900), leave(1, 0xA, 1000)]
        acc = self.feed(events)
        stats, _t, _u, _o = analyze.analyze(events)
        self.assertEqual(sum(acc.folded.values()),
                         sum(self_t for _c, _i, self_t, _m in stats.values()))

    def test_disabled_by_default(self):
        acc = analyze.Accumulator()
        acc.feed(*enter(1, 0xA, 0))
        acc.feed(*leave(1, 0xA, 1))
        self.assertIsNone(acc.folded)


class TestCallSites(TraceDirFixture):
    def test_caller_comes_from_the_stack_not_the_return_address(self):
        """GCC hooks travel with an inlined body, so a return address can
        land in whichever function absorbed the code. The shadow stack knows
        who actually called."""
        self.write_trace("trace.1.1.0.bin", [
            enter(1, 0xA, 0, caller=0x900),
            enter(1, 0xB, 10, caller=0x910),
            leave(1, 0xB, 20), leave(1, 0xA, 30)])
        names = {0xA: ("outer", "/a.c:1"), 0xB: ("inner", "/b.c:2"),
                 0x900: ("libc", "??:0"), 0x910: ("elsewhere", "/a.c:7")}
        with mock.patch.object(analyze, "resolve", return_value=names):
            data = analyze.collect(self.dir, "app", callers=True)
        inner = next(r for r in data["call_sites"] if r["function"] == "inner")
        self.assertEqual(inner["caller"], "outer")
        self.assertEqual(inner["call_site"], "/a.c:7")

    def test_root_frame_has_no_caller(self):
        self.write_trace("trace.1.1.0.bin",
                         [enter(1, 0xA, 0, caller=0x900), leave(1, 0xA, 5)])
        names = {0xA: ("main", "/a.c:1"), 0x900: ("??", "??:0")}
        with mock.patch.object(analyze, "resolve", return_value=names):
            with redirect_stderr(io.StringIO()):  # half the addrs are libc
                data = analyze.collect(self.dir, "app", callers=True)
        self.assertEqual(data["call_sites"][0]["caller"], "(entry)")


class TestOverheadCompensation(TraceDirFixture):
    def test_measured_hook_cost_is_deducted(self):
        # 3 calls of the leaf inside one outer call, hook cost 10 ns.
        events = [enter(1, 0xA, 0)]
        t = 10
        for _ in range(3):
            events += [enter(1, 0xB, t), leave(1, 0xB, t + 100)]
            t += 200
        events.append(leave(1, 0xA, 1000))
        self.write_trace("trace.1.1.0.bin", events, hook_ns=10)
        names = {0xA: ("outer", "/a.c:1"), 0xB: ("leaf", "/b.c:1")}
        with mock.patch.object(analyze, "resolve", return_value=names):
            raw = analyze.collect(self.dir, "app")
            adj = analyze.collect(self.dir, "app", subtract_overhead=True)
        raw_leaf = next(r for r in raw["rows"] if r["function"] == "leaf")
        adj_leaf = next(r for r in adj["rows"] if r["function"] == "leaf")
        self.assertLess(adj_leaf["self_ms"], raw_leaf["self_ms"])
        self.assertTrue(adj["overhead_subtracted"])
        self.assertFalse(raw["overhead_subtracted"])

    def test_never_goes_negative(self):
        self.write_trace("trace.1.1.0.bin",
                         [enter(1, 0xA, 0), leave(1, 0xA, 1)], hook_ns=10_000)
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app", subtract_overhead=True)
        self.assertGreaterEqual(data["rows"][0]["self_ms"], 0)


class TestSummaryTraces(TraceDirFixture):
    def record(self, func, calls, incl, self_t, lo, hi, durations=()):
        hist = [0] * analyze.HIST_BUCKETS
        for d in durations:
            hist[analyze.hist_bucket(d)] += 1
        return {"func": func, "calls": calls, "incl": incl, "self": self_t,
                "min": lo, "max": hi, "hist": hist}

    def test_reads_records(self):
        path = self.write_summary("trace.summary.1.1.bin",
                                  [self.record(0xA, 5, 500, 400, 50, 200)])
        meta, records = analyze.read_summary(path)
        self.assertEqual(meta["tid"], 1)
        self.assertEqual(records[0]["calls"], 5)
        self.assertEqual(records[0]["max"], 200)

    def test_bad_magic_skipped(self):
        path = self.write_summary("trace.summary.1.1.bin", [], magic=b"XXXXXXXX")
        err = io.StringIO()
        with redirect_stderr(err):
            meta, records = analyze.read_summary(path)
        self.assertIsNone(meta)
        self.assertIn("bad or missing summary header", err.getvalue())

    def test_future_version_skipped(self):
        path = self.write_summary("trace.summary.1.1.bin", [], version=99)
        err = io.StringIO()
        with redirect_stderr(err):
            meta, _r = analyze.read_summary(path)
        self.assertIsNone(meta)
        self.assertIn("newer than this callsight understands",
                      err.getvalue())

    def test_record_size_mismatch_skipped(self):
        """A known version whose records are not the size that version
        declares: the file was written by something that disagrees with us
        about the layout, and reading it would produce garbage."""
        path = self.write_summary("trace.summary.1.1.bin", [], version=1,
                                  record_size=analyze.SUM_RECORD.size + 8)
        err = io.StringIO()
        with redirect_stderr(err):
            meta, _r = analyze.read_summary(path)
        self.assertIsNone(meta)
        self.assertIn("unsupported summary layout", err.getvalue())

    def test_threads_are_merged(self):
        self.write_summary("trace.summary.1.1.bin",
                           [self.record(0xA, 3, 300, 300, 100, 100)], tid=1)
        self.write_summary("trace.summary.1.2.bin",
                           [self.record(0xA, 2, 400, 400, 50, 500)], tid=2)
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        self.assertEqual(data["mode"], "summary")
        self.assertEqual(data["threads"], 2)
        row = data["rows"][0]
        self.assertEqual(row["calls"], 5)
        self.assertAlmostEqual(row["incl_ms"], 700 / 1e6)
        self.assertEqual(row["max_ns"], 500)

    def test_ticks_are_scaled(self):
        """Summary files can hold raw ticks; totals must come back in ns."""
        self.write_summary("trace.summary.1.1.bin",
                           [self.record(0xA, 1, 2000, 2000, 2000, 2000)],
                           flags=analyze.HF_TICKS, tick_hz=2_000_000_000)
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        self.assertAlmostEqual(data["rows"][0]["incl_ms"], 1000 / 1e6)

    def test_percentiles_come_from_the_histogram(self):
        """A fat tail must show up in p99 while leaving p50 alone — the
        whole point of keeping a histogram rather than a mean."""
        durs = [100] * 90 + [50000] * 10
        self.write_summary("trace.summary.1.1.bin",
                           [self.record(0xA, 100, 509000, 509000, 100, 50000,
                                        durations=durs)])
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        row = data["rows"][0]
        self.assertLess(abs(row["p50_ns"] - 100) / 100, 0.2)
        self.assertLess(abs(row["p99_ns"] - 50000) / 50000, 0.2)

    def test_truncation_is_reported(self):
        self.write_summary("trace.summary.1.1.bin",
                           [self.record(0xA, 1, 10, 10, 10, 10)], truncated=7)
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("f", "/a.c:1")}):
            data = analyze.collect(self.dir, "app")
        self.assertTrue(any("7 calls" in n for n in data["notices"]))

    def test_call_paths_are_refused(self):
        """Summary traces hold totals, not stacks: say so instead of
        producing an empty flame graph."""
        self.write_summary("trace.summary.1.1.bin",
                           [self.record(0xA, 1, 10, 10, 10, 10)])
        with self.assertRaises(RuntimeError) as cm:
            analyze.collect(self.dir, "app", folded=True)
        self.assertIn("summary", str(cm.exception))


class TestResolve(unittest.TestCase):
    def fake_run(self, output, **kwargs):
        proc = mock.Mock(stdout=output, stderr="", returncode=0)
        return mock.patch("subprocess.run", return_value=proc, **kwargs)

    def test_parses_function_and_location(self):
        with self.fake_run("main\n/src/main.c:10\nhelper\n/src/h.c:3\n"):
            names = analyze.resolve({0x20, 0x10}, "app")
        self.assertEqual(names[0x10], ("main", "/src/main.c:10"))
        self.assertEqual(names[0x20], ("helper", "/src/h.c:3"))

    def test_short_output_falls_back_to_placeholders(self):
        with self.fake_run("main\n"):
            names = analyze.resolve({0x10, 0x20}, "app")
        self.assertEqual(names[0x20], ("??", "??:0"))

    def test_empty_address_set_runs_nothing(self):
        with mock.patch("subprocess.run") as run:
            self.assertEqual(analyze.resolve(set(), "app"), {})
        run.assert_not_called()

    def test_batches_large_address_sets(self):
        """Addresses are chunked so the command line cannot hit ARG_MAX."""
        n = analyze.ADDR2LINE_BATCH * 2 + 1
        addrs = set(range(0x1000, 0x1000 + n))
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            hexes = [c for c in cmd if c.startswith("0x")]
            return mock.Mock(returncode=0, stderr="",
                             stdout="".join(f"fn\n/f.c:1\n" for _ in hexes))

        with mock.patch("subprocess.run", side_effect=run):
            names = analyze.resolve(addrs, "app")
        self.assertEqual(len(names), n)
        self.assertEqual(len(calls), 3)
        for cmd in calls:
            self.assertLessEqual(len([c for c in cmd if c.startswith("0x")]),
                                 analyze.ADDR2LINE_BATCH)

    def test_missing_addr2line_explains_itself(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(RuntimeError) as cm:
                analyze.resolve({0x10}, "app")
        self.assertIn("binutils", str(cm.exception))

    def test_addr2line_failure_reports_exe(self):
        import subprocess
        err = subprocess.CalledProcessError(1, "addr2line", stderr="no such file")
        with mock.patch("subprocess.run", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                analyze.resolve({0x10}, "app")
        self.assertIn("app", str(cm.exception))
        self.assertIn("no such file", str(cm.exception))

    def test_custom_addr2line_is_used(self):
        """Cross-compiled targets need their toolchain's copy: the host one
        cannot read a foreign ELF."""
        seen = []

        def run(cmd, **kwargs):
            seen.append(cmd[0])
            return mock.Mock(returncode=0, stderr="", stdout="fn\n/f.c:1\n")

        with mock.patch("subprocess.run", side_effect=run):
            analyze.resolve({0x10}, "app", cmd="arm-none-eabi-addr2line")
        self.assertEqual(seen, ["arm-none-eabi-addr2line"])

    def test_environment_selects_addr2line(self):
        with mock.patch.dict("os.environ",
                             {"CALLSIGHT_ADDR2LINE": "mips-addr2line"}):
            self.assertEqual(analyze.addr2line_cmd(), "mips-addr2line")

    def test_explicit_beats_environment(self):
        with mock.patch.dict("os.environ",
                             {"CALLSIGHT_ADDR2LINE": "mips-addr2line"}):
            self.assertEqual(analyze.addr2line_cmd("x"), "x")


class TestCollect(TraceDirFixture):
    """collect() over several files, with addr2line stubbed out."""

    def collect(self, **kwargs):
        names = {0xA: ("outer", "/src/a.c:1"), 0xB: ("inner", "/src/b.c:2")}
        with mock.patch.object(analyze, "resolve", return_value=names):
            return analyze.collect(self.dir, "app", **kwargs)

    def test_no_trace_files(self):
        with self.assertRaises(RuntimeError) as cm:
            analyze.collect(self.dir, "app")
        self.assertIn("no trace files", str(cm.exception))

    def test_header_only_files(self):
        self.write_trace("trace.1.1.0.bin", [])
        with self.assertRaises(RuntimeError) as cm:
            analyze.collect(self.dir, "app")
        self.assertIn("no events", str(cm.exception))

    def test_merges_files_and_threads(self):
        self.write_trace("trace.1.1.0.bin",
                         [enter(1, 0xA, 0), enter(1, 0xB, 100),
                          leave(1, 0xB, 300), leave(1, 0xA, 400)])
        self.write_trace("trace.1.2.0.bin",
                         [enter(2, 0xA, 50), leave(2, 0xA, 250)])
        data = self.collect()
        self.assertEqual(data["events"], 6)
        self.assertEqual(data["threads"], 2)
        self.assertEqual(data["functions"], 2)
        self.assertEqual(data["unmatched_exits"], 0)
        self.assertEqual(data["unclosed_enters"], 0)
        self.assertAlmostEqual(data["span_ms"], 400 / 1e6)
        outer = next(r for r in data["rows"] if r["function"] == "outer")
        self.assertEqual(outer["calls"], 2)
        self.assertAlmostEqual(outer["incl_ms"], 600 / 1e6)
        self.assertEqual([t["tid"] for t in data["per_thread"]], [1, 2])
        self.assertEqual([t["events"] for t in data["per_thread"]], [4, 2])

    def test_summary_files_do_not_look_like_event_files(self):
        """Both live in traces/ and both start with 'trace.'; picking the
        wrong reader would misparse one as the other."""
        self.write_trace("trace.1.1.0.bin",
                         [enter(1, 0xA, 0), leave(1, 0xA, 9)])
        self.assertEqual(len(analyze._event_files(self.dir)), 1)
        self.write_summary("trace.summary.1.1.bin", [])
        self.assertEqual(len(analyze._event_files(self.dir)), 1)
        self.assertEqual(len(analyze._summary_files(self.dir)), 1)

    def test_one_thread_split_across_files_still_matches(self):
        """Streaming mode can split a thread over several files; the pairs
        must still be matched, not counted as unmatched exits."""
        self.write_trace("trace.stream.1.bin", [enter(1, 0xA, 0)])
        self.write_trace("trace.stream.2.bin", [leave(1, 0xA, 900)])
        data = self.collect()
        self.assertEqual(data["unmatched_exits"], 0)
        self.assertEqual(data["unclosed_enters"], 0)
        self.assertAlmostEqual(data["rows"][0]["incl_ms"], 900 / 1e6)

    def test_folded_rows_only_when_requested(self):
        # outer 0..400 with inner 100..200: self is 300 / 100.
        self.write_trace("trace.1.1.0.bin",
                         [enter(1, 0xA, 0), enter(1, 0xB, 100),
                          leave(1, 0xB, 200), leave(1, 0xA, 400)])
        self.assertNotIn("folded", self.collect())
        folded = self.collect(folded=True)["folded"]
        self.assertEqual(folded, [("outer", 300), ("outer;inner", 100)])

    def test_pids_are_reported(self):
        """Several pids in one directory usually means two runs got mixed."""
        self.write_trace("trace.1.1.0.bin",
                         [enter(1, 0xA, 0), leave(1, 0xA, 5)], pid=11)
        self.write_trace("trace.2.2.0.bin",
                         [enter(2, 0xA, 0), leave(2, 0xA, 5)], pid=22)
        self.assertEqual(self.collect()["pids"], [11, 22])

    def test_stale_binary_warning(self):
        self.write_trace("trace.1.1.0.bin", [enter(1, 0xA, 0), leave(1, 0xA, 5)])
        err = io.StringIO()
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("??", "??:0")}):
            with redirect_stderr(err):
                analyze.collect(self.dir, "app")
        self.assertIn("same binary", err.getvalue())

    def test_version_1_unresolved_mentions_no_pie(self):
        """v1 traces carry no load bias, so -no-pie really was the fix."""
        self.write_trace("trace.1.1.bin", [enter(1, 0xA, 0), leave(1, 0xA, 5)],
                         version=1)
        err = io.StringIO()
        with mock.patch.object(analyze, "resolve",
                               return_value={0xA: ("??", "??:0")}):
            with redirect_stderr(err):
                analyze.collect(self.dir, "app")
        self.assertIn("-no-pie", err.getvalue())

    def test_no_warning_when_symbols_resolve(self):
        self.write_trace("trace.1.1.0.bin", [enter(1, 0xA, 0), leave(1, 0xA, 5)])
        err = io.StringIO()
        with redirect_stderr(err):
            self.collect()
        self.assertEqual(err.getvalue(), "")


class TestReportFormats(TraceDirFixture):
    def setUp(self):
        super().setUp()
        # outer 0..400 with inner 100..200: self time 300 / 100, so row and
        # folded ordering is unambiguous.
        self.write_trace("trace.1.1.0.bin",
                         [enter(1, 0xA, 0), enter(1, 0xB, 100, caller=0x900),
                          leave(1, 0xB, 200), leave(1, 0xA, 400)])
        names = {0xA: ("outer", "/src/a.c:1"), 0xB: ("inner", "/src/b.c:2"),
                 0x900: ("outer", "/src/a.c:5")}
        patch = mock.patch.object(analyze, "resolve", return_value=names)
        patch.start()
        self.addCleanup(patch.stop)

    def run_report(self, fmt, top=20):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = analyze.report(self.dir, "app", top, fmt)
        return rc, out.getvalue()

    def test_text_report(self):
        rc, out = self.run_report("text")
        self.assertEqual(rc, 0)
        self.assertIn("events=4 threads=1 functions=2", out)
        self.assertIn("unmatched_exits=0", out)
        self.assertIn("TOP BY SELF TIME", out)
        self.assertIn("outer", out)
        self.assertIn("PER-THREAD SUMMARY", out)

    def test_text_report_shows_percentiles(self):
        _rc, out = self.run_report("text")
        self.assertIn("p50", out)
        self.assertIn("p99", out)

    def test_json_report(self):
        _rc, out = self.run_report("json")
        data = json.loads(out)
        self.assertEqual(data["tool"], "callsight")
        self.assertEqual(data["events"], 4)
        self.assertEqual([r["function"] for r in data["rows"]],
                         ["outer", "inner"])  # sorted by self time

    def test_json_carries_latency_fields(self):
        _rc, out = self.run_report("json")
        row = json.loads(out)["rows"][0]
        for field in ("p50_ns", "p90_ns", "p99_ns", "min_ns", "max_ns"):
            self.assertIn(field, row)

    def test_json_top_limits_rows(self):
        _rc, out = self.run_report("json", top=1)
        self.assertEqual(len(json.loads(out)["rows"]), 1)

    def test_json_top_zero_keeps_all_rows(self):
        _rc, out = self.run_report("json", top=0)
        self.assertEqual(len(json.loads(out)["rows"]), 2)

    def test_folded_report(self):
        _rc, out = self.run_report("folded")
        lines = out.splitlines()
        self.assertEqual(lines, ["outer 300", "outer;inner 100"])
        for line in lines:
            path, value = line.rsplit(" ", 1)
            self.assertTrue(int(value) >= 0)
            self.assertTrue(path)

    def test_callers_report(self):
        _rc, out = self.run_report("callers")
        self.assertIn("inner <- outer", out)
        self.assertIn("/src/a.c:5", out)

    def test_chrome_report_is_valid_json(self):
        _rc, out = self.run_report("chrome")
        data = json.loads(out)
        names = sorted(e["name"] for e in data["traceEvents"])
        self.assertEqual(names, ["inner", "outer"])
        for e in data["traceEvents"]:
            self.assertEqual(e["ph"], "X")
            self.assertGreaterEqual(e["ts"], 0)

    def test_chrome_timestamps_are_rebased(self):
        """Absolute CLOCK_MONOTONIC values put every span days into a
        viewer's timeline."""
        _rc, out = self.run_report("chrome")
        self.assertEqual(min(e["ts"] for e in json.loads(out)["traceEvents"]), 0)


class TestDiff(unittest.TestCase):
    def write(self, tmp, name, rows):
        path = Path(tmp) / name
        path.write_text(json.dumps({"rows": rows}))
        return path

    def test_regression_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self.write(tmp, "a.json",
                              [{"function": "f", "self_ms": 100.0, "calls": 10}])
            new = self.write(tmp, "b.json",
                             [{"function": "f", "self_ms": 150.0, "calls": 10}])
            rows, worst = analyze.diff(base, new)
        self.assertAlmostEqual(rows[0]["pct"], 50.0)
        self.assertAlmostEqual(worst, 50.0)

    def test_new_and_removed_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self.write(tmp, "a.json",
                              [{"function": "gone", "self_ms": 5.0, "calls": 1}])
            new = self.write(tmp, "b.json",
                             [{"function": "fresh", "self_ms": 7.0, "calls": 1}])
            rows, _worst = analyze.diff(base, new)
        by_name = {r["function"]: r for r in rows}
        self.assertEqual(by_name["gone"]["new"], 0.0)
        self.assertEqual(by_name["fresh"]["base"], 0.0)

    def test_threshold_filters_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self.write(tmp, "a.json",
                              [{"function": "f", "self_ms": 100.0, "calls": 1},
                               {"function": "g", "self_ms": 1.0, "calls": 1}])
            new = self.write(tmp, "b.json",
                             [{"function": "f", "self_ms": 130.0, "calls": 1},
                              {"function": "g", "self_ms": 1.01, "calls": 1}])
            rows, _worst = analyze.diff(base, new, threshold=1.0)
        self.assertEqual([r["function"] for r in rows], ["f"])


class TestFindExe(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(analyze.find_exe("some/binary"), "some/binary")


if __name__ == "__main__":
    unittest.main()
