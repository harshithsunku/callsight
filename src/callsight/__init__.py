"""callsight — compile-time function tracing for C/C++ projects."""

try:  # the installed dist is the source of truth
    from importlib.metadata import PackageNotFoundError, version as _version
    try:
        __version__ = _version("callsight")
    except PackageNotFoundError:  # running from a source checkout
        __version__ = "0.3.1"
except ImportError:  # pragma: no cover - importlib.metadata is stdlib >= 3.8
    __version__ = "0.3.1"
