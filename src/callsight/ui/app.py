"""FastAPI app for the callsight web UI.

Endpoints operate on a project directory on the server's filesystem:
browse for a folder, edit its trace.config, preview the selection, build
the instrumented profile, run it with tracing enabled, and analyze the
resulting traces — all through the same code as the CLI.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from callsight import analyze, cli, flags, provision, symbols

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="callsight", docs_url=None, redoc_url=None)


def project_path(raw):
    """Resolve and validate a user-supplied project directory."""
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(404, f"{p}: not a directory")
    return p


def run_cmd(cmd, cwd, timeout, env=None):
    """Run a command, return (ok, combined_output)."""
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                              capture_output=True, text=True)
        return proc.returncode == 0, (proc.stdout + proc.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout}s"
    except OSError as e:
        return False, str(e)


def callsight_cmd():
    """How to invoke the callsight CLI from build integrations."""
    exe = shutil.which("callsight")
    return [exe] if exe else [sys.executable, "-m", "callsight.cli"]


def cmake_cmd():
    """cmake if available, else an ephemeral copy via uvx (no root needed)."""
    return ["cmake"] if shutil.which("cmake") else ["uvx", "cmake"]


def find_instr_binary(project):
    """Locate the single instrumented binary in common build dirs."""
    found = []
    for sub in ("bin", "build-instr", "build", "."):
        d = project / sub
        if d.is_dir():
            found.extend(p for p in d.iterdir()
                         if p.is_file() and os.access(p, os.X_OK)
                         and (p.name.endswith(".instr")
                              or sub == "build-instr" and "." not in p.name))
    return found


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/browse")
def browse(path: str = "/"):
    p = project_path(path)
    entries = []
    try:
        for child in sorted(p.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                entries.append({
                    "name": child.name,
                    "has_makefile": (child / "Makefile").exists(),
                    "has_cmake": (child / "CMakeLists.txt").exists(),
                })
    except OSError as e:
        raise HTTPException(400, str(e))
    return {"path": str(p), "parent": str(p.parent), "entries": entries}


@app.get("/api/project")
def project_info(path: str):
    p = project_path(path)
    sources = flags.scan_sources(p)
    build = "cmake" if (p / "CMakeLists.txt").exists() else "make"
    binaries = [str(b.relative_to(p)) for b in find_instr_binary(p)]
    return {"path": str(p), "sources": len(sources), "build": build,
            "has_config": (p / "trace.config").exists(),
            "has_callsight_dir": (p / "callsight").is_dir(),
            "binaries": binaries,
            "has_traces": any((p / "traces").glob("trace.*.bin"))
            if (p / "traces").is_dir() else False}


@app.get("/api/config")
def get_config(path: str):
    p = project_path(path) / "trace.config"
    if not p.exists():
        return {"exists": False, "content": cli.CONFIG_TEMPLATE}
    try:
        content = p.read_text()
    except UnicodeDecodeError:
        raise HTTPException(400, f"{p}: not a UTF-8 text file")
    return {"exists": True, "content": content}


class ConfigBody(BaseModel):
    path: str
    content: str


@app.post("/api/config")
def save_config(body: ConfigBody):
    p = project_path(body.path) / "trace.config"
    p.write_text(body.content)
    return {"ok": True}


@app.get("/api/functions")
def list_functions(path: str):
    """Function definitions grouped by file, for the config builder."""
    p = project_path(path)
    # Auto-provision: first scan downloads the bundled static ctags when
    # the system has none; failures fall back to the regex parser.
    ctags = provision.ensure_ctags()
    result = symbols.list_functions(str(p))
    entries = result["functions"]
    files, by_file = [], {}
    for e in entries:
        grp = by_file.get(e["file"])
        if grp is None:
            grp = {"file": e["file"], "functions": []}
            by_file[e["file"]] = grp
            files.append(grp)
        grp["functions"].append({"name": e["name"], "line": e["line"],
                                 "static": e["static"]})
    source = None
    if ctags:
        source = "bundled" if os.path.abspath(ctags) == \
            os.path.abspath(provision.bundled_ctags()) else "path"
    return {"files": files, "total_files": len(files),
            "total_functions": len(entries),
            "backend": result["backend"],
            "ctags": {"source": source}}


class IncludeFunc(BaseModel):
    name: str
    depth: Optional[int] = Field(None, ge=0)


class GenerateBody(BaseModel):
    path: str
    excluded_files: list[str] = []
    include_funcs: list[IncludeFunc] = []
    excluded_funcs: list[str] = []
    counter_events: list[str] = []
    counter_funcs: list[IncludeFunc] = []
    counter_files: list[str] = []
    counter_min: Optional[str] = None


@app.post("/api/config/generate")
def generate_config(body: GenerateBody):
    """Render trace.config text from config-builder selections.

    Returns the text only; writing happens via POST /api/config."""
    project_path(body.path)
    cmin = body.counter_min
    if cmin is not None and cmin != "auto":
        try:
            cmin = int(cmin)
        except ValueError:
            raise HTTPException(400, f"counter-min: want 'auto' or a number "
                                     f"of nanoseconds, got {cmin!r}")
    try:
        content = flags.render_config(
            excluded_files=body.excluded_files,
            include_funcs=[(f.name, f.depth) for f in body.include_funcs],
            excluded_funcs=body.excluded_funcs,
            counter_events=body.counter_events,
            counter_funcs=[(f.name, f.depth) for f in body.counter_funcs],
            counter_files=body.counter_files,
            counter_min=cmin)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"content": content}


@app.get("/api/counters/probe")
def counters_probe(event: str = "instructions"):
    """What hardware counters can actually do on this machine, right now.

    The UI shows this before offering to count anything. perf_event_open
    succeeding proves nothing — inside a container the event opens, reads,
    and returns zero forever — so this runs a known loop and checks the
    counter really moved. Without it the Count button would cheerfully
    produce a table of zeros.
    """
    from callsight import counters
    p = counters.probe(event)
    p["summary"] = counters.describe_probe(p)
    p["events"] = sorted(flags.COUNTER_EVENTS)
    p["max_events"] = flags.COUNTER_MAX_EVENTS
    return p


class CounterPreviewBody(BaseModel):
    path: str
    binary: str
    counter_events: list[str] = []
    counter_funcs: list[IncludeFunc] = []
    counter_files: list[str] = []
    counter_min: Optional[str] = None
    excluded_files: list[str] = []
    include_funcs: list[IncludeFunc] = []
    excluded_funcs: list[str] = []


@app.post("/api/counters/preview")
def counters_preview(body: CounterPreviewBody):
    """Dry-run a counter selection against the built binary.

    Answers the two questions worth answering before a run rather than
    after: which of these names actually resolve to countable functions, and
    what will counting them cost. The cost is knowable exactly — two reads
    per counted call — so it comes from the previous run's call counts.
    """
    from callsight import counters

    p = project_path(body.path)
    binary = (p / body.binary).resolve()
    if not binary.is_file() or p not in binary.parents:
        raise HTTPException(404, f"{body.binary}: not found under project")

    text = flags.render_config(
        excluded_files=body.excluded_files,
        include_funcs=[(f.name, f.depth) for f in body.include_funcs],
        excluded_funcs=body.excluded_funcs,
        counter_events=body.counter_events,
        counter_funcs=[(f.name, f.depth) for f in body.counter_funcs],
        counter_files=body.counter_files)
    tmp = p / ".callsight-preview.config"
    try:
        tmp.write_text(text)
        spec = flags.parse_config(str(tmp))
        sel = counters.resolve_selection(spec, binary, flags.scan_sources(p),
                                         p)
    except SystemExit as e:
        raise HTTPException(400, str(e))
    except (OSError, ValueError) as e:
        raise HTTPException(400, str(e))
    finally:
        tmp.unlink(missing_ok=True)

    probe = counters.probe(body.counter_events[0]
                           if body.counter_events else "instructions")
    read_ns = probe.get("read_ns") or 0

    # Cost projection from the last analyzed run, when there is one.
    rows, added_ns, span_ms = [], 0, 0.0
    try:
        data = analyze.collect(p / "traces", str(binary))
        rows = data["rows"]
        span_ms = data.get("span_ms", 0.0)
        added_ns = counters.estimate_overhead_ns(sel, rows, read_ns)
    except (RuntimeError, OSError):
        pass

    durations = {r["function"]: r.get("p50_ns", 0) for r in rows}
    floor_ns = probe.get("floor_ns") or 0
    chosen = [n for _a, n in sel.entries]
    return {
        "available": probe["available"],
        "reason": probe["reason"],
        "read_ns": read_ns,
        "floor_ns": floor_ns,
        "counted": chosen,
        "unresolved": sel.unresolved,
        "not_instrumented": sel.not_instrumented,
        "warnings": sel.warnings,
        # Named individually so the UI can grey them out with the reason,
        # rather than the run silently declining to count them later.
        "too_short": [n for n in chosen
                      if floor_ns and 0 < durations.get(n, 0) < floor_ns],
        "durations": {n: durations.get(n, 0) for n in chosen},
        "added_ms": added_ns / 1e6,
        "span_ms": span_ms,
    }


@app.get("/api/subtree")
def subtree(path: str, function: str,
            depth: Optional[int] = Query(None, ge=0)):
    """Resolve a function's call subtree for the config editor."""
    from callsight import callgraph
    p = project_path(path)
    sources = flags.scan_sources(p)
    graph = callgraph.build_graph(sources)
    if function not in graph:
        raise HTTPException(404, f"'{function}' not defined under {p}")
    sub = callgraph.expand(graph, [function], depth)
    files = sorted({f for fn in sub for f in graph[fn]["files"]})
    line = f"include-func {function}" + (f" {depth}" if depth is not None
                                         else "")
    return {"functions": sorted(sub), "files": files, "config_line": line}


