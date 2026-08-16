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

    def test_binary_outside_project_is_rejected(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as cm:
            ui_app.analyze_traces(path=str(self.project),
                                  binary="../escape", top=10)
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
