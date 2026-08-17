#!/usr/bin/env python3
"""End-to-end tests for the C runtime itself.

Everything here builds and runs a real instrumented binary: the hooks, the
segment rotation, the budget accounting and the failure paths only exist in
compiled code, and every scenario below covers a defect that shipped or
could ship silently.

These are not part of the stdlib-only unit suite — they need a compiler and
take a few seconds — so they live in a subdirectory that `unittest discover
-s tests` does not recurse into. Run them directly:

    python3 tests/runtime/test_runtime.py

Skipped automatically when no C compiler is available.
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from callsight import analyze, flags  # noqa: E402

RUNTIME = ROOT / "src" / "callsight" / "runtime"
PROBE_SRC = Path(__file__).resolve().parent / "runtime_probe.c"
CC = os.environ.get("CC", "cc")

_built = {}


def have_cc():
    return shutil.which(CC) is not None


def build_probe(tmpdir, pie=False):
    """Build the probe once per (pie) variant and cache it."""
    key = bool(pie)
    if key in _built and Path(_built[key]).exists():
        return _built[key]
    out = Path(tmpdir) / ("probe_pie" if pie else "probe")
    link = ["-pie"] if pie else ["-no-pie"]
    compile_flags = ["-fPIE"] if pie else []
    subprocess.run(
        [CC, "-std=c11", "-O2", "-g", f"-I{RUNTIME}", *compile_flags,
         "-finstrument-functions", "-c", "-o", f"{out}.probe.o", str(PROBE_SRC)],
        check=True, capture_output=True)
    subprocess.run(
        [CC, "-std=c11", "-O2", "-g", f"-I{RUNTIME}", *compile_flags,
         "-c", "-o", f"{out}.trace.o", str(RUNTIME / "trace.c")],
        check=True, capture_output=True)
    subprocess.run(
        [CC, *link, "-o", str(out), f"{out}.probe.o", f"{out}.trace.o",
         "-lpthread"],
        check=True, capture_output=True)
    _built[key] = str(out)
    return str(out)


@unittest.skipUnless(have_cc(), f"no C compiler ({CC}) available")
class RuntimeCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._builddir = tempfile.mkdtemp(prefix="callsight-rt-")
        cls.probe = build_probe(cls._builddir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._builddir, ignore_errors=True)
        _built.clear()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="callsight-trace-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def run_probe(self, *args, env=None, exe=None, timeout=120):
        full = dict(os.environ)
        full["TRACE_ENABLE"] = "1"
        full["TRACE_DIR"] = str(self.dir)
        full.update(env or {})
        proc = subprocess.run([exe or self.probe, *[str(a) for a in args]],
                              env=full, capture_output=True, text=True,
                              timeout=timeout)
        return proc

    def trace_files(self):
        return sorted(self.dir.glob("trace.*.bin"))

    def total_bytes(self):
        return sum(f.stat().st_size for f in self.trace_files())

    def parse_all(self):
        """Every file must parse cleanly: a header, then a whole number of
        records. Returns (events, notices)."""
        events, notices = 0, []
        for path in analyze._event_files(self.dir):
            meta = analyze.read_header(path)
            self.assertIsNotNone(meta, f"{path.name}: unreadable header")
            body = path.stat().st_size - meta["header_size"]
            self.assertEqual(body % analyze.EVENT.size, 0,
                             f"{path.name}: {body} body bytes is not a whole "
                             f"number of {analyze.EVENT.size}-byte records")
            if meta["flags"] & analyze.HF_TICKS:
                meta["anchor"] = analyze._closing_anchor(path, meta)
            events += sum(1 for _ in analyze.read_events(path, meta, notices))
        return events, notices

    def notice_kinds(self):
        return {n["kind"] for n in self.parse_all()[1]}


class TestSegmentIntegrity(RuntimeCase):
    def test_thread_id_reuse_never_corrupts_a_file(self):
        """Sequential create/join makes the kernel recycle thread ids. The
        old runtime opened the recycled thread's file in append mode and
        wrote a second header into the middle of it, shifting every later
        record off the record grid — with nothing in the file to say so,
        because only offset zero is ever validated."""
        proc = self.run_probe("churn", 40, 60)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        tids = set()
        for path in self.trace_files():
            # Two files may share a tid only if their sequence numbers
            # differ; neither may contain a stray header.
            raw = path.read_bytes()
            meta = analyze.read_header(path)
            self.assertIsNotNone(meta)
            body = raw[meta["header_size"]:]
            self.assertEqual(len(body) % analyze.EVENT.size, 0)
            self.assertNotIn(analyze.MAGIC, body,
                             f"{path.name}: a second file header was written "
                             f"inside the event stream")
            tids.add(path.name)
        self.assertGreater(len(tids), 5, "expected several threads to record")
        events, _n = self.parse_all()
        self.assertGreater(events, 0)

    def test_every_segment_is_independently_parseable(self):
        proc = self.run_probe("threads", 4, 4000,
                              env={"TRACE_SEG_MB": "1", "TRACE_MAX_MB": "32"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertGreater(len(self.trace_files()), 1, "expected rotation")
        events, _n = self.parse_all()
        self.assertGreater(events, 0)


class TestForkSafety(RuntimeCase):
    def test_parent_and_child_write_separate_files(self):
        """Without an atfork handler the child inherits the parent's
        descriptor and the parent's pid in the path, so both processes
        interleave into one file."""
        proc = self.run_probe("fork", 300)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parent = child = None
        for token in proc.stdout.split():
            if token.startswith("parent="):
                parent = int(token.split("=")[1])
            elif token.startswith("child="):
                child = int(token.split("=")[1])
        self.assertIsNotNone(parent)
        self.assertIsNotNone(child)
        self.assertNotEqual(parent, child)

        pids = {analyze.read_header(p)["pid"] for p in self.trace_files()}
        self.assertIn(parent, pids)
        self.assertIn(child, pids)
        # Each file belongs to exactly one process, and all of them parse.
        for path in self.trace_files():
            name_pid = int(path.name.split(".")[1])
            self.assertEqual(name_pid, analyze.read_header(path)["pid"])
        events, _n = self.parse_all()
        self.assertGreater(events, 0)


class TestCaptureLimits(RuntimeCase):
    def test_budget_stops_the_capture(self):
        proc = self.run_probe("threads", 2, 60000,
                              env={"TRACE_MAX_MB": "4", "TRACE_SEG_MB": "1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        total = self.total_bytes()
        # One in-flight buffer per thread may land after the budget trips.
        self.assertLessEqual(total, 8 * 1024 * 1024,
                             f"budget of 4 MB produced {total} bytes")
        self.assertIn("budget", self.notice_kinds())

    def test_wrap_keeps_the_end_of_the_run(self):
        """A flight recorder: the run generates far more than the budget and
        what survives is the most recent part of it."""
        proc = self.run_probe("threads", 2, 200000,
                              env={"TRACE_MAX_MB": "4", "TRACE_SEG_MB": "1",
                                   "TRACE_FULL": "wrap"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        total = self.total_bytes()
        self.assertLessEqual(total, 12 * 1024 * 1024,
                             f"wrap budget of 4 MB produced {total} bytes")
        kinds = self.notice_kinds()
        self.assertIn("wrap", kinds)
        self.assertNotIn("budget", kinds)
        # Sequence numbers prove the early segments were the ones dropped.
        seqs = [analyze.read_header(p)["seq"] for p in self.trace_files()]
        self.assertGreater(max(seqs), 2)

    def test_wrap_budget_survives_many_threads(self):
        """A thread can never discard the segment it is writing, so a fixed
        segment size means the floor is (threads x 2 x segment) — which with
        a couple of dozen threads overshoots a small budget by an order of
        magnitude. The segment size has to adapt to both."""
        budget_mb = 8
        proc = self.run_probe("threads", 16, 40000,
                              env={"TRACE_MAX_MB": str(budget_mb),
                                   "TRACE_FULL": "wrap"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        total_mb = self.total_bytes() / (1024 * 1024)
        self.assertLess(total_mb, budget_mb * 3,
                        f"{budget_mb} MB budget over 16 threads produced "
                        f"{total_mb:.1f} MB")
        self.assertIn("wrap", self.notice_kinds())

    def test_unlimited_is_opt_in(self):
        proc = self.run_probe("spin", 2000, env={"TRACE_MAX_MB": "0"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("budget", self.notice_kinds())

    def test_free_space_floor_stops_the_capture(self):
        """Asking for more free space than exists is the same code path a
        nearly-full device takes, without needing one."""
        proc = self.run_probe("threads", 2, 60000,
                              env={"TRACE_MIN_FREE_MB": "999999999"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nospace", self.notice_kinds())

    def test_write_failure_is_reported_not_swallowed(self):
        """RLIMIT_FSIZE makes write() fail exactly as a full disk does. The
        old runtime ignored the return value, so the report looked clean and
        was simply missing everything after the failure.

        The in-band marker needs room in the file to be written, which is
        precisely what has run out here — so stderr is the channel that has
        to carry it."""
        limit = 200 * 1024
        proc = self.run_probe("fsize", 60000, limit,
                              env={"TRACE_SEG_MB": "64"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("capture stopped", proc.stderr)
        self.assertIn("write to trace segment failed", proc.stderr)
        for path in self.trace_files():
            self.assertLessEqual(path.stat().st_size, limit)
        # And the truncated file is still a well-formed trace: the partial
        # record the failed write left behind is trimmed away.
        events, _n = self.parse_all()
        self.assertGreater(events, 0)

    def test_event_cap_is_an_upper_bound(self):
        cap = 50000
        proc = self.run_probe("threads", 3, 30000,
                              env={"TRACE_MAX": str(cap)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        events, _n = self.parse_all()
        self.assertLessEqual(events, cap)
        self.assertGreater(events, cap // 4)
        self.assertIn("max_events", self.notice_kinds())


class TestClocks(RuntimeCase):
    def report(self, env=None):
        proc = self.run_probe("accuracy", 30, 2000, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return analyze.collect(self.dir, self.probe)

    def test_clock_modes_agree(self):
        """The fast cycle-counter path and CLOCK_MONOTONIC must measure the
        same 30 x 2 ms of sleeping."""
        tsc = self.report({"TRACE_CLOCK": "tsc"})
        self.setUp()  # fresh trace dir
        mono = self.report({"TRACE_CLOCK": "mono"})

        def wait_ms(data):
            row = next(r for r in data["rows"]
                       if r["function"] == "probe_wait")
            return row["incl_ms"]

        a, b = wait_ms(tsc), wait_ms(mono)
        self.assertGreater(a, 0)
        self.assertLess(abs(a - b) / max(a, b), 0.25,
                        f"tsc={a:.3f}ms mono={b:.3f}ms disagree")

    def test_tick_traces_declare_their_calibration(self):
        self.run_probe("spin", 500, env={"TRACE_CLOCK": "tsc"})
        metas = [analyze.read_header(p) for p in self.trace_files()]
        ticking = [m for m in metas if m["flags"] & analyze.HF_TICKS]
        if not ticking:
            self.skipTest("no invariant cycle counter on this machine")
        for m in ticking:
            self.assertGreater(m["tick_hz"], 1_000_000,
                               "a ticks trace without a rate is unreadable")

    def test_mono_traces_are_nanoseconds(self):
        self.run_probe("spin", 500, env={"TRACE_CLOCK": "mono"})
        for meta in [analyze.read_header(p) for p in self.trace_files()]:
            self.assertFalse(meta["flags"] & analyze.HF_TICKS)
            self.assertEqual(meta["tick_hz"], 0)


class TestAccuracy(RuntimeCase):
    """The claim this project rests on is that the numbers are exact rather
    than sampled. That has to be checked against known ground truth."""

    def test_call_counts_are_exact(self):
        calls = 777
        proc = self.run_probe("accuracy", calls, 10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = analyze.collect(self.dir, self.probe)
        row = next(r for r in data["rows"] if r["function"] == "probe_wait")
        self.assertEqual(row["calls"], calls)
        self.assertEqual(data["unmatched_exits"], 0)

    def test_measured_time_matches_a_known_sleep(self):
        calls, usec = 40, 3000
        proc = self.run_probe("accuracy", calls, usec)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = analyze.collect(self.dir, self.probe)
        row = next(r for r in data["rows"] if r["function"] == "probe_wait")
        expected_ms = calls * usec / 1000.0
        # nanosleep only guarantees "at least", so allow overshoot but no
        # undershoot beyond a small margin.
        self.assertGreater(row["incl_ms"], expected_ms * 0.95)
        self.assertLess(row["incl_ms"], expected_ms * 1.6)

    def test_percentiles_bracket_a_known_duration(self):
        calls, usec = 60, 2000
        self.run_probe("accuracy", calls, usec)
        data = analyze.collect(self.dir, self.probe)
        row = next(r for r in data["rows"] if r["function"] == "probe_wait")
        target = usec * 1000
        self.assertGreater(row["p50_ns"], target * 0.8)
        self.assertLess(row["p50_ns"], target * 1.8)
        self.assertGreaterEqual(row["max_ns"], row["p50_ns"])
        self.assertLessEqual(row["min_ns"], row["p50_ns"] * 1.3)


class TestSummaryMode(RuntimeCase):
    def test_totals_match_event_mode(self):
        """Two ways of counting the same run must agree, or one of them is
        lying."""
        self.run_probe("accuracy", 200, 100)
        events = analyze.collect(self.dir, self.probe)
        ev_row = next(r for r in events["rows"]
                      if r["function"] == "probe_wait")

        self.setUp()
        self.run_probe("accuracy", 200, 100, env={"TRACE_MODE": "summary"})
        summary = analyze.collect(self.dir, self.probe)
        sum_row = next(r for r in summary["rows"]
                       if r["function"] == "probe_wait")

        self.assertEqual(summary["mode"], "summary")
        self.assertEqual(ev_row["calls"], sum_row["calls"])
        self.assertLess(
            abs(ev_row["incl_ms"] - sum_row["incl_ms"]) / ev_row["incl_ms"],
            0.35, f"events={ev_row['incl_ms']}ms summary={sum_row['incl_ms']}ms")

    def test_disk_use_does_not_grow_with_the_run(self):
        """The whole point: ten times the calls, the same bytes on disk."""
        self.run_probe("spin", 2000, env={"TRACE_MODE": "summary"})
        small = self.total_bytes()
        self.setUp()
        self.run_probe("spin", 20000, env={"TRACE_MODE": "summary"})
        big = self.total_bytes()
        self.assertEqual(small, big,
                         f"{small} vs {big} bytes for a 10x longer run")
        self.assertLess(big, 64 * 1024)

    def test_no_event_files_are_written(self):
        self.run_probe("spin", 1000, env={"TRACE_MODE": "summary"})
        self.assertEqual(analyze._event_files(self.dir), [])
        self.assertTrue(analyze._summary_files(self.dir))


class TestPositionIndependentExecutables(RuntimeCase):
    def test_pie_symbols_resolve(self):
        """A PIE records load-time addresses. The runtime writes the load
        bias into every header so they still map back to the binary — this
        is what removed the -no-pie requirement."""
        pie = build_probe(self._builddir, pie=True)
        proc = self.run_probe("spin", 400, exe=pie)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        biases = {analyze.read_header(p)["load_bias"]
                  for p in self.trace_files()}
        self.assertTrue(any(b > 0 for b in biases),
                        "a PIE should report a nonzero load bias")
        data = analyze.collect(self.dir, pie)
        names = {r["function"] for r in data["rows"]}
        self.assertIn("probe_leaf", names)
        self.assertNotIn("??", names)


class TestInertWhenDisabled(RuntimeCase):
    def test_nothing_is_written_without_trace_enable(self):
        proc = self.run_probe("spin", 500, env={"TRACE_ENABLE": "0"})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(list(self.dir.glob("*")), [])


def counters_work():
    """Whether this machine's PMU is genuinely usable, right now.

    Deliberately asked per test run rather than cached at import: on a shared
    host whether an event reaches hardware can differ between processes, and
    an answer from ten minutes ago is not evidence about this one.
    """
    from callsight import counters
    return counters.probe()["available"]


class TestHardwareCounters(RuntimeCase):
    """Counters have two correct behaviours and CI usually sees the second.

    On a machine with a working PMU the values must be exact and the guard
    rail must fire. On a machine without one — every container, including
    GitHub's runners — the run must be *ordinary*: no counter columns, no
    zeros, and a reason on stderr. That second case is the one this suite can
    always prove, and it is the one that would otherwise ship broken.
    """

    def write_map(self, names, events=("instructions",), min_ns="auto"):
        from callsight import counters, elf
        syms = elf.functions(self.probe)
        missing = [n for n in names if n not in syms]
        self.assertEqual(missing, [], f"probe has no symbol for {missing}")
        sel = counters.Selection(
            events=[(e,) + flags.parse_counter_event(e) for e in events],
            entries=sorted((syms[n][0], n) for n in names),
            min_ns=min_ns, unresolved=[], not_instrumented=[], warnings=[])
        return counters.write_map(counters.map_path(self.dir), sel,
                                  self.probe)

    def test_absent_counters_produce_an_ordinary_report(self):
        """The failure this guards: reporting zero instructions for every
        function, which looks exactly like a program that does nothing."""
        if counters_work():
            self.skipTest("this machine has working counters")
        self.write_map(["probe_leaf", "probe_mid"])
        proc = self.run_probe("spin", 200, env={"TRACE_MODE": "summary"})
        self.assertIn("not usable here", proc.stderr)

        data = analyze.collect(self.dir, self.probe)
        self.assertEqual(data["counter_events"], [])
        for row in data["rows"]:
            self.assertNotIn("counters", row,
                             "no counters means no columns, not zeroed ones")
        self.assertGreater(len(data["rows"]), 1)

    def test_no_map_means_no_counters_and_no_complaints(self):
        proc = self.run_probe("spin", 200, env={"TRACE_MODE": "summary"})
        self.assertNotIn("counter", proc.stderr.lower())
        self.assertEqual(analyze.collect(self.dir, self.probe)
                         ["counter_events"], [])

    def test_counters_disabled_explicitly(self):
        self.write_map(["probe_leaf"])
        proc = self.run_probe("spin", 200, env={"TRACE_MODE": "summary",
                                                "TRACE_COUNTERS": "none"})
        self.assertNotIn("not usable here", proc.stderr)
        self.assertEqual(analyze.collect(self.dir, self.probe)
                         ["counter_events"], [])

    def test_exact_and_repeatable_counts(self):
        """The claim the feature exists to make: the same work reports the
        same number, which wall time never does."""
        if not counters_work():
            self.skipTest("no usable hardware counters here")
        seen = []
        for _ in range(3):
            for f in self.trace_files():
                f.unlink()
            self.write_map(["probe_leaf", "probe_top"], min_ns="0")
            self.run_probe("spin", 300, env={"TRACE_MODE": "summary"})
            data = analyze.collect(self.dir, self.probe)
            row = next(r for r in data["rows"]
                       if r["function"] == "probe_leaf")
            self.assertIn("instructions", row["counters"])
            seen.append(round(row["counters"]["instructions"]["per_call"], 1))
        self.assertEqual(len(set(seen)), 1,
                         f"instructions per call drifted across runs: {seen}")
        self.assertGreater(seen[0], 0)

    def test_inclusive_counts_contain_the_children(self):
        if not counters_work():
            self.skipTest("no usable hardware counters here")
        self.write_map(["probe_leaf", "probe_mid"], min_ns="0")
        self.run_probe("spin", 200, env={"TRACE_MODE": "summary"})
        rows = {r["function"]: r for r in
                analyze.collect(self.dir, self.probe)["rows"]}
        leaf = rows["probe_leaf"]["counters"]["instructions"]
        mid = rows["probe_mid"]["counters"]["instructions"]
        # probe_mid calls probe_leaf eight times per call.
        self.assertGreater(mid["per_call"], 8 * leaf["per_call"])
        self.assertLess(mid["self"], mid["total"])

    def test_functions_too_short_are_demoted_and_named(self):
        """A function far shorter than a counter read measures the
        instrument. The runtime stops counting it and says which."""
        if not counters_work():
            self.skipTest("no usable hardware counters here")
        self.write_map(["probe_leaf"], min_ns="auto")
        self.run_probe("spin", 4000, env={"TRACE_MODE": "summary"})
        data = analyze.collect(self.dir, self.probe)
        leaf = next(r for r in data["rows"] if r["function"] == "probe_leaf")
        counted = leaf.get("counters", {}).get("instructions", {}).get("calls", 0)
        self.assertLess(counted, leaf["calls"],
                        "a sub-microsecond function should stop being counted")
        self.assertTrue(any("too short" in n for n in data["notices"]),
                        data["notices"])

    def test_event_mode_counter_records_do_not_break_matching(self):
        """Counter records ride in the event stream; a reader that mistook
        one for an exit would corrupt every match after it."""
        if not counters_work():
            self.skipTest("no usable hardware counters here")
        self.write_map(["probe_leaf", "probe_mid"], min_ns="0")
        self.run_probe("spin", 200)
        data = analyze.collect(self.dir, self.probe)
        self.assertEqual(data["unmatched_exits"], 0)
        row = next(r for r in data["rows"] if r["function"] == "probe_leaf")
        self.assertGreater(row["counters"]["instructions"]["per_call"], 0)

if __name__ == "__main__":
    unittest.main(verbosity=2)


