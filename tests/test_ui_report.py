"""Unit tests for the web UI's report endpoint.

The UI is an optional extra, so these tests skip unless FastAPI is
installed. The endpoint functions are called directly — no HTTP client
needed, and the core test run stays stdlib-only."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from callsight import analyze

try:
    from callsight.ui import app as ui_app
except ImportError:  # optional 'ui' extra not installed
    ui_app = None

from test_analyze import enter, leave, pack_events


@unittest.skipIf(ui_app is None, "needs the optional 'ui' extra (FastAPI)")
class TestAnalyzeEndpoint(unittest.TestCase):
    """Rows must be sorted before --top truncates them: truncating first
    would show an arbitrary slice of functions instead of the hot ones."""

    #  0xA self 300, 0xB self 200, 0xC self 100 — recorded in the opposite
    #  order, so an unsorted truncation is visible.
    EVENTS = [enter(1, 0xC, 0), leave(1, 0xC, 100),
              enter(1, 0xB, 100), leave(1, 0xB, 300),
              enter(1, 0xA, 300), leave(1, 0xA, 600)]
    NAMES = {0xA: ("hot", "/a.c:1"), 0xB: ("warm", "/b.c:1"),
             0xC: ("cold", "/c.c:1")}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / "traces").mkdir()
        (self.project / "traces" / "trace.1.1.bin").write_bytes(
            pack_events(self.EVENTS))
        self.binary = self.project / "app.instr"
        self.binary.write_bytes(b"\x7fELF")
        patch = mock.patch.object(analyze, "resolve", return_value=self.NAMES)
        patch.start()
        self.addCleanup(patch.stop)

    def report(self, top):
        return ui_app.analyze_traces(path=str(self.project),
                                     binary="app.instr", top=top)

    def test_rows_are_sorted_by_self_time(self):
        data = self.report(top=50)
        self.assertEqual([r["function"] for r in data["rows"]],
                         ["hot", "warm", "cold"])

    def test_top_keeps_the_hottest_rows(self):
        data = self.report(top=2)
        self.assertEqual([r["function"] for r in data["rows"]],
                         ["hot", "warm"])

    def test_top_zero_returns_every_row(self):
        data = self.report(top=0)
        self.assertEqual(len(data["rows"]), 3)
        self.assertEqual(data["functions"], 3)

    def test_summary_counters_survive(self):
        data = self.report(top=1)
        self.assertEqual(data["events"], len(self.EVENTS))
        self.assertEqual(data["unmatched_exits"], 0)
        self.assertEqual(data["unclosed_enters"], 0)

    def test_rows_carry_latency_percentiles(self):
        row = self.report(top=1)["rows"][0]
        for field in ("p50_ns", "p99_ns", "max_ns"):
            self.assertIn(field, row)

    def test_binary_outside_project_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            ui_app.analyze_traces(path=str(self.project),
                                  binary="../escape", top=10)
        self.assertEqual(cm.exception.status_code, 404)


@unittest.skipIf(ui_app is None, "needs the optional 'ui' extra (FastAPI)")
class TestFlameEndpoint(unittest.TestCase):
    """Collapsed stacks for the browser flame graph."""

    EVENTS = [enter(1, 0xA, 0), enter(1, 0xB, 100),
              leave(1, 0xB, 300), leave(1, 0xA, 400)]
    NAMES = {0xA: ("outer", "/a.c:1"), 0xB: ("inner", "/b.c:1")}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / "traces").mkdir()
        (self.project / "traces" / "trace.1.1.0.bin").write_bytes(
            pack_events(self.EVENTS))
        self.binary = self.project / "app.instr"
        self.binary.write_bytes(b"\x7fELF")
        patch = mock.patch.object(analyze, "resolve", return_value=self.NAMES)
        patch.start()
        self.addCleanup(patch.stop)

    def test_returns_call_paths(self):
        d = ui_app.flame(path=str(self.project), binary="app.instr", top=100)
        self.assertEqual(d["paths"], 2)
        self.assertEqual(dict(d["folded"]), {"outer": 200, "outer;inner": 200})

    def test_caps_the_number_of_paths(self):
        """A deep trace has more call paths than a screen has pixels."""
        d = ui_app.flame(path=str(self.project), binary="app.instr", top=1)
        self.assertEqual(len(d["folded"]), 1)
        self.assertEqual(d["paths"], 2)

    def test_binary_outside_project_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            ui_app.flame(path=str(self.project), binary="../escape", top=10)
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(ui_app is None, "needs the optional 'ui' extra (FastAPI)")
class TestCounterEndpoints(unittest.TestCase):
    """The counter panel's job is to be honest about this machine before
    offering to count anything."""

    def test_probe_reports_capability_and_the_event_menu(self):
        p = ui_app.counters_probe()
        self.assertIn("available", p)
        self.assertIn("instructions", p["events"])
        self.assertEqual(p["max_events"], 3)
        # Either answer is fine; claiming availability without a reason is not.
        if not p["available"]:
            self.assertTrue(p["reason"])

    def test_generate_round_trips_counter_selections(self):
        from callsight import flags
        with tempfile.TemporaryDirectory() as tmp:
            body = ui_app.GenerateBody(
                path=tmp, counter_events=["instructions"],
                counter_funcs=[ui_app.IncludeFunc(name="hot", depth=1)],
                counter_min="auto")
            text = ui_app.generate_config(body)["content"]
        self.assertIn("counter instructions", text)
        self.assertIn("counter-func hot 1", text)
        self.assertIn("counter-min auto", text)

    def test_generate_rejects_a_bad_floor(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as tmp:
            body = ui_app.GenerateBody(path=tmp, counter_min="soon")
            with self.assertRaises(HTTPException):
                ui_app.generate_config(body)


@unittest.skipIf(ui_app is None, "needs the optional 'ui' extra (FastAPI)")
class TestLiveSession(unittest.TestCase):
    """The live view must read only what a growing file has gained, or a
    once-a-second refresh would re-parse the whole trace every tick."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def session(self):
        s = ui_app.LiveSession(self.dir, "exe")
        s.names = {0xA: ("hot", "a.c:1"), 0xB: ("warm", "b.c:1")}
        return s

    def test_only_new_bytes_are_read_on_each_tick(self):
        path = self.dir / "trace.1.1.0.bin"
        path.write_bytes(pack_events([enter(1, 0xA, 0), leave(1, 0xA, 100)]))
        s = self.session()
        first = s.tick()
        self.assertEqual(first["events"], 2)
        offset = dict(s.offsets)

        # Nothing appended: the tick must consume nothing.
        self.assertEqual(s.tick()["events"], 2)
        self.assertEqual(s.offsets, offset)

        # Append two more events; only those may be read.
        with open(path, "ab") as f:
            f.write(pack_events([enter(1, 0xB, 200), leave(1, 0xB, 300)])
                    [analyze.HEADER.size:])
        third = s.tick()
        self.assertEqual(third["events"], 4)
        self.assertEqual(third["rate"], 2)

    def test_a_half_written_record_is_resumed_not_skipped(self):
        """A file being appended to is caught mid-record on almost every
        tick; losing those bytes would drop events silently."""
        path = self.dir / "trace.1.1.0.bin"
        full = pack_events([enter(1, 0xA, 0), leave(1, 0xA, 100)])
        path.write_bytes(full[:-8])          # last record cut short
        s = self.session()
        self.assertEqual(s.tick()["events"], 1)
        with open(path, "wb") as f:
            f.write(full)
        self.assertEqual(s.tick()["events"], 2)

    def test_report_carries_counter_columns_when_present(self):
        s = self.session()
        s.acc.feed(1, analyze.ENTER, 0xA, 0)
        s.acc.feed(1, analyze.EXIT, 0xA, 100)
        s.acc.feed(1, analyze.COUNTER, 4200, 0, 0)
        s.metas["x"] = {"counters": [(0, 1)], "header_size": 80}
        row = next(r for r in s.report()["rows"] if r["function"] == "hot")
        self.assertEqual(row["counters"]["instructions"]["per_call"], 4200)