@app.get("/api/scan")
def scan(path: str):
    p = project_path(path)
    config = p / "trace.config"
    if not config.exists():
        raise HTTPException(400, "no trace.config — save one first")
    sources = flags.scan_sources(p)
    try:
        spec = flags.parse_config(config)
        includes, excludes, include_funcs = (spec.includes, spec.excludes,
                                             spec.include_funcs)
        if include_funcs:
            selected, dropped, auto_funcs, reachable, warnings = \
                flags.function_selection(include_funcs, sources, includes,
                                         excludes)
            return {"total": len(sources), "instrumented": len(selected),
                    "excluded": dropped, "subtree": sorted(reachable),
                    "warnings": warnings}
    except SystemExit as e:
        raise HTTPException(400, str(e))
    except UnicodeDecodeError:
        raise HTTPException(400, f"{config}: not a UTF-8 text file")
    selected, dropped = flags.select(sources, includes, excludes)
    return {"total": len(sources), "instrumented": len(selected),
            "excluded": dropped}


class BuildBody(BaseModel):
    path: str
    callsight: str = ""   # override for the CALLSIGHT make variable
    timeout: int = Field(300, ge=1, le=3600)


@app.post("/api/build")
def build(body: BuildBody):
    p = project_path(body.path)
    tk = body.callsight.split() if body.callsight else callsight_cmd()
    logs = []
    if (p / "Makefile").exists():
        ok, out = run_cmd(["make", "instrument",
                           f"CALLSIGHT={' '.join(tk)}"], p, body.timeout)
        logs.append(f"$ make instrument\n{out}")
    elif (p / "CMakeLists.txt").exists():
        cmake = cmake_cmd()
        ok1, out1 = run_cmd(
            cmake + ["-DCALLSIGHT_INSTRUMENT=ON",
                     f"-DCALLSIGHT_COMMAND={';'.join(tk)}", "-B",
                     "build-instr"], p, body.timeout)
        logs.append(f"$ {' '.join(cmake)} configure\n{out1}")
        ok = ok1
        if ok1:
            ok, out2 = run_cmd(cmake + ["--build", "build-instr"], p,
                               body.timeout)
            logs.append(f"$ {' '.join(cmake)} --build\n{out2}")
    else:
        raise HTTPException(400, "no Makefile or CMakeLists.txt in project")
    return {"ok": ok, "log": "\n\n".join(logs),
            "binaries": [str(b.relative_to(p)) for b in find_instr_binary(p)]}


