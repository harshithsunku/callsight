#!/usr/bin/env python3
"""
Offline analyzer for compile-time traces produced by the callsight runtime.

Reads the binary trace files written by trace.c, resolves function addresses
with addr2line (static functions included), matches enter/exit events per
thread, and reports per-function call counts, inclusive/self times and
per-call latency percentiles.

Usage:
    callsight analyze [traces/] [--exe path/to/binary] [--top 20]
                      [--format text|json|folded|chrome|callers]

If --exe is omitted, a single `*.instr` binary is looked up under ./bin and .

Trace files are streamed: events are matched as they are read and never all
held in memory, so a multi-million-event trace costs a few MB here instead of
scaling with the event count. Everything this keeps — counters, one shadow
stack per thread, one histogram per function — is proportional to the number
of functions and threads, never to the number of calls.

Two file formats are read. Version 2 carries the PIE load bias, the clock
anchors needed to turn raw cycle counts into nanoseconds, and in-band marker
records saying whether the capture was cut short. Version 1 files (bare
16-byte header, nanosecond timestamps, no markers) still analyze.
"""

import argparse
import json
import os
import struct
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

MAGIC = b"MLTRACE\0"
SUM_MAGIC = b"MLSUMRY\0"

class _Layout:
    """Every on-disk struct, in one byte order.

    The agent writes its NATIVE byte order and the host swaps if it has to:
    the device is the constrained side of this system and the analysis host
    is not (see the byte-order note in runtime/trace.h). Sizes are identical
    either way, so only the unpacking differs — and the choice is made once
    per file, never per record, so a foreign-endian trace costs nothing to
    read beyond what Python's own struct module does anyway.
    """

    __slots__ = ("endian", "header_v1", "header", "event", "sum_header",
                 "sum_record")

    def __init__(self, endian):
        self.endian = endian
        # Version 1 header, and the prefix of every later one: readers can
        # always learn the version before they know the rest of the layout.
        self.header_v1 = struct.Struct(endian + "8sII")
        # Version 2: adds header_size (so v3 will not break this reader),
        # the PIE load bias, clock anchors and the measured hook cost.
        self.header = struct.Struct(endian + "8sIIIIQQQQQIIQ")
        self.event = struct.Struct(endian + "QQQIB3x")
        self.sum_header = struct.Struct(endian + "8sIIIIQQQQQIIQQQ")
        self.sum_record = struct.Struct(endian + "6Q160I")


LE = _Layout("<")
BE = _Layout(">")

# The little-endian names, which are what the format documentation and the
# overwhelming majority of agents use.
HEADER_V1 = LE.header_v1
HEADER = LE.header
EVENT = LE.event
SUM_HEADER = LE.sum_header
SUM_RECORD = LE.sum_record

# magic[8] + u32 version — the prefix every callsight header shares, and all
# that is needed to tell which way round the rest of it is.
_PEEK = struct.Struct("<8sI")


def _layout_for(head):
    """Pick the byte order a header was written in.

    The magic is a byte string and reads the same either way, but the u32
    `version` right after it is a small number — so a value that does not fit
    in 16 bits is a byte-swapped one, and nothing else. TRACE_HF_BIGENDIAN in
    `flags` says the same thing outright, but it cannot be read until the
    order is known, which is why this is what decides.
    """
    if len(head) < _PEEK.size:
        return LE
    return BE if _PEEK.unpack_from(head)[1] > 0xFFFF else LE


def byte_order(head):
    """'<' or '>' — the byte order the given callsight header was written in.

    For readers outside this module (the stream server) that need the same
    answer for their own structs."""
    return _layout_for(head).endian

VERSION = 2  # TRACE_FILE_VERSION in runtime/trace.h

ENTER, EXIT, MARKER = 0, 1, 2

HF_TICKS = 0x1
HF_WRAPPED = 0x2
HF_BIGENDIAN = 0x4

# Marker codes; see TRACE_MARK_* in runtime/trace.h.
MARK_BUDGET, MARK_NOSPACE, MARK_WRITE_ERR = 1, 2, 3
MARK_MAXEVENTS, MARK_WRAP, MARK_CLOCK = 4, 5, 6

HIST_BUCKETS = 160

# Events per read() while streaming a trace file.
READ_BLOCK_EVENTS = 65536
# Addresses per addr2line invocation (a big program's address set would
# otherwise approach ARG_MAX on one command line).
ADDR2LINE_BATCH = 4096


def addr2line_cmd(explicit=None):
    """The addr2line to use: --addr2line, then $CALLSIGHT_ADDR2LINE, then
    the host one. Cross-compiled targets need their own toolchain's copy —
    the host binutils cannot read a foreign ELF."""
    return explicit or os.environ.get("CALLSIGHT_ADDR2LINE") or "addr2line"


# --- Reading ---------------------------------------------------------------

