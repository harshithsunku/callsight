#!/usr/bin/env python3
"""Link checker for the hand-built docs site.

Replaces what `mkdocs build --strict` used to catch: every local href/src must
resolve to a file that exists, and every '#anchor' must exist as an id in the
page it points at. External links are listed, not fetched.

Usage: python3 .github/scripts/check_docs_links.py docs
"""

import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


class PageParser(HTMLParser):
    """Collect element ids and the local links a page references."""

    LINK_ATTRS = {"a": "href", "link": "href", "img": "src", "script": "src"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.links = []   # (tag, value)
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        attr = self.LINK_ATTRS.get(tag)
        if attr and attrs.get(attr):
            self.links.append((tag, attrs[attr]))
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip()


def main(root):
    root = Path(root)
    pages = sorted(root.rglob("*.html"))
    if not pages:
        sys.exit(f"no HTML pages under {root}")

    # Keyed by resolved path, so link targets (also resolved) match.
    parsed = {}
    for page in pages:
        p = PageParser()
        p.feed(page.read_text(encoding="utf-8"))
        parsed[page.resolve()] = p

    errors = []
    external = set()
    for page, p in parsed.items():
        if not p.title:
            errors.append(f"{page}: no <title>")
        for tag, link in p.links:
            url = urlparse(link)
            if url.scheme in ("http", "https", "mailto", "data"):
                external.add(link)
                continue
            if url.scheme:
                errors.append(f"{page}: unsupported scheme in {link!r}")
                continue

            # Resolve the target document ('' means this page).
            if url.path:
                target = (page.parent / unquote(url.path)).resolve()
                if target.is_dir():
                    target = target / "index.html"
                if not target.exists():
                    errors.append(f"{page}: {tag} target does not exist: {link}")
                    continue
            else:
                target = page.resolve()

            if url.fragment:
                tp = parsed.get(target)
                if tp is None and target.suffix == ".html":
                    errors.append(f"{page}: cannot check anchor in {link}")
                elif tp is not None and url.fragment not in tp.ids:
                    errors.append(f"{page}: no id '{url.fragment}' in "
                                  f"{target.name} (from {link})")

    print(f"checked {len(pages)} pages, "
          f"{sum(len(p.links) for p in parsed.values())} links "
          f"({len(external)} external, not fetched)")
    for page, p in sorted(parsed.items()):
        print(f"  {page.name}: {len(p.ids)} ids, {len(p.links)} links — {p.title}")

    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("\nall local links and anchors resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "docs"))