class RunBody(BaseModel):
    path: str
    binary: str
    trace_max: int = Field(1000000, ge=0)
    timeout: int = Field(30, ge=1, le=3600)


@app.post("/api/run")
def run(body: RunBody):
    p = project_path(body.path)
    binary = (p / body.binary).resolve()
    if not binary.is_file() or p not in binary.parents:
        raise HTTPException(404, f"{body.binary}: not found under project")
    env = dict(os.environ, TRACE_ENABLE="1", TRACE_MAX=str(body.trace_max),
               TRACE_DIR=str(p / "traces"))
    ok, out = run_cmd([str(binary)], p, body.timeout, env=env)
    n = len(list((p / "traces").glob("trace.*.bin"))) \
        if (p / "traces").is_dir() else 0
    # A killed-by-timeout run is fine: the cap bounds the event count.
    return {"ok": ok, "output": out, "trace_files": n}


@app.get("/api/analyze")
def analyze_traces(path: str, binary: str, top: int = Query(50, ge=0)):
    """Hotspot report; rows are the `top` hottest by self time.

    collect() returns rows in completion order, so they must be sorted
    before truncating — otherwise 'top' would be an arbitrary slice of the
    functions rather than the hot ones."""
    p = project_path(path)
    binary = (p / binary).resolve()
    if not binary.is_file() or p not in binary.parents:
        raise HTTPException(404, f"{binary}: not found under project")
    try:
        data = analyze.collect(p / "traces", str(binary))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    data["rows"] = sorted(data["rows"], key=lambda r: r["self_ms"],
                          reverse=True)
    if top:
        data["rows"] = data["rows"][:top]
    return data


