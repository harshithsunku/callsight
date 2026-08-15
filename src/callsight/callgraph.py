"""Lightweight static call graph for C/C++ sources.

Powers the `include-func` directive: given a seed function, callsight must
know which functions it (transitively) calls to instrument exactly that
subtree. This parser is deliberately heuristic — a full C parser would
drag in libclang — and the failure mode is documented: calls through
function pointers, macro-generated calls, and dynamically dispatched C++
methods are not followed. Definitions are matched by name; if two files
define same-named functions (e.g. file-local `static`s), all definitions
contribute their files and their callees are unioned.
"""

import re

# Calls we never follow: C keywords with parens and common pseudo-calls.
C_KEYWORDS = frozenset("""
    if for while switch return sizeof typeof alignof _Alignof case goto
    do else void int long short char float double unsigned signed const
    static extern struct union enum typedef volatile inline restrict
    __attribute__ __builtin_unreachable defined
""".split())

_FUNC_DEF_RE = re.compile(
    # return type(s) + name + params + '{'  — start-of-line anchored to
    # skip calls, which are indented inside another function body. The
    # return-type span may cross a newline followed by horizontal
    # whitespace: after _strip() such a run is a blanked multi-line
    # comment or __attribute__((...)) between the type and the name (or a
    # harmlessly split declaration). The trailing '{', and params that
    # can't contain ';', still keep calls from matching.
    r"^[ \t]*[A-Za-z_](?:[\w:\*&<>~\[\], \t]|\n[ \t]*)*?"
    r"\b([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:->[^{;]*)?\{",
    re.MULTILINE)

_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

_STRIP_RE = re.compile(
    r"//[^\n]*"           # line comments
    r"|/\*.*?\*/"         # block comments
    r'|"(?:\\.|[^"\\])*"' # string literals
    r"|'(?:\\.|[^'\\])*'",  # char literals
    re.DOTALL)


_ATTR_RE = re.compile(r"__attribute__\s*\(\([^()]*\)\)")


def _blank(m):
    # Replace everything but newlines so offsets AND line numbers survive.
    return re.sub(r"[^\n]", " ", m.group(0))


def _strip(text):
    """Blank out comments, literals and __attribute__((...)) specifiers
    (offset- and line-preserving) so they can't confuse the structure
    scan."""
    text = _ATTR_RE.sub(_blank, text)
    return _STRIP_RE.sub(_blank, text)


def _body_span(text, open_brace):
    """Return (start, end) of the block opened at text[open_brace]."""
    depth = 0
    for i in range(open_brace, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return open_brace + 1, i
    return open_brace + 1, len(text)  # unbalanced: take the rest


_STATIC_RE = re.compile(r"\bstatic\b")


def find_definitions(path):
    """Return [(name, line, static)] for each function definition in path.

    Same heuristic scan as parse_source, so the same limitations apply;
    `static` comes from a `static` keyword before the function name on the
    definition line."""
    with open(path, errors="replace") as f:
        text = _strip(f.read())
    defs = []
    for m in _FUNC_DEF_RE.finditer(text):
        name = m.group(1)
        if name in C_KEYWORDS:
            continue
        line = text.count("\n", 0, m.start()) + 1
        prefix = text[m.start():m.start(1)]
        defs.append((name, line, bool(_STATIC_RE.search(prefix))))
    return defs


def parse_source(path):
    """Parse one source file; return {name: [callee, ...]}."""
    with open(path, errors="replace") as f:
        text = _strip(f.read())
    functions = {}
    for m in _FUNC_DEF_RE.finditer(text):
        name = m.group(1)
        if name in C_KEYWORDS:
            continue
        start, end = _body_span(text, m.end() - 1)
        callees = [c for c in _CALL_RE.findall(text[start:end])
                   if c not in C_KEYWORDS]
        functions.setdefault(name, [])
        functions[name] = sorted(set(functions[name]) | set(callees))
    return functions


def build_graph(sources):
    """{name: {"files": [path...], "callees": [name...]}} over all sources.

    Multiple same-named definitions (file-local statics, C++ overloads)
    merge: files accumulate, callees union."""
    graph = {}
    for path in sources:
        try:
            parsed = parse_source(path)
        except OSError:
            continue
        for name, callees in parsed.items():
            entry = graph.setdefault(name, {"files": [], "callees": []})
            entry["files"].append(path)
            entry["callees"] = sorted(set(entry["callees"]) | set(callees))
    return graph


def expand(graph, seeds, depth=None):
    """Breadth-first reachable set from seeds. depth=None means the full
    subtree; depth=0 is just the seeds, depth=1 adds direct callees, ..."""
    seen = set()
    frontier = [(s, 0) for s in seeds]
    while frontier:
        name, d = frontier.pop()
        if name in seen or name not in graph:
            continue
        seen.add(name)
        if depth is not None and d >= depth:
            continue
        frontier.extend((c, d + 1) for c in graph[name]["callees"])
    return seen
