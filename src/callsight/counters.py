"""Turn the function names in trace.config into the addresses the hooks see.

The instrumentation hooks are handed a function's *address* and nothing
else. trace.config, the web UI and everything a person types deal in
*names*. Something has to bridge that, and the compiler cannot: every
instrumented function calls the same two hooks, so there is no per-function
compile-time hook to specialize.

So the bridge is a small text file — the counter map — written here from the
built binary's symbol table and read by the runtime at startup:

    CALLSIGHT-COUNTERS 1
    build-id 3f2ae1c9...
    exe /abs/path/bin/app.instr
    min auto
    event instructions 0 1
    event cache-misses 0 3
    0000000000401b40 checksum
    0000000000401c90 transform

Three consequences worth knowing, all of them good:

  - `static` functions work. They are in .symtab like anything else, and
    they are exactly the internals people want to count.
  - Changing which functions are counted needs **no rebuild** — rewrite the
    map and run again. The compile-time half (which functions have hooks at
    all) is already decided by trace.config.
  - The map is plain text with hex addresses, so it is the same file for a
    32-bit big-endian agent as for x86-64. Addresses are link-time; the
    runtime adds its load bias, the mirror of what analyze subtracts.

The map goes stale the moment the binary is rebuilt, and a stale address
points at whatever now lives there. Hence the build id: `callsight run`
regenerates the map every time, and `analyze` says so when they disagree.
"""

import os
from typing import NamedTuple

from callsight import callgraph, elf, flags, symbols

MAP_MAGIC = "CALLSIGHT-COUNTERS"
MAP_VERSION = 1
MAP_NAME = "callsight.counters"


class Selection(NamedTuple):
    """What a config's counter directives resolve to against one binary."""
    events: list             # [(name, perf_type, perf_config)]
    entries: list            # [(address, name)], sorted by address
    min_ns: object           # "auto", or a floor in nanoseconds
    unresolved: list         # named, but no symbol in the binary
    not_instrumented: list   # named and present, but carries no hooks
    warnings: list

    @property
    def enabled(self):
        """Counters do nothing without both an event and a function."""
        return bool(self.events and self.entries)


def instrumented_names(spec, sources):
    """The set of function names that will actually carry hooks.

    Mirrors what `callsight flags` hands the compiler, because a function
    without hooks can never be counted no matter what the counter directives
    say — and silently emitting an address that never fires is the kind of
    thing that has people wondering why their table is empty.

    GCC's -finstrument-functions-exclude-function-list is a *substring*
    match, which is reproduced here rather than approximated.
    """
    if spec.include_funcs:
        _sel, _drop, auto, reachable, _warn = flags.function_selection(
            spec.include_funcs, sources, spec.includes, spec.excludes)
        excluded = list(spec.funcs) + list(auto)
        names = set(reachable)
    else:
        selected, _dropped = flags.select(sources, spec.includes,
                                          spec.excludes)
        graph = callgraph.build_graph(flags.with_headers(sources))
        chosen = set(selected)
        names = {fn for fn, info in graph.items()
                 if any(f in chosen for f in info["files"])}
        excluded = list(spec.funcs)
    return {n for n in names
            if not any(pat and pat in n for pat in excluded)}


def _wanted_names(spec, sources, project_dir):
    """Names the counter directives ask for, and any warnings raised."""
    wanted, warnings = [], []

    if spec.counter_funcs:
        graph = callgraph.build_graph(flags.with_headers(sources))
        for name, depth in spec.counter_funcs:
            if name not in graph:
                warnings.append(
                    f"counter-func '{name}' is not defined in the scanned "
                    f"sources (function pointers and macro-generated calls "
                    f"are not followed)")
                wanted.append(name)   # still try the symbol table for it
                continue
            wanted.extend(sorted(callgraph.expand(graph, [name], depth)))

    if spec.counter_files:
        listing = symbols.list_functions(str(project_dir))
        for entry in listing["functions"]:
            if any(flags.matches(entry["file"], p)
                   for p in spec.counter_files):
                wanted.append(entry["name"])

    # Order-preserving dedupe: the config's order is the user's priority
    # order, and it shows up again in the report's column order.
    seen = set()
    return [n for n in wanted if not (n in seen or seen.add(n))], warnings


