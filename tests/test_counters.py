"""Unit tests for the hardware-counter selection pipeline.

Config directives -> the set of functions they name -> the addresses those
names have in the built binary -> the map file the runtime reads.

The cases worth guarding are the ones where a wrong answer is *quiet*:
selecting a function that carries no instrumentation hooks (it would simply
never appear), a map left over from a previous build (its addresses now
point at whatever occupies them), and asking for more events than a PMU has
registers (the kernel would scale the counts and the exact numbers this
feature exists to produce would silently become estimates).
"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from callsight import counters, flags
from test_elf import build_elf


def write(tmp, name, text):
    p = Path(tmp) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text))
    return p


class ConfigFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def spec(self, text):
        return flags.parse_config(write(self.dir, "trace.config", text))


class TestDirectives(ConfigFixture):
    def test_counter_directives_parse(self):
        spec = self.spec("""
            counter instructions,cache-misses
            counter-func transform 1
            counter-func checksum
            counter-file src/codec/**
            counter-min 5us
        """)
        self.assertEqual(spec.counter_events, ["instructions", "cache-misses"])
        self.assertEqual(spec.counter_funcs, [("transform", 1),
                                              ("checksum", None)])
        self.assertEqual(spec.counter_files, ["src/codec/**"])
        self.assertEqual(spec.counter_min, 5000)

    def test_default_floor_is_auto(self):
        self.assertEqual(self.spec("counter instructions").counter_min, "auto")

    def test_counter_min_units(self):
        for text, want in (("counter-min 0", 0), ("counter-min 250ns", 250),
                           ("counter-min 5us", 5000),
                           ("counter-min 2ms", 2000000),
                           ("counter-min 1s", 1000000000),
                           ("counter-min 400", 400)):
            with self.subTest(text=text):
                self.assertEqual(self.spec(text).counter_min, want)

    def test_events_may_be_space_or_comma_separated(self):
        a = self.spec("counter instructions,cache-misses").counter_events
        b = self.spec("counter instructions cache-misses").counter_events
        self.assertEqual(a, b)

    def test_duplicate_events_collapse(self):
        spec = self.spec("counter instructions,instructions")
        self.assertEqual(spec.counter_events, ["instructions"])

    def test_raw_pmu_codes_are_allowed(self):
        self.assertEqual(self.spec("counter r00c0").counter_events, ["r00c0"])
        self.assertEqual(flags.parse_counter_event("r00c0"),
                         (flags.PERF_TYPE_RAW, 0xC0))

    def test_unknown_event_is_refused_with_the_list(self):
        with self.assertRaises(SystemExit) as cm:
            self.spec("counter instrctions")
        self.assertIn("instructions", str(cm.exception))

    def test_too_many_events_explains_multiplexing(self):
        """The reason matters: silently accepting a fourth event would make
        the kernel scale the counts, and scaled counts are estimates."""
        with self.assertRaises(SystemExit) as cm:
            self.spec("counter instructions,cycles,cache-misses,branch-misses")
        msg = str(cm.exception)
        self.assertIn("registers", msg)
        self.assertIn("estimates", msg)

    def test_unknown_directive_lists_the_counter_ones(self):
        with self.assertRaises(SystemExit) as cm:
            self.spec("counter-fnc foo")
        self.assertIn("counter-func", str(cm.exception))

    def test_configs_without_counters_are_unchanged(self):
        spec = self.spec("exclude src/rng.c\nexclude-func crc32\n")
        self.assertEqual(spec.counter_events, [])
        self.assertEqual(spec.counter_funcs, [])
        self.assertFalse(spec.counter_files)


class TestRenderRoundTrip(ConfigFixture):
    def test_ui_selections_render_and_parse_back(self):
        text = flags.render_config(
            excluded_files=["src/rng.c"],
            counter_events=["instructions", "cache-misses"],
            counter_funcs=[("transform", 1), ("checksum", None)],
            counter_files=["src/codec/**"],
            counter_min=2000)
        spec = self.spec(text)
        self.assertEqual(spec.counter_events, ["instructions", "cache-misses"])
        self.assertEqual(spec.counter_funcs, [("transform", 1),
                                              ("checksum", None)])
        self.assertEqual(spec.counter_files, ["src/codec/**"])
        self.assertEqual(spec.counter_min, 2000)
        self.assertEqual(spec.excludes, ["src/rng.c"])

    def test_no_counter_section_when_nothing_selected(self):
        """The syntax comment still names them; what must not appear is a
        directive line, which would turn the feature on unasked."""
        text = flags.render_config(excluded_files=["src/rng.c"])
        directives = [ln for ln in text.splitlines()
                      if ln and not ln.startswith("#")]
        self.assertEqual(directives, ["exclude src/rng.c"])

    def test_render_refuses_a_bad_event(self):
        with self.assertRaises(ValueError):
            flags.render_config(counter_events=["nonsense"])

    def test_render_refuses_too_many_events(self):
        with self.assertRaises(ValueError):
            flags.render_config(counter_events=["instructions", "cycles",
                                                "cache-misses",
                                                "branch-misses"])


PROJECT = """
    int helper(int x) { return x + 1; }
    int leaf(int x) { return x * 2; }
    int transform(int x) { return helper(leaf(x)); }
    int noisy(int x) { return x; }
    int main(void) { return transform(1) + noisy(2); }
"""

SYMBOLS = [("helper", 0x1000, 16), ("leaf", 0x1010, 16),
           ("transform", 0x1020, 32), ("noisy", 0x1040, 8),
           ("main", 0x1050, 32)]


class ResolveFixture(unittest.TestCase):
    """A tiny project plus a synthetic binary whose symbols match it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        write(self.dir, "src/app.c", PROJECT)
        self.exe = self.dir / "app.instr"
        self.exe.write_bytes(build_elf(funcs=SYMBOLS,
                                       build_id="aa" * 20))
        self.sources = flags.scan_sources(self.dir)

    def resolve(self, text):
        spec = flags.parse_config(write(self.dir, "trace.config", text))
        return counters.resolve_selection(spec, self.exe, self.sources,
                                          self.dir)

    def names(self, sel):
        return [n for _a, n in sel.entries]


class TestResolution(ResolveFixture):
    def test_a_named_function_resolves_to_its_address(self):
        sel = self.resolve("counter instructions\ncounter-func noisy\n")
        self.assertEqual(sel.entries, [(0x1040, "noisy")])
        self.assertTrue(sel.enabled)

    def test_subtree_expansion_matches_include_func(self):
        """counter-func uses the same call-graph walk as include-func, so
        'count this and what it calls' means the same thing in both."""
        full = self.resolve("counter instructions\ncounter-func transform\n")
        self.assertEqual(sorted(self.names(full)),
                         ["helper", "leaf", "transform"])
        just_one = self.resolve(
            "counter instructions\ncounter-func transform 0\n")
        self.assertEqual(self.names(just_one), ["transform"])

    def test_counter_file_selects_by_definition_file(self):
        sel = self.resolve("counter instructions\ncounter-file src/app.c\n")
        self.assertEqual(sorted(self.names(sel)),
                         sorted(n for n, _a, _s in SYMBOLS))

    def test_entries_are_sorted_by_address(self):
        sel = self.resolve("counter instructions\ncounter-file src/**\n")
        self.assertEqual([a for a, _n in sel.entries],
                         sorted(a for a, _n in sel.entries))

    def test_excluded_functions_cannot_be_counted(self):
        """The quiet failure this prevents: an exclude-func'd function has
        no hooks, so a counter selection naming it would never fire and the
        user would be left wondering where the row went."""
        sel = self.resolve("""
            exclude-func noisy
            counter instructions
            counter-func noisy
        """)
        self.assertEqual(sel.entries, [])
        self.assertEqual(sel.not_instrumented, ["noisy"])
        self.assertTrue(any("no instrumentation hooks" in w
                            for w in sel.warnings), sel.warnings)

    def test_functions_in_excluded_files_cannot_be_counted(self):
        sel = self.resolve("""
            exclude src/app.c
            counter instructions
            counter-func transform 0
        """)
        self.assertEqual(sel.entries, [])
        self.assertEqual(sel.not_instrumented, ["transform"])

    def test_a_name_with_no_symbol_is_reported(self):
        sel = self.resolve("counter instructions\ncounter-func ghost\n")
        self.assertEqual(sel.unresolved, ["ghost"])
        self.assertTrue(any("no symbol" in w for w in sel.warnings),
                        sel.warnings)

    def test_functions_without_an_event_are_not_silently_useless(self):
        sel = self.resolve("counter-func noisy\n")
        self.assertFalse(sel.enabled)
        self.assertTrue(any("no 'counter' event" in w for w in sel.warnings),
                        sel.warnings)

    def test_no_counter_directives_resolves_to_nothing(self):
        spec = flags.parse_config(write(self.dir, "trace.config",
                                        "exclude-func noisy\n"))
        sel = counters.resolve_selection(spec, self.exe, self.sources,
                                         self.dir)
        self.assertFalse(sel.enabled)
        self.assertEqual(sel.entries, [])


class TestMapFile(ResolveFixture):
    def test_map_round_trips(self):
        sel = self.resolve("""
            counter instructions,cache-misses
            counter-func transform
            counter-min 3us
        """)
        path = self.dir / "callsight.counters"
        counters.write_map(path, sel, self.exe, "aa" * 20)
        got = counters.read_map(path)

        self.assertEqual(got["version"], counters.MAP_VERSION)
        self.assertEqual(got["build_id"], "aa" * 20)
        self.assertEqual(got["min"], 3000)
        self.assertEqual(got["events"],
                         [("instructions", 0, 1), ("cache-misses", 0, 3)])
        self.assertEqual(got["entries"], sel.entries)

    def test_map_is_plain_text_and_architecture_neutral(self):
        """Hex addresses in a text file: the same map serves a 32-bit
        big-endian agent and an x86-64 one."""
        sel = self.resolve("counter instructions\ncounter-func transform\n")
        text = counters.render_map(sel, self.exe, "aa" * 20)
        self.assertTrue(text.isascii())
        self.assertIn("0000000000001020 transform", text)

    def test_rendering_is_deterministic(self):
        sel = self.resolve("counter instructions\ncounter-file src/**\n")
        self.assertEqual(counters.render_map(sel, self.exe, "bb" * 20),
                         counters.render_map(sel, self.exe, "bb" * 20))

    def test_unknown_lines_are_ignored_not_fatal(self):
        """A map from a newer callsight must not stop an older runtime from
        using the parts it understands."""
        path = self.dir / "m.counters"
        path.write_text(f"{counters.MAP_MAGIC} 1\n"
                        f"build-id -\n"
                        f"future-directive whatever\n"
                        f"event instructions 0 1\n"
                        f"0000000000001020 transform\n")
        got = counters.read_map(path)
        self.assertEqual(got["entries"], [(0x1020, "transform")])
        self.assertIsNone(got["build_id"])

    def test_a_foreign_file_is_refused(self):
        path = self.dir / "not-a-map"
        path.write_text("hello\n")
        with self.assertRaises(ValueError):
            counters.read_map(path)

    def test_stale_map_is_detected_by_build_id(self):
        """The failure this catches is invisible otherwise: addresses from a
        previous build land on whatever now occupies them, and the counts
        are attributed to the wrong functions with nothing to show for it."""
        sel = self.resolve("counter instructions\ncounter-func transform\n")
        path = self.dir / "callsight.counters"
        counters.write_map(path, sel, self.exe, "aa" * 20)
        self.assertIsNone(counters.stale_reason(path, self.exe))

        rebuilt = self.dir / "app2.instr"
        rebuilt.write_bytes(build_elf(funcs=SYMBOLS, build_id="cc" * 20))
        reason = counters.stale_reason(path, rebuilt)
        self.assertIsNotNone(reason)
        self.assertIn("different binary", reason)

    def test_missing_build_id_is_not_called_stale(self):
        """No build id is an absence of evidence, not evidence of staleness;
        claiming otherwise would cry wolf on every -Wl,--build-id=none
        build."""
        sel = self.resolve("counter instructions\ncounter-func transform\n")
        path = self.dir / "callsight.counters"
        counters.write_map(path, sel, self.exe, None)
        self.assertIsNone(counters.stale_reason(path, self.exe))

    def test_map_path_sits_beside_the_traces(self):
        self.assertEqual(counters.map_path("/tmp/traces"),
                         "/tmp/traces/" + counters.MAP_NAME)


class TestOverheadEstimate(ResolveFixture):
    def test_estimate_uses_the_previous_run_call_counts(self):
        """Two reads per counted call, so the cost of a selection is knowable
        before running it — which is the difference between a warning and a
        lesson."""
        sel = self.resolve("counter instructions\ncounter-func transform 0\n")
        rows = [{"function": "transform", "calls": 1000},
                {"function": "noisy", "calls": 500000}]
        self.assertEqual(
            counters.estimate_overhead_ns(sel, rows, read_ns=1400),
            1000 * 2 * 1400)

    def test_nothing_selected_costs_nothing(self):
        sel = self.resolve("counter-func transform\n")   # no event
        self.assertEqual(counters.estimate_overhead_ns(
            sel, [{"function": "transform", "calls": 10}], 1400), 0)


class TestProbe(unittest.TestCase):
    """The probe must answer honestly on whatever machine runs the tests —
    counters work on bare metal and do not inside most containers, and both
    are correct answers. What is never acceptable is claiming availability
    without evidence."""

    def test_probe_reports_a_reason_when_unavailable(self):
        p = counters.probe()
        self.assertIn("available", p)
        if p["available"]:
            self.assertGreater(p["read_ns"], 0)
            self.assertGreater(p["floor_ns"], p["read_ns"])
            self.assertEqual(p["reason"], "")
        else:
            self.assertTrue(p["reason"], "an unavailable probe must say why")
            self.assertIsNone(p["read_ns"])
        self.assertIn("counters", counters.describe_probe(p))

    def test_an_unknown_event_is_refused_without_syscalls(self):
        p = counters.probe("not-an-event")
        self.assertFalse(p["available"])
        self.assertIn("unknown counter event", p["reason"])


if __name__ == "__main__":
    unittest.main()