def read_header(path):
    """Parse a trace file header into a meta dict, or return None (with a
    warning) when the file is not a trace this version can read."""
    with open(path, "rb") as f:
        head = f.read(HEADER.size)
    if len(head) < HEADER_V1.size or head[:8] != MAGIC:
        print(f"warning: {path}: bad or missing header, skipped",
              file=sys.stderr)
        return None
    lay = _layout_for(head)
    _magic, version, event_size = lay.header_v1.unpack(head[:lay.header_v1.size])
    if event_size != EVENT.size:
        print(f"warning: {path}: event size {event_size} != {EVENT.size}, "
              f"skipped", file=sys.stderr)
        return None

    meta = {"path": path, "version": version, "flags": 0, "load_bias": 0,
            "tick_hz": 0, "t0_ticks": 0, "t0_ns": 0, "hook_ns": 0,
            "pid": 0, "seq": 0, "header_size": lay.header_v1.size,
            "layout": lay, "big_endian": lay is BE}
    if version == 1:
        return meta
    if version != VERSION:
        print(f"warning: {path}: trace format version {version} is newer "
              f"than this callsight understands (expected {VERSION}), "
              f"skipped", file=sys.stderr)
        return None
    if len(head) < lay.header.size:
        print(f"warning: {path}: truncated v2 header, skipped",
              file=sys.stderr)
        return None
    (_m, _v, _e, header_size, flags, load_bias, tick_hz, t0_ticks, t0_ns,
     hook_ns, pid, seq, _res) = lay.header.unpack(head)
    meta.update(header_size=header_size, flags=flags, load_bias=load_bias,
                tick_hz=tick_hz, t0_ticks=t0_ticks, t0_ns=t0_ns,
                hook_ns=hook_ns, pid=pid, seq=seq)
    return meta