def resolve_selection(spec, exe, sources, project_dir=None, nm=None):
    """Resolve a ConfigSpec's counter directives against a built binary."""
    events = [(name,) + flags.parse_counter_event(name)
              for name in spec.counter_events]
    if project_dir is None:
        project_dir = os.path.dirname(os.path.abspath(str(exe))) or "."

    wanted, warnings = _wanted_names(spec, sources, project_dir)
    if not wanted:
        return Selection(events, [], spec.counter_min, [], [], warnings)

    symtab = elf.functions(exe, nm=nm)
    hooked = instrumented_names(spec, sources)

    entries, unresolved, not_instrumented = [], [], []
    for name in wanted:
        if name not in symtab:
            unresolved.append(name)
            continue
        if name not in hooked:
            not_instrumented.append(name)
            continue
        entries.append((symtab[name][0], name))

    if unresolved:
        warnings.append(
            f"{len(unresolved)} selected function(s) have no symbol in "
            f"{os.path.basename(str(exe))} — inlined away, renamed, or the "
            f"binary is from a different build: "
            f"{', '.join(unresolved[:5])}"
            + (" ..." if len(unresolved) > 5 else ""))
    if not_instrumented:
        warnings.append(
            f"{len(not_instrumented)} selected function(s) carry no "
            f"instrumentation hooks, so they cannot be counted — widen the "
            f"include/exclude rules first: "
            f"{', '.join(not_instrumented[:5])}"
            + (" ..." if len(not_instrumented) > 5 else ""))
    if entries and not events:
        warnings.append(
            "functions are selected for counting but no 'counter' event is "
            "configured — add e.g. 'counter instructions'")

    entries.sort()
    return Selection(events, entries, spec.counter_min, unresolved,
                     not_instrumented, warnings)


# --- the map file ----------------------------------------------------------

def render_map(selection, exe, build_id=None):
    """The counter map as text. Deterministic, so a rebuild that changes
    nothing produces a byte-identical file."""
    lines = [f"{MAP_MAGIC} {MAP_VERSION}",
             f"build-id {build_id or '-'}",
             f"exe {os.path.abspath(str(exe))}",
             f"min {selection.min_ns}"]
    for name, ptype, pconfig in selection.events:
        lines.append(f"event {name} {ptype} {pconfig}")
    for addr, name in selection.entries:
        lines.append(f"{addr:016x} {name}")
    return "\n".join(lines) + "\n"


def write_map(path, selection, exe, build_id=None):
    """Write the map the runtime reads. Returns the path."""
    text = render_map(selection, exe, build_id)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)      # a half-written map must never be readable
    return path


def read_map(path):
    """Parse a counter map back — for the UI preview, tests, and `doctor`.

    Unknown lines are ignored rather than fatal, matching the runtime: a map
    written by a later callsight must not stop an older runtime from using
    the parts it understands.
    """
    out = {"version": None, "build_id": None, "exe": None, "min": "auto",
           "events": [], "entries": []}
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if lineno == 1:
                parts = line.split()
                if len(parts) != 2 or parts[0] != MAP_MAGIC:
                    raise ValueError(f"{path}: not a callsight counter map")
                out["version"] = int(parts[1])
                continue
            key, _, rest = line.partition(" ")
            rest = rest.strip()
            if key == "build-id":
                out["build_id"] = None if rest == "-" else rest
            elif key == "exe":
                out["exe"] = rest
            elif key == "min":
                out["min"] = rest if rest == "auto" else int(rest)
            elif key == "event":
                bits = rest.split()
                if len(bits) == 3:
                    out["events"].append((bits[0], int(bits[1]),
                                          int(bits[2])))
            else:
                try:
                    out["entries"].append((int(key, 16), rest))
                except ValueError:
                    continue
    return out


def map_path(tracedir):
    """Where the runtime looks by default: beside the traces, because that
    is the one directory both `callsight run` and the UI already agree on."""
    return os.path.join(str(tracedir), MAP_NAME)


def stale_reason(map_file, exe):
    """Why this map does not belong to this binary, or None if it does.

    Addresses from a previous build point at whatever occupies them now, and
    nothing downstream can detect that — the counts simply belong to the
    wrong functions.
    """
    try:
        data = read_map(map_file)
    except (OSError, ValueError) as e:
        return str(e)
    recorded = data.get("build_id")
    if not recorded:
        return None            # nothing to compare; not evidence of staleness
    try:
        current = elf.Elf(exe).build_id()
    except (elf.ElfError, OSError):
        return None
    if current and current != recorded:
        return (f"the counter map was built for a different binary "
                f"(build id {recorded[:12]}… vs {current[:12]}…) — "
                f"re-run `callsight counters`")
    return None


# --- does this machine actually have working counters? ---------------------
#
# perf_event_open succeeding proves nothing. In a container the host PMU is
# usually not exposed, and the kernel then hands back a counter that opens,
# reads, and returns zero forever — so a naive implementation reports zero
# instructions for every function and looks perfectly healthy doing it.
# time_running == 0 is the tell, and asking is cheap enough to do from
# Python via ctypes rather than shipping a probe binary.

_PERF_ATTR_SIZE = 128            # PERF_ATTR_SIZE_VER7
_ATTR_EXCLUDE_KERNEL = 1 << 5    # in the flags bitfield at offset 40
_ATTR_EXCLUDE_HV = 1 << 6
# read_format: TOTAL_TIME_ENABLED | TOTAL_TIME_RUNNING, so one read tells us
# both the count and whether it was ever scheduled onto real hardware.
_READ_FORMAT = 0x1 | 0x2

_SYS_perf_event_open = {
    "x86_64": 298, "aarch64": 241, "armv7l": 364, "armv8l": 364,
    "i686": 336, "ppc64le": 319, "ppc64": 319, "s390x": 331,
    "riscv64": 241, "loongarch64": 241,
}