class LiveSession:
    """A report that grows while the program is still running.

    One persistent Accumulator plus a byte offset per trace file. Each tick
    reads only what the files have gained, so the cost is proportional to
    *new* events rather than to the whole trace — which is what makes a
    once-a-second refresh viable on a capture heading for millions of events.

    Two sources, one pipeline: a binary this server launched, or a directory
    `callsight serve` is filling from a remote device. Neither knows it is
    being watched; both are just trace files appearing on disk.
    """

    def __init__(self, tracedir, exe, proc=None, addr2line=None):
        self.tracedir = Path(tracedir)
        self.exe = str(exe)
        self.proc = proc
        self.addr2line = addr2line
        self.acc = analyze.Accumulator()
        self.offsets = {}          # path -> bytes consumed
        self.metas = {}            # path -> header meta, parsed once
        self.names = {}            # resolved symbols, cached across ticks
        self.notices = []
        self.started = time.time()
        self.last_events = 0
        self.rate = 0.0
        self.error = None

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        """SIGTERM, not SIGKILL: a clean exit flushes each thread's buffered
        tail, and that tail is usually the interesting part."""
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def tick(self):
        """Consume whatever is new and return the current report."""
        for path in sorted(self.tracedir.glob("trace.*.bin")):
            key = str(path)
            meta = self.metas.get(key)
            if meta is None:
                meta = analyze.read_header(path)
                if meta is None:
                    self.metas[key] = False     # remember not to retry
                    continue
                if meta["flags"] & analyze.HF_TICKS:
                    meta["anchor"] = None
                self.metas[key] = meta
            if meta is False:
                continue
            try:
                start = self.offsets.get(key, 0)
                if path.stat().st_size <= max(start, meta["header_size"]):
                    continue
                end = [start]
                for ev in analyze.read_events(path, meta, self.notices,
                                              start_offset=start,
                                              offset_out=end):
                    self.acc.feed(*ev)
                self.offsets[key] = end[0]
            except (OSError, RuntimeError) as e:
                self.error = str(e)
        return self.report()

    def report(self):
        elapsed = time.time() - self.started
        # finish() only reads the accumulator's state, so calling it once a
        # second on a still-growing capture is fine.
        stats, threads, unmatched, open_frames = self.acc.finish()

        new = {a for a in self.acc.addrs if a not in self.names}
        if new:
            try:
                self.names.update(analyze.resolve(new, self.exe,
                                                  self.addr2line))
            except RuntimeError as e:
                self.error = str(e)
                self.names.update({a: ("??", "??:0") for a in new})

        events = [(t, c) for m in self.metas.values()
                  if m and m.get("counters") for t, c in m["counters"]
                  if t is not None]
        event_names = [analyze.counter_event_name(t, c) for t, c in events]

        rows = []
        for func, (calls, incl, self_t, max_t) in stats.items():
            fn, loc = self.names.get(func, ("??", "??:0"))
            counted = self.acc.counter_calls.get(func, 0)
            row = {"function": fn, "location": loc, "calls": calls,
                   "incl_ms": incl / 1e6, "self_ms": self_t / 1e6,
                   "max_ns": max_t}
            if counted and event_names:
                row["counters"] = {
                    name: {"calls": counted,
                           "total": self.acc.counter_incl[func][i],
                           "per_call": self.acc.counter_incl[func][i] / counted}
                    for i, name in enumerate(event_names)}
            rows.append(row)
        rows.sort(key=lambda r: r["self_ms"], reverse=True)

        total = self.acc.events
        self.rate = (total - self.last_events)
        self.last_events = total
        return {"events": total, "rate": self.rate, "threads": len(threads),
                "functions": len(stats), "elapsed_s": elapsed,
                "unmatched_exits": unmatched, "open_frames": open_frames,
                "running": self.running, "error": self.error,
                "counter_events": event_names, "rows": rows[:60]}


