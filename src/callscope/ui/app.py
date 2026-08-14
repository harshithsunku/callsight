"""FastAPI app for the callscope web UI.

Endpoints operate on a project directory on the server's filesystem:
browse for a folder, edit its trace.config, preview the selection, build
the instrumented profile, run it with tracing enabled, and analyze the
resulting traces — all through the same code as the CLI.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from callscope import analyze, cli, flags

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="callscope", docs_url=None, redoc_url=None)


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


def callscope_cmd():
    """How to invoke the callscope CLI from build integrations."""
    exe = shutil.which("callscope")
    return exe if exe else f"{sys.executable} -m callscope.cli"


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
    for child in sorted(p.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            entries.append({
                "name": child.name,
                "has_makefile": (child / "Makefile").exists(),
                "has_cmake": (child / "CMakeLists.txt").exists(),
            })
    return {"path": str(p), "parent": str(p.parent), "entries": entries}


@app.get("/api/project")
def project_info(path: str):
    p = project_path(path)
    sources = flags.scan_sources(p)
    build = "cmake" if (p / "CMakeLists.txt").exists() else "make"
    binaries = [str(b.relative_to(p)) for b in find_instr_binary(p)]
    return {"path": str(p), "sources": len(sources), "build": build,
            "has_config": (p / "trace.config").exists(),
            "has_callscope_dir": (p / "callscope").is_dir(),
            "binaries": binaries,
            "has_traces": any((p / "traces").glob("trace.*.bin"))
            if (p / "traces").is_dir() else False}


@app.get("/api/config")
def get_config(path: str):
    p = project_path(path) / "trace.config"
    if not p.exists():
        return {"exists": False, "content": cli.CONFIG_TEMPLATE}
    return {"exists": True, "content": p.read_text()}


class ConfigBody(BaseModel):
    path: str
    content: str


@app.post("/api/config")
def save_config(body: ConfigBody):
    p = project_path(body.path) / "trace.config"
    p.write_text(body.content)
    return {"ok": True}


@app.get("/api/scan")
def scan(path: str):
    p = project_path(path)
    config = p / "trace.config"
    if not config.exists():
        raise HTTPException(400, "no trace.config — save one first")
    sources = flags.scan_sources(p)
    includes, excludes, funcs = flags.parse_config(config)
    selected, dropped = flags.select(sources, includes, excludes)
    return {"total": len(sources), "instrumented": len(selected),
            "excluded": dropped}


class BuildBody(BaseModel):
    path: str
    callscope: str = ""   # override for the CALLSCOPE make variable
    timeout: int = 300


@app.post("/api/build")
def build(body: BuildBody):
    p = project_path(body.path)
    tk = body.callscope or callscope_cmd()
    logs = []
    if (p / "Makefile").exists():
        ok, out = run_cmd(["make", "instrument", f"CALLSCOPE={tk}"], p,
                          body.timeout)
        logs.append(f"$ make instrument\n{out}")
    elif (p / "CMakeLists.txt").exists():
        cmake = cmake_cmd()
        tk_list = tk.split(" ", 1)
        cmd = tk_list[0] if len(tk_list) == 1 else ";".join(tk_list)
        ok1, out1 = run_cmd(
            cmake + ["-DCALLSCOPE_INSTRUMENT=ON",
                     f"-DCALLSCOPE_COMMAND={cmd}", "-B", "build-instr"],
            p, body.timeout)
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
    trace_max: int = 1000000
    timeout: int = 30


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
def analyze_traces(path: str, binary: str, top: int = 50):
    p = project_path(path)
    binary = (p / binary).resolve()
    if not binary.is_file() or p not in binary.parents:
        raise HTTPException(404, f"{binary}: not found under project")
    try:
        data = analyze.collect(p / "traces", str(binary))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    data["rows"] = data["rows"][:top]
    return data