def _closing_anchor(path, meta):
    """The runtime writes a clock anchor as the very last record at exit.
    Pairing it with the startup anchor measures the tick rate across the
    whole run, which beats any startup calibration window — and costs a
    single seek to find."""
    event = meta.get("layout", LE).event
    body = os.path.getsize(path) - meta["header_size"]
    if body < event.size:
        return None
    with open(path, "rb") as f:
        f.seek(meta["header_size"] + (body // event.size - 1) * event.size)
        rec = f.read(event.size)
    if len(rec) < event.size:
        return None
    ts, code, payload, _tid, kind = event.unpack(rec)
    if kind == MARKER and code == MARK_CLOCK:
        return ts, payload
    return None


def _ns_per_tick(meta):
    """Nanoseconds per timestamp tick, or None when timestamps already are
    nanoseconds."""
    if not (meta["flags"] & HF_TICKS):
        return None
    anchor = meta.get("anchor")
    if anchor:
        d_ticks = anchor[0] - meta["t0_ticks"]
        d_ns = anchor[1] - meta["t0_ns"]
        if d_ticks > 0 and d_ns > 0:
            return d_ns / d_ticks
    if meta["tick_hz"] > 0:
        return 1e9 / meta["tick_hz"]
    raise RuntimeError(
        f"{meta['path']}: timestamps are raw cycle counts but the file "
        f"carries no usable clock calibration — re-record with "
        f"TRACE_CLOCK=mono")


MARK_NAMES = {
    MARK_BUDGET: "budget",
    MARK_NOSPACE: "nospace",
    MARK_WRITE_ERR: "write_error",
    MARK_MAXEVENTS: "max_events",
    MARK_WRAP: "wrap",
}


def read_events(path, meta=None, notices=None):
    """Yield (tid, kind, func, ts_ns, caller) from one trace file.

    Addresses come back as link addresses (the PIE load bias already
    subtracted) and timestamps as nanoseconds, so files from different
    processes and different clock sources are directly comparable. Marker
    records are appended to `notices` rather than yielded — a reader that
    mistook one for an exit would corrupt the whole match."""
    if meta is None:
        meta = read_header(path)
        if meta is None:
            return
        if meta["flags"] & HF_TICKS:
            meta["anchor"] = _closing_anchor(path, meta)
    if notices is None:
        notices = []

    scale = _ns_per_tick(meta)
    bias = meta["load_bias"]
    t0_ticks, t0_ns = meta["t0_ticks"], meta["t0_ns"]
    event = meta.get("layout", LE).event

    with open(path, "rb") as f:
        f.seek(meta["header_size"])
        block = READ_BLOCK_EVENTS * event.size
        rest = b""
        while True:
            chunk = f.read(block)
            if not chunk:
                break
            if rest:
                chunk = rest + chunk
            usable = len(chunk) - len(chunk) % event.size
            for ts, func, caller, tid, kind in event.iter_unpack(chunk[:usable]):
                if kind == MARKER:
                    if func != MARK_CLOCK:
                        notices.append({"kind": MARK_NAMES.get(func, str(func)),
                                        "payload": caller,
                                        "file": os.path.basename(str(path))})
                    continue
                if scale is not None:
                    ts = t0_ns + int((ts - t0_ticks) * scale)
                yield tid, kind, func - bias, ts, caller - bias if caller else 0
            rest = chunk[usable:]
        if rest:
            # A killed process can leave a partial record behind; the events
            # before it are still good.
            print(f"warning: {path}: {len(rest)} trailing bytes ignored "
                  f"(truncated final record)", file=sys.stderr)
            notices.append({"kind": "truncated", "payload": len(rest),
                            "file": os.path.basename(str(path))})


def read_summary(path):
    """Parse a summary file written by TRACE_MODE=summary.

    Returns (meta, [record dicts]) with times already in nanoseconds, or
    (None, []) when the file is unreadable."""
    with open(path, "rb") as f:
        head = f.read(SUM_HEADER.size)
        if len(head) < SUM_HEADER.size or head[:8] != SUM_MAGIC:
            print(f"warning: {path}: bad or missing summary header, skipped",
                  file=sys.stderr)
            return None, []
        lay = _layout_for(head)
        (_m, version, record_size, header_size, flags, load_bias, tick_hz,
         t0_ticks, t0_ns, hook_ns, pid, tid, records, span,
         truncated) = lay.sum_header.unpack(head)
        if version != 1 or record_size != SUM_RECORD.size:
            print(f"warning: {path}: unsupported summary layout "
                  f"(version {version}, record {record_size}), skipped",
                  file=sys.stderr)
            return None, []
        meta = {"path": path, "version": version, "flags": flags,
                "load_bias": load_bias, "tick_hz": tick_hz,
                "t0_ticks": t0_ticks, "t0_ns": t0_ns, "hook_ns": hook_ns,
                "pid": pid, "tid": tid, "truncated": truncated,
                "header_size": header_size,
                "layout": lay, "big_endian": lay is BE}
        # No closing anchor in a summary file: the header rate is all there
        # is, and it is measured on the same hardware, so it is close.
        scale = 1.0 if not (flags & HF_TICKS) else (
            1e9 / tick_hz if tick_hz else 1.0)
        meta["span"] = int(span * scale)

        f.seek(header_size)
        out = []
        for _ in range(records):
            raw = f.read(lay.sum_record.size)
            if len(raw) < lay.sum_record.size:
                break
            vals = lay.sum_record.unpack(raw)
            out.append({
                "func": vals[0] - load_bias, "calls": vals[1],
                "incl": int(vals[2] * scale), "self": int(vals[3] * scale),
                "min": int(vals[4] * scale), "max": int(vals[5] * scale),
                "hist": list(vals[6:]), "scale": scale,
            })
    return meta, out


# --- Latency histogram -----------------------------------------------------

def hist_bucket(d):
    """Mirror of trace_hist_bucket() in the runtime: exact below 8, then
    four sub-buckets per octave.

    Clamped at zero: a trace whose clocks disagree can hand us a negative
    duration, and an out-of-range index would take down the whole report
    over one bad record."""
    if d < 8:
        return int(d) if d > 0 else 0
    msb = d.bit_length() - 1
    sub = (d >> (msb - 2)) & 3
    idx = (msb - 3) * 4 + sub + 8
    return idx if idx < HIST_BUCKETS else HIST_BUCKETS - 1


def bucket_bounds(i):
    """Inclusive [low, high] range of durations that land in bucket i."""
    if i < 8:
        return i, i
    msb = (i - 8) // 4 + 3
    sub = (i - 8) % 4
    return (4 + sub) << (msb - 2), (((5 + sub) << (msb - 2)) - 1)


def percentile(hist, total, q):
    """Estimate the q-quantile from a bucketed histogram, as the midpoint of
    the bucket the quantile falls in (bucket width is under ~19%, so this is
    within a few percent)."""
    if total <= 0:
        return 0
    target = q * total
    seen = 0
    for i, count in enumerate(hist):
        if not count:
            continue
        seen += count
        if seen >= target:
            low, high = bucket_bounds(i)
            return (low + high) // 2
    return 0


# --- Matching --------------------------------------------------------------

class Accumulator:
    """Incremental enter/exit matcher.

    feed() one event at a time in per-thread order (events from different
    threads may interleave); every counter the report needs is maintained on
    the way through, so no event list is ever materialized. With
    folded=True it also accumulates self time per distinct call path, keyed
    by the tuple of addresses on the stack, for flame-graph output; with
    callers=True it accumulates per call site."""

    def __init__(self, folded=False, callers=False, on_complete=None):
        self.calls = defaultdict(int)
        self.incl = defaultdict(int)    # inclusive ns
        self.self_t = defaultdict(int)  # self ns (inclusive minus children)
        self.max_t = defaultdict(int)
        self.min_t = {}
        self.hist = defaultdict(lambda: [0] * HIST_BUCKETS)
        self.child_calls = defaultdict(int)  # direct nested calls, for
        self.desc_calls = defaultdict(int)   # overhead compensation
        self.stacks = defaultdict(list)
        self.unmatched_exits = 0
        self.events = 0
        self.first_ts = {}
        self.last_ts = {}
        self.per_tid = defaultdict(int)
        self.addrs = set()
        self.folded = defaultdict(int) if folded else None
        self.edges = defaultdict(lambda: [0, 0, 0]) if callers else None
        self.on_complete = on_complete

    def feed(self, tid, kind, func, ts, caller=0):
        self.events += 1
        self.first_ts.setdefault(tid, ts)
        self.last_ts[tid] = ts
        self.per_tid[tid] += 1
        self.addrs.add(func)

        st = self.stacks[tid]
        if kind == ENTER:
            # [func, enter_ts, child_ns, call_site, n_children, n_descendants]
            st.append([func, ts, 0, caller, 0, 0])
            return

        # find nearest unmatched enter for this function
        idx = next((i for i in range(len(st) - 1, -1, -1)
                    if st[i][0] == func), None)
        if idx is None:
            self.unmatched_exits += 1
            return
        # close any dangling frames above (e.g. after longjmp/truncation)
        del st[idx + 1:]
        f, enter_ts, child_ns, call_site, nchild, ndesc = st.pop()
        dur = ts - enter_ts
        self.calls[f] += 1
        self.incl[f] += dur
        self.self_t[f] += dur - child_ns
        self.child_calls[f] += nchild
        self.desc_calls[f] += ndesc
        if dur > self.max_t[f]:
            self.max_t[f] = dur
        if f not in self.min_t or dur < self.min_t[f]:
            self.min_t[f] = dur
        self.hist[f][hist_bucket(dur)] += 1
        if self.folded is not None:
            path = tuple(frame[0] for frame in st) + (f,)
            self.folded[path] += dur - child_ns
        if self.edges is not None and call_site:
            # The caller's identity comes from the shadow stack, not from
            # symbolizing the return address: when GCC inlines a function
            # the hooks travel with the inlined body, so the return address
            # lands in whichever function absorbed the code. The stack knows
            # who logically called whom; the address still gives the line.
            edge = self.edges[(f, call_site)]
            edge[0] += 1
            edge[1] += dur
            edge[2] = st[-1][0] if st else 0
        if self.on_complete is not None:
            self.on_complete(tid, f, enter_ts, dur, len(st))
        if st:
            st[-1][2] += dur
            st[-1][4] += 1
            st[-1][5] += 1 + ndesc

    def finish(self):
        """Return (stats, threads, unmatched_exits, open_frames)."""
        stats = {f: (self.calls[f], self.incl[f], self.self_t[f], self.max_t[f])
                 for f in self.calls}
        threads = {t: (self.first_ts[t], self.last_ts[t]) for t in self.first_ts}
        open_frames = sum(len(s) for s in self.stacks.values())
        return stats, threads, self.unmatched_exits, open_frames


def analyze(events):
    """Match enter/exit per thread; return per-function stats and thread info."""
    acc = Accumulator()
    for ev in events:
        acc.feed(*ev)
    return acc.finish()


# --- Symbolization ---------------------------------------------------------

def resolve(addrs, exe, cmd=None):
    """Resolve addresses to (function, file:line) via batched addr2line runs."""
    addrs = sorted(addrs)
    tool = addr2line_cmd(cmd)
    names = {}
    for start in range(0, len(addrs), ADDR2LINE_BATCH):
        batch = addrs[start:start + ADDR2LINE_BATCH]
        argv = [tool, "-f", "-C", "-e", str(exe)] + [hex(a) for a in batch]
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  check=True)
        except FileNotFoundError:
            raise RuntimeError(
                f"{tool} not found on PATH — install binutils "
                f"(Debian/Ubuntu: apt install binutils), or point "
                f"--addr2line at the matching cross-toolchain copy when the "
                f"binary was built for another architecture")
        except OSError as e:
            raise RuntimeError(f"could not run {tool}: {e}")
        except subprocess.CalledProcessError as e:
            detail = (e.stderr or "").strip().splitlines()
            raise RuntimeError(
                f"{tool} failed on {exe} (exit {e.returncode})"
                + (f": {detail[0]}" if detail else ""))
        lines = proc.stdout.splitlines()
        for i, a in enumerate(batch):
            fn = lines[2 * i] if 2 * i < len(lines) else "??"
            loc = lines[2 * i + 1] if 2 * i + 1 < len(lines) else "??:0"
            names[a] = (fn, loc)
    return names


def _warn_if_unresolved(names, exe, metas):
    """Symbols that will not resolve usually mean the addresses and the
    binary do not belong together. Version 2 traces record the PIE load bias
    so this is handled automatically; older ones needed -no-pie."""
    if not names:
        return
    unresolved = sum(1 for fn, _loc in names.values() if fn == "??")
    if unresolved * 2 < len(names):
        return
    old = any(m["version"] < 2 for m in metas)
    hint = ("this is a version 1 trace, which does not record the PIE load "
            "bias — relink with -no-pie or re-record with a current runtime"
            if old else
            "check that --exe is the same binary that produced the trace")
    print(f"warning: {unresolved}/{len(names)} addresses did not resolve "
          f"in {exe} — {hint}", file=sys.stderr)


def find_exe(arg):
    if arg:
        return arg
    candidates = list(Path("bin").glob("*.instr")) + list(Path(".").glob("*.instr"))
    if len(candidates) == 1:
        return str(candidates[0])
    sys.exit("--exe not given and "
             + ("no *.instr binary found under ./bin or ." if not candidates
                else f"multiple *.instr binaries found: {', '.join(map(str, candidates))}"))


# --- Collection ------------------------------------------------------------

def _event_files(tracedir):
    return sorted(p for p in Path(tracedir).glob("trace.*.bin")
                  if not p.name.startswith("trace.summary."))


def _summary_files(tracedir):
    return sorted(Path(tracedir).glob("trace.summary.*.bin"))


def _open_metas(files):
    """Header (and clock anchor) for every readable file.

    Files sharing a startup anchor came from one process and one clock, so
    they must convert with the same rate. Only the segment holding the
    closing anchor can measure it, and a thread's enter and exit routinely
    land in different segments — converting those with two different rates
    yields a negative duration."""
    metas = []
    for path in files:
        meta = read_header(path)
        if meta is None:
            continue
        if meta["flags"] & HF_TICKS:
            meta["anchor"] = _closing_anchor(path, meta)
        metas.append(meta)

    shared = {}
    for meta in metas:
        if meta.get("anchor"):
            shared.setdefault((meta["t0_ticks"], meta["t0_ns"]), meta["anchor"])
    for meta in metas:
        if meta["flags"] & HF_TICKS and not meta.get("anchor"):
            meta["anchor"] = shared.get((meta["t0_ticks"], meta["t0_ns"]))
    return metas


def _describe_notices(notices, metas):
    """Fold raw marker records into one line each, in report order."""
    out = []
    counts = defaultdict(lambda: [0, 0])
    for n in notices:
        entry = counts[n["kind"]]
        entry[0] += 1
        entry[1] += n["payload"]
    if "budget" in counts:
        mb = counts["budget"][1] // (1024 * 1024)
        out.append(f"capture stopped: the {mb} MB on-disk budget was reached "
                   f"(TRACE_MAX_MB); everything after that point is missing")
    if "max_events" in counts:
        out.append(f"capture stopped: the {counts['max_events'][1]}-event cap "
                   f"was reached (TRACE_MAX)")
    if "nospace" in counts:
        mb = counts["nospace"][1] // (1024 * 1024)
        out.append(f"capture stopped: free space fell below {mb} MB "
                   f"(TRACE_MIN_FREE_MB)")
    if "write_error" in counts:
        out.append(f"capture stopped: writing a trace segment failed "
                   f"(errno {counts['write_error'][1]}) — the report is "
                   f"missing everything after that point")
    if "truncated" in counts:
        out.append(f"{counts['truncated'][0]} segment(s) end in a partial "
                   f"record: the process was killed, or a write failed "
                   f"partway — those threads' final events are missing")
    if "wrap" in counts:
        n, lost = counts["wrap"]
        out.append(f"TRACE_FULL=wrap discarded {n} earlier segment(s), about "
                   f"{lost} events: this report covers the END of the run, "
                   f"and unmatched exits below are expected")
    if any(m["flags"] & HF_WRAPPED for m in metas) and "wrap" not in counts:
        out.append("this capture rotated: earlier segments were discarded")
    if any(m.get("big_endian") for m in metas):
        out.append("recorded by a big-endian agent; byte-swapped on read")
    return out


def collect(tracedir, exe, folded=False, callers=False, addr2line=None,
            subtract_overhead=False):
    """Analyze all trace files in tracedir; return structured report data.

    Dict with summary counters, a 'rows' list (one per function: calls,
    incl/self/max ms, percentiles, resolved name/location) and 'per_thread'
    timing. With folded=True it also carries 'folded': [(path, self_ns)]
    collapsed-stack rows, highest self time first; with callers=True a
    'call_sites' list."""
    files = _event_files(tracedir)
    summaries = _summary_files(tracedir)
    if not files and not summaries:
        raise RuntimeError(f"no trace files in {tracedir}")
    if summaries and (folded or callers):
        raise RuntimeError(
            "summary traces record per-function totals, not call paths — "
            "re-record without TRACE_MODE=summary for this output")

    if summaries:
        return _collect_summary(summaries, exe, addr2line, subtract_overhead)

    metas = _open_metas(files)
    if not metas:
        raise RuntimeError(f"no readable trace files in {tracedir}")

    # One accumulator across all files: in streaming mode a thread's events
    # can be split over several trace.stream.*.bin files.
    acc = Accumulator(folded=folded, callers=callers)
    notices = []
    for meta in metas:
        for ev in read_events(meta["path"], meta, notices):
            acc.feed(*ev)
    if acc.events == 0:
        raise RuntimeError("trace files contained no events")

    wanted = set(acc.addrs)
    if acc.edges is not None:
        wanted |= {site for _f, site in acc.edges}
        wanted |= {e[2] for e in acc.edges.values() if e[2]}
    names = resolve(wanted, exe, addr2line)
    _warn_if_unresolved(names, exe, metas)
    stats, threads, unmatched, open_frames = acc.finish()

    hook_ns = max((m["hook_ns"] for m in metas), default=0)
    span_ns = max(t[1] for t in threads.values()) - min(t[0] for t in threads.values())
    rows = []
    for func, (calls, incl, self_t, max_t) in stats.items():
        fn, loc = names.get(func, ("??", "??:0"))
        hist = acc.hist[func]
        if subtract_overhead and hook_ns:
            incl = max(0, incl - (calls + 2 * acc.desc_calls[func]) * hook_ns)
            self_t = max(0, self_t - (calls + 2 * acc.child_calls[func]) * hook_ns)
        rows.append({"function": fn, "location": loc, "calls": calls,
                     "incl_ms": incl / 1e6, "self_ms": self_t / 1e6,
                     "max_ms": max_t / 1e6,
                     "min_ns": acc.min_t.get(func, 0), "max_ns": max_t,
                     "p50_ns": percentile(hist, calls, 0.50),
                     "p90_ns": percentile(hist, calls, 0.90),
                     "p99_ns": percentile(hist, calls, 0.99)})
    per_thread = [{"tid": tid, "events": acc.per_tid[tid],
                   "span_ms": (threads[tid][1] - threads[tid][0]) / 1e6}
                  for tid in sorted(acc.per_tid)]
    data = {"events": acc.events, "threads": len(threads),
            "functions": len(stats), "span_ms": span_ns / 1e6,
            "unmatched_exits": unmatched, "unclosed_enters": open_frames,
            "mode": "events", "hook_ns": hook_ns,
            "overhead_subtracted": bool(subtract_overhead and hook_ns),
            "notices": _describe_notices(notices, metas),
            "pids": sorted({m["pid"] for m in metas if m["pid"]}),
            "rows": rows, "per_thread": per_thread}
    if folded:
        data["folded"] = sorted(
            ((";".join(names.get(a, ("??", ""))[0] for a in path), ns)
             for path, ns in acc.folded.items()),
            key=lambda r: r[1], reverse=True)
    if callers:
        sites = []
        for (func, site), (n, total, parent) in acc.edges.items():
            fname = names.get(func, ("??", "??:0"))[0]
            sloc = names.get(site, ("??", "??:0"))[1]
            pname = names.get(parent, ("??", ""))[0] if parent else "(entry)"
            sites.append({"function": fname, "caller": pname,
                          "call_site": sloc, "calls": n,
                          "incl_ms": total / 1e6})
        data["call_sites"] = sorted(sites, key=lambda r: r["incl_ms"],
                                    reverse=True)
    return data


def _collect_summary(files, exe, addr2line, subtract_overhead):
    """Merge the per-thread totals written by TRACE_MODE=summary."""
    merged = {}
    metas = []
    truncated = 0
    span = 0
    for path in files:
        meta, records = read_summary(path)
        if meta is None:
            continue
        metas.append(meta)
        truncated += meta["truncated"]
        span = max(span, meta["span"])
        for rec in records:
            cur = merged.get(rec["func"])
            if cur is None:
                merged[rec["func"]] = rec
                continue
            cur["calls"] += rec["calls"]
            cur["incl"] += rec["incl"]
            cur["self"] += rec["self"]
            cur["max"] = max(cur["max"], rec["max"])
            cur["min"] = min(cur["min"] or rec["min"], rec["min"])
            cur["hist"] = [a + b for a, b in zip(cur["hist"], rec["hist"])]
    if not merged:
        raise RuntimeError("summary files contained no records")

    names = resolve(set(merged), exe, addr2line)
    _warn_if_unresolved(names, exe, metas)
    hook_ns = max((m["hook_ns"] for m in metas), default=0)

    rows = []
    calls_total = 0
    for func, rec in merged.items():
        fn, loc = names.get(func, ("??", "??:0"))
        calls_total += rec["calls"]
        incl, self_t = rec["incl"], rec["self"]
        if subtract_overhead and hook_ns:
            incl = max(0, incl - rec["calls"] * hook_ns)
            self_t = max(0, self_t - rec["calls"] * hook_ns)
        scale = rec["scale"]
        rows.append({"function": fn, "location": loc, "calls": rec["calls"],
                     "incl_ms": incl / 1e6, "self_ms": self_t / 1e6,
                     "max_ms": rec["max"] / 1e6,
                     "min_ns": rec["min"], "max_ns": rec["max"],
                     "p50_ns": int(percentile(rec["hist"], rec["calls"], 0.50) * scale),
                     "p90_ns": int(percentile(rec["hist"], rec["calls"], 0.90) * scale),
                     "p99_ns": int(percentile(rec["hist"], rec["calls"], 0.99) * scale)})
    notices = []
    if truncated:
        notices.append(f"{truncated} calls were nested deeper than the "
                       f"shadow stack and are not counted")
    if any(m.get("big_endian") for m in metas):
        notices.append("recorded by a big-endian agent; byte-swapped on read")
    per_thread = [{"tid": m["tid"], "events": 0, "span_ms": m["span"] / 1e6}
                  for m in sorted(metas, key=lambda m: m["tid"])]
    return {"events": calls_total * 2, "threads": len(metas),
            "functions": len(rows), "span_ms": span / 1e6,
            "unmatched_exits": 0, "unclosed_enters": 0,
            "mode": "summary", "hook_ns": hook_ns,
            "overhead_subtracted": bool(subtract_overhead and hook_ns),
            "notices": notices,
            "pids": sorted({m["pid"] for m in metas if m["pid"]}),
            "rows": rows, "per_thread": per_thread}


# --- Output ----------------------------------------------------------------

def _version():
    try:
        from callsight import __version__
        return __version__
    except Exception:
        return "unknown"


def _dur(ns):
    """Per-call durations span nanoseconds to seconds; a fixed ms column
    turns most of them into 0.000."""
    if ns >= 1e9:
        return f"{ns / 1e9:.2f}s"
    if ns >= 1e6:
        return f"{ns / 1e6:.2f}ms"
    if ns >= 1e3:
        return f"{ns / 1e3:.2f}us"
    return f"{ns:.0f}ns"


def print_text(data, top):
    print(f"events={data['events']} threads={data['threads']} "
          f"functions={data['functions']} span={data['span_ms']:.1f}ms "
          f"unmatched_exits={data['unmatched_exits']} "
          f"unclosed_enters={data['unclosed_enters']}"
          + (f" mode={data['mode']}" if data.get("mode") == "summary" else ""))
    for note in data.get("notices", []):
        print(f"! {note}")
    if data.get("overhead_subtracted"):
        print(f"  (times corrected for {data['hook_ns']} ns of measured "
              f"hook overhead per call)")
    print()

    hdr = (f"{'calls':>10} {'incl_ms':>12} {'self_ms':>12} "
           f"{'p50':>9} {'p99':>9} {'max':>9}  function (first location)")
    for label, key in (("TOP BY SELF TIME", "self_ms"),
                       ("TOP BY INCLUSIVE TIME", "incl_ms")):
        print(f"== {label} ==")
        print(hdr)
        rows = sorted(data["rows"], key=lambda r: r[key], reverse=True)[:top]
        for r in rows:
            print(f"{r['calls']:>10} {r['incl_ms']:>12.3f} {r['self_ms']:>12.3f} "
                  f"{_dur(r.get('p50_ns', 0)):>9} {_dur(r.get('p99_ns', 0)):>9} "
                  f"{_dur(r.get('max_ns', 0)):>9}  "
                  f"{r['function']} ({r['location']})")
        print()

    print("== PER-THREAD SUMMARY ==")
    print(f"{'tid':>8} {'events':>10} {'span_ms':>12}")
    for t in data["per_thread"]:
        print(f"{t['tid']:>8} {t['events']:>10} {t['span_ms']:>12.3f}")


def print_json(data, top):
    out = dict(data)
    out["tool"] = "callsight"
    out["version"] = _version()
    out["rows"] = sorted(data["rows"], key=lambda r: r["self_ms"], reverse=True)
    if top > 0:
        out["rows"] = out["rows"][:top]
    json.dump(out, sys.stdout, indent=2)
    print()


def print_folded(data):
    """Collapsed stacks: '<caller>;<callee> <self_ns>', one per call path.

    The input format of flamegraph.pl and speedscope; values are nanoseconds
    of self time."""
    for path, ns in data["folded"]:
        print(f"{path} {ns}")


def print_callers(data, top):
    """Which call sites of a hot function actually cost.

    caller_addr is a return address, so it resolves to the exact line that
    made the call — the thing a sampling profiler can only estimate."""
    print(f"{'calls':>10} {'incl_ms':>12}  callee <- call site")
    rows = data["call_sites"][:top] if top > 0 else data["call_sites"]
    for r in rows:
        print(f"{r['calls']:>10} {r['incl_ms']:>12.3f}  {r['function']} "
              f"<- {r['caller']} ({r['call_site']})")


def emit_chrome(tracedir, exe, addr2line=None, out=None):
    """Stream a Chrome/Perfetto trace: a real timeline with true nesting.

    Two passes over the files — one to learn which addresses need symbols,
    one to emit — so memory stays flat no matter how long the trace is."""
    # Resolved per call, not as a default: a default would bind the stdout
    # that existed at import time and ignore any later redirection.
    out = sys.stdout if out is None else out
    files = _event_files(tracedir)
    if not files:
        raise RuntimeError(f"no trace files in {tracedir}")
    metas = _open_metas(files)
    if not metas:
        raise RuntimeError(f"no readable trace files in {tracedir}")

    addrs = set()
    origin = None
    for meta in metas:
        for _tid, _kind, func, ts, _caller in read_events(meta["path"], meta):
            addrs.add(func)
            if origin is None or ts < origin:
                origin = ts
    names = resolve(addrs, exe, addr2line)
    _warn_if_unresolved(names, exe, metas)
    origin = origin or 0

    out.write('{"displayTimeUnit":"ms","traceEvents":[\n')
    state = {"n": 0}

    def on_complete(tid, func, start_ns, dur_ns, _depth):
        name = names.get(func, ("??", ""))[0]
        # Rebased to the start of the trace: absolute CLOCK_MONOTONIC values
        # put every span days into the timeline in a viewer.
        rec = {"name": name, "ph": "X", "pid": 1, "tid": tid,
               "ts": (start_ns - origin) / 1000.0, "dur": dur_ns / 1000.0}
        out.write(("," if state["n"] else "") + json.dumps(rec) + "\n")
        state["n"] += 1

    acc = Accumulator(on_complete=on_complete)
    for meta in metas:
        for ev in read_events(meta["path"], meta):
            acc.feed(*ev)
    out.write("]}\n")
    return state["n"]


def diff(base_path, new_path, key="self_ms", threshold=0.0):
    """Compare two --format json reports function by function.

    Returns (rows, worst_regression_pct). Exact call counts make this a real
    comparison rather than two samples that happened to land differently."""
    with open(base_path) as f:
        base = json.load(f)
    with open(new_path) as f:
        new = json.load(f)
    old_rows = {r["function"]: r for r in base.get("rows", [])}
    new_rows = {r["function"]: r for r in new.get("rows", [])}

    rows = []
    worst = 0.0
    for name in sorted(set(old_rows) | set(new_rows)):
        a = old_rows.get(name)
        b = new_rows.get(name)
        old_v = a[key] if a else 0.0
        new_v = b[key] if b else 0.0
        if old_v > 0:
            pct = (new_v - old_v) / old_v * 100.0
        else:
            pct = float("inf") if new_v > 0 else 0.0
        if abs(new_v - old_v) >= threshold:
            rows.append({"function": name, "base": old_v, "new": new_v,
                         "delta": new_v - old_v, "pct": pct,
                         "base_calls": a["calls"] if a else 0,
                         "new_calls": b["calls"] if b else 0})
        if pct != float("inf") and pct > worst and old_v > 0:
            worst = pct
    rows.sort(key=lambda r: r["delta"], reverse=True)
    return rows, worst


def print_diff(rows, key):
    print(f"{'base_' + key:>12} {'new_' + key:>12} {'delta':>12} "
          f"{'change':>9}  function")
    for r in rows:
        pct = "new" if r["base"] == 0 else (
            "gone" if r["new"] == 0 else f"{r['pct']:+.1f}%")
        print(f"{r['base']:>12.3f} {r['new']:>12.3f} {r['delta']:>+12.3f} "
              f"{pct:>9}  {r['function']}")


def report(tracedir, exe, top, fmt="text", addr2line=None,
           subtract_overhead=False):
    """Analyze all trace files in tracedir and print the report.

    Returns the unmatched_exits count (0 means a clean trace)."""
    exe = find_exe(exe)
    if fmt == "chrome":
        emit_chrome(tracedir, exe, addr2line)
        return 0

    data = collect(tracedir, exe, folded=(fmt == "folded"),
                   callers=(fmt == "callers"), addr2line=addr2line,
                   subtract_overhead=subtract_overhead)

    if fmt == "json":
        print_json(data, top)
    elif fmt == "folded":
        print_folded(data)
    elif fmt == "callers":
        print_callers(data, top)
    else:
        print_text(data, top)

    return data["unmatched_exits"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tracedir", nargs="?", default="traces",
                    help="directory with trace.<pid>.<tid>.<seq>.bin files")
    ap.add_argument("--exe", default=None,
                    help="instrumented binary for addr2line "
                         "(default: the single *.instr under ./bin or .)")
    ap.add_argument("--top", type=int, default=20,
                    help="rows per table (json: 0 means all rows)")
    ap.add_argument("--format", choices=("text", "json", "folded", "chrome",
                                         "callers"),
                    default="text",
                    help="text tables (default), json for tooling, folded "
                         "collapsed stacks for flamegraph.pl and speedscope, "
                         "chrome for ui.perfetto.dev, or callers for hot "
                         "call sites")
    ap.add_argument("--addr2line", default=None,
                    help="addr2line to use (default: $CALLSIGHT_ADDR2LINE or "
                         "the host one); cross-compiled binaries need their "
                         "own toolchain's copy")
    ap.add_argument("--subtract-overhead", action="store_true",
                    help="deduct the runtime's measured per-hook cost from "
                         "reported times")
    args = ap.parse_args(argv)
    try:
        report(args.tracedir, args.exe, args.top, args.format,
               args.addr2line, args.subtract_overhead)
    except RuntimeError as e:
        sys.exit(str(e))
    except BrokenPipeError:
        # `callsight analyze --format folded | head` and friends. Redirect
        # stdout to devnull so the interpreter's shutdown flush cannot raise
        # a second time (see the note in the Python stdlib docs on SIGPIPE).
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)


if __name__ == "__main__":
    main()
