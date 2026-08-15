"""Enumerate function definitions in a C/C++ project.

Powers the web UI's config builder: it needs the functions a project
defines (with file and line) to offer include-func/exclude-func
candidates. Two strategies:

  - ctags, when a ctags binary is available (on PATH, or the bundled
    static copy in $CALLSIGHT_HOME/bin — see provision.py) — accurate
    parsing, and static detection via the "file:" scope marker ctags
    emits for file-scope tags (builds without that marker report
    static=False).
  - the heuristic regex parser from callgraph.py as fallback — static is
    detected from a `static` keyword before the function name.

Any ctags failure (nonzero exit, unparseable output) falls back silently
to the regex path; the result is best-effort either way.
"""

import json
import os
import subprocess

from callsight import callgraph, flags, provision

# One ctags invocation covers the whole project; abort if it hangs.
_CTAGS_TIMEOUT = 60


def _ctags_definitions(ctags, sources):
    """Run ctags with JSON output over sources.

    Return [{"path", "name", "line", "static"}] entries, or None on any
    failure so the caller can fall back to the regex parser."""
    try:
        proc = subprocess.run(
            [ctags, "--output-format=json", "--sort=no",
             "--kinds-C=f", "--kinds-C++=f", "--fields=+n",
             "--extras=-q", "-f", "-"] + sources,
            capture_output=True, text=True, timeout=_CTAGS_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    entries = []
    try:
        for line in proc.stdout.splitlines():
            tag = json.loads(line)
            if tag.get("_type") != "tag":
                continue
            entries.append({"path": tag["path"], "name": tag["name"],
                            "line": int(tag["line"]),
                            "static": bool(tag.get("file"))})
    except (KeyError, TypeError, ValueError):
        return None
    return entries


def _regex_definitions(sources):
    """Fallback: callgraph.py's heuristic regex parser."""
    entries = []
    for path in sources:
        try:
            defs = callgraph.find_definitions(path)
        except OSError:
            continue
        for name, line, static in defs:
            entries.append({"path": path, "name": name, "line": line,
                            "static": static})
    return entries


def list_functions(project_dir):
    """One entry per function definition in the project:

        {"file": <path relative to project_dir>, "name": <str>,
         "line": <int>, "static": <bool>}

    sorted by (file, line). Sources come from flags.scan_sources, so the
    usual skip rules (VCS dirs, venv, build dirs) apply."""
    root = os.path.abspath(project_dir)
    sources = sorted(os.path.abspath(p) for p in flags.scan_sources(root))
    entries = None
    ctags = provision.find_ctags()
    if ctags:
        entries = _ctags_definitions(ctags, sources)
    if entries is None:
        entries = _regex_definitions(sources)
    out = [{"file": os.path.relpath(e["path"], root), "name": e["name"],
            "line": e["line"], "static": e["static"]} for e in entries]
    out.sort(key=lambda e: (e["file"], e["line"]))
    return out