def _perf_open(ptype, pconfig):
    """A per-thread counting event, or None. Raises nothing."""
    import ctypes
    import platform

    nr = _SYS_perf_event_open.get(platform.machine())
    if nr is None:
        return None
    attr = bytearray(_PERF_ATTR_SIZE)
    attr[0:4] = int(ptype).to_bytes(4, "little" if _le() else "big")
    attr[4:8] = _PERF_ATTR_SIZE.to_bytes(4, "little" if _le() else "big")
    _put64(attr, 8, int(pconfig))
    _put64(attr, 32, _READ_FORMAT)                       # read_format
    _put64(attr, 40, _ATTR_EXCLUDE_KERNEL | _ATTR_EXCLUDE_HV)
    libc = ctypes.CDLL(None, use_errno=True)
    buf = (ctypes.c_char * len(attr)).from_buffer(attr)
    # pid 0, cpu -1: this thread, followed across cores, so the kernel saves
    # and restores the counter on every context switch.
    fd = libc.syscall(ctypes.c_long(nr), ctypes.byref(buf),
                      ctypes.c_int(0), ctypes.c_int(-1), ctypes.c_int(-1),
                      ctypes.c_ulong(0))
    return fd if fd >= 0 else None


def _le():
    import sys
    return sys.byteorder == "little"


def _put64(buf, off, value):
    buf[off:off + 8] = int(value).to_bytes(8, "little" if _le() else "big")


def _read_counter(fd):
    """(value, time_enabled, time_running), or None."""
    import struct as _struct
    try:
        # Plain read, not pread: a perf fd is not seekable.
        raw = os.read(fd, 24)
    except OSError:
        return None
    if len(raw) < 24:
        return None
    return _struct.unpack("=QQQ", raw)


def probe(event="instructions"):
    """What hardware counters can actually do here, right now.

    Returns a dict the CLI and the web UI both render:

        {"available": bool, "reason": str, "read_ns": int|None,
         "floor_ns": int|None, "event": str}

    `read_ns` is measured through Python, so it is an over-estimate of what
    the runtime pays — it is for deciding whether a function is worth
    counting at all (microseconds vs nanoseconds), not for correcting any
    number. The runtime calibrates its own cost and that is what the report
    uses.
    """
    import time

    result = {"available": False, "reason": "", "read_ns": None,
              "floor_ns": None, "event": event}
    try:
        ptype, pconfig = flags.parse_counter_event(event)
    except ValueError as e:
        result["reason"] = str(e)
        return result

    try:
        fd = _perf_open(ptype, pconfig)
    except Exception as e:                       # ctypes/platform surprises
        result["reason"] = f"could not call perf_event_open: {e}"
        return result
    if fd is None:
        result["reason"] = (
            "perf_event_open was refused — check "
            "/proc/sys/kernel/perf_event_paranoid (needs 2 or lower), or "
            "the platform has no PMU exposed")
        return result

    try:
        before = _read_counter(fd)
        # Something with a genuinely unknown-but-large instruction count.
        acc = 0
        for i in range(200000):
            acc += i
        after = _read_counter(fd)
        if before is None or after is None:
            result["reason"] = "the counter opened but could not be read"
            return result
        if after[2] == 0:
            result["reason"] = (
                "the counter opened but was never scheduled onto hardware "
                "(time_running is 0) — typical inside a container, where "
                "the host PMU is not exposed. Counting here would report "
                "zero for every function.")
            return result
        if after[0] <= before[0]:
            result["reason"] = ("the counter opened and ran but did not "
                                "count — it cannot be trusted")
            return result

        n = 2000
        t0 = time.perf_counter_ns()
        for _ in range(n):
            os.read(fd, 24)
        read_ns = (time.perf_counter_ns() - t0) // n

        result.update(available=True, read_ns=read_ns,
                      floor_ns=read_ns * 2 * DEFAULT_FLOOR_RATIO,
                      reason="")
        return result
    finally:
        os.close(fd)


# How much longer than a counter read a function must be before counting it
# says more about the code than about the instrument.
DEFAULT_FLOOR_RATIO = 20


def describe_probe(p):
    """One line for a CLI or a banner."""
    if not p["available"]:
        return f"hardware counters unavailable: {p['reason']}"
    return (f"hardware counters available: ~{p['read_ns']} ns per read, so "
            f"functions shorter than about {_dur(p['floor_ns'])} are skipped "
            f"by default")


def _dur(ns):
    if ns is None:
        return "?"
    if ns >= 1000000:
        return f"{ns / 1000000:.1f}ms"
    if ns >= 1000:
        return f"{ns / 1000:.1f}us"
    return f"{ns}ns"


def estimate_overhead_ns(selection, rows, read_ns):
    """How much wall time this selection would add to a run like the last one.

    Two counter reads per counted call, so the answer is entirely determined
    by the previous run's call counts — which makes it a real number to show
    someone *before* they run, rather than a lesson they learn after.
    """
    if not selection.enabled or not read_ns:
        return 0
    chosen = {name for _addr, name in selection.entries}
    calls = sum(r.get("calls", 0) for r in rows if r.get("function") in chosen)
    return calls * 2 * read_ns