_live = {"session": None}


class LiveStartBody(BaseModel):
    path: str
    binary: str
    mode: str = "events"
    trace_max: int = Field(5000000, ge=0)


@app.post("/api/live/start")
def live_start(body: LiveStartBody):
    """Launch a binary and watch its trace grow. Non-blocking, unlike /api/run."""
    from callsight import counters

    p = project_path(body.path)
    binary = (p / body.binary).resolve()
    if not binary.is_file() or p not in binary.parents:
        raise HTTPException(404, f"{body.binary}: not found under project")
    live_stop()

    tracedir = p / "traces"
    tracedir.mkdir(exist_ok=True)
    for stale in tracedir.glob("trace.*.bin"):
        stale.unlink()

    env = dict(os.environ, TRACE_ENABLE="1", TRACE_MODE=body.mode,
               TRACE_MAX=str(body.trace_max), TRACE_DIR=str(tracedir))
    # Same rule as `callsight run`: regenerate the counter map so it can
    # never describe a previous build.
    config = p / "trace.config"
    if config.exists():
        try:
            spec = flags.parse_config(str(config))
            if spec.counter_events or spec.counter_funcs or spec.counter_files:
                sel = counters.resolve_selection(spec, binary,
                                                 flags.scan_sources(p), p)
                counters.write_map(counters.map_path(tracedir), sel, binary)
                env["TRACE_COUNTERS"] = counters.map_path(tracedir)
        except (SystemExit, OSError, ValueError):
            pass          # counters are optional; the run is not
    try:
        proc = subprocess.Popen([str(binary)], cwd=p, env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except OSError as e:
        raise HTTPException(400, str(e))
    _live["session"] = LiveSession(tracedir, binary, proc)
    return {"ok": True, "watching": str(tracedir)}


class LiveWatchBody(BaseModel):
    path: str
    binary: str
    dir: str = "traces"


@app.post("/api/live/watch")
def live_watch(body: LiveWatchBody):
    """Watch a directory someone else is filling — `callsight serve`
    receiving from a remote device, most usefully."""
    p = project_path(body.path)
    binary = (p / body.binary).resolve()
    if not binary.is_file() or p not in binary.parents:
        raise HTTPException(404, f"{body.binary}: not found under project")
    tracedir = Path(body.dir)
    if not tracedir.is_absolute():
        tracedir = p / tracedir
    if not tracedir.is_dir():
        raise HTTPException(404, f"{tracedir}: not a directory")
    live_stop()
    _live["session"] = LiveSession(tracedir, binary)
    return {"ok": True, "watching": str(tracedir)}


@app.post("/api/live/stop")
def live_stop():
    s = _live.get("session")
    if s is not None:
        s.stop()
    return {"ok": True}


@app.get("/api/live/events")
def live_events():
    """Server-sent events: one report per second while anything is happening.

    SSE rather than polling because the browser reconnects on its own and the
    server decides the cadence — and because the interesting case is a device
    streaming in, where the arrival rate is not ours to predict.
    """
    s = _live.get("session")
    if s is None:
        raise HTTPException(400, "nothing is being watched — start a run "
                                 "or point at a serve directory first")

    def stream():
        idle = 0
        while True:
            try:
                payload = s.tick()
            except Exception as e:                  # never kill the stream
                payload = {"error": str(e), "rows": [], "running": False}
            yield f"data: {json.dumps(payload)}\n\n"
            if not payload.get("running") and payload.get("rate", 0) == 0:
                idle += 1
                # A finished run still gets a few ticks: the last flush can
                # land after the process is gone.
                if idle > 3:
                    break
            else:
                idle = 0
            time.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/flame")
def flame(path: str, binary: str, top: int = Query(4000, ge=1)):
    """Collapsed stacks for the flame graph.

    Capped: a deep trace can have hundreds of thousands of distinct call
    paths, and anything past the widest few thousand is narrower than a
    pixel on any screen."""
    p = project_path(path)
    binary = (p / binary).resolve()
    if not binary.is_file() or p not in binary.parents:
        raise HTTPException(404, f"{binary}: not found under project")
    try:
        data = analyze.collect(p / "traces", str(binary), folded=True)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"folded": data["folded"][:top],
            "notices": data.get("notices", []),
            "paths": len(data["folded"])}
