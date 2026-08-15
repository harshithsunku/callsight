"""Provision bundled external tools (currently: a static ctags binary).

The web UI's config builder and symbols.py prefer a real ctags binary
(accurate parsing) over the regex fallback. Rather than depending on
whatever the host happens to have installed, callsight can download a
pre-built static universal-ctags from its GitHub release assets into
$CALLSIGHT_HOME/bin (default ~/.callsight/bin) — perflens-style.

Lookup order for ctags: PATH first, then the bundled copy. Only
linux-x86_64 and linux-aarch64 assets are published; anything else just
keeps the regex fallback.
"""

import hashlib
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request

_DOWNLOAD_TIMEOUT = 60
_RELEASES_BASE = ("https://github.com/harshithsunku/callsight"
                  "/releases/latest/download")


def callsight_home():
    """Base dir for bundled tools; CALLSIGHT_HOME overrides ~/.callsight."""
    return os.environ.get("CALLSIGHT_HOME") or os.path.join(
        os.path.expanduser("~"), ".callsight")


def bin_dir():
    return os.path.join(callsight_home(), "bin")


def bundled_ctags():
    return os.path.join(bin_dir(), "ctags")


def find_ctags():
    """ctags on PATH first, then the bundled copy, else None."""
    path = shutil.which("ctags")
    if path:
        return path
    bundled = bundled_ctags()
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return None


def asset_name(machine=None):
    """Release-asset name for the current CPU, e.g.
    'callsight-ctags-linux-x86_64'; None when unsupported."""
    machine = (platform.machine() if machine is None else machine).lower()
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if machine in ("x86_64", "aarch64"):
        return f"callsight-ctags-linux-{machine}"
    return None


def _fetch(url):
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT) as resp:
        return resp.read()


def download_ctags():
    """Download the static ctags release asset into $CALLSIGHT_HOME/bin.

    Verifies the published sha256, installs atomically (temp file +
    os.replace), and smoke-runs '<path> --version'. Raises RuntimeError
    with a clear message on any failure."""
    asset = asset_name()
    if asset is None:
        raise RuntimeError(
            f"no prebuilt ctags for this architecture "
            f"({platform.machine() or 'unknown'}); the regex fallback "
            f"still works without it")
    dest = bundled_ctags()
    try:
        data = _fetch(f"{_RELEASES_BASE}/{asset}")
        checksums = _fetch(f"{_RELEASES_BASE}/{asset}.sha256").decode()
    except (OSError, ValueError) as e:
        raise RuntimeError(f"could not download {asset}: {e}") from None
    expected = None
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == asset:
            expected = parts[0]
            break
    if expected is None:
        raise RuntimeError(f"malformed checksum file for {asset}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"checksum mismatch for {asset}: expected {expected}, "
            f"got {actual}")
    os.makedirs(bin_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=bin_dir(), prefix="ctags.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    failure = None
    try:
        proc = subprocess.run([dest, "--version"], capture_output=True,
                              text=True, timeout=30)
        out = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0 or "ctags" not in out.lower():
            failure = out
    except (OSError, subprocess.TimeoutExpired) as e:
        failure = str(e)
    if failure is not None:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise RuntimeError(
            f"installed ctags failed its smoke test: {failure}")
    return dest


def ensure_ctags():
    """find_ctags(), else try downloading the bundled copy.

    Returns the ctags path, or None when nothing is available (caller
    falls back to the regex parser)."""
    path = find_ctags()
    if path:
        return path
    try:
        return download_ctags()
    except RuntimeError:
        return None
