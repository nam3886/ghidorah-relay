"""The checks behind tests/admin_ui/test_build_guards.py: what the CSP would block or what must never ship, searched in
the built admin web (`admin/static/app/`). They take text and return findings, so the tests can also run them on planted
files and see each one found."""

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "packages/relay-core/src/relay_core/admin/static"
APP = STATIC / "app"
PUBLIC = Path(__file__).resolve().parents[2] / "apps/admin-web/public"
SCANNED = (".html", ".css", ".js")

# The fonts the design uses (DESIGN section 4): family -> (subsets, weights). They come from two exact-pinned fontsource
# packages and ship as hashed woff2 files of the dist; nothing is vendored in public/ and nothing is inlined as data:.
FONTS = {"be-vietnam-pro": (("latin", "vietnamese"), (400, 500, 600, 700)),
         "ibm-plex-mono": (("latin", "vietnamese"), (400, 500))}
FONT_SUFFIXES = (".woff", ".woff2", ".ttf", ".otf", ".eot")


def font_files() -> list[str]:
    """The woff2 files of the dist, as paths relative to it."""
    return [p.relative_to(APP).as_posix() for p in sorted(APP.rglob("*.woff2")) if p.is_file()]


def missing_fonts(names: list[str]) -> list[str]:
    """The font files the design needs that `names` (dist paths) do not have: `assets/<family>-<subset>-<weight>-normal-
    <hash>.woff2`."""
    missing = []
    for family, (subsets, weights) in FONTS.items():
        for subset in subsets:
            for weight in weights:
                pattern = re.compile(r"^assets/%s-%s-%d-normal-[A-Za-z0-9_-]+\.woff2$" % (family, subset, weight))
                if not any(pattern.match(name) for name in names):
                    missing.append("%s %s %d" % (family, subset, weight))
    return missing


def vendored_fonts() -> list[str]:
    """Font files under public/ (there must be none: the fonts come from the packages)."""
    return [p.name for p in sorted(PUBLIC.rglob("*")) if p.is_file() and p.suffix.lower() in FONT_SUFFIXES]

# What each check looks for. Every pattern is searched in the text of every scanned file.
REMOTE_URL = re.compile(r"""(?:https?:)?//[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}[^\s"'`)<>]*""")
INLINE_HANDLER = re.compile(r"""[\s"'`]on[a-z]+\s*=\s*["'`]""")
JAVASCRIPT_URL = re.compile(r"javascript:", re.I)
SOURCE_MAP = re.compile(r"sourceMappingURL")
STYLE_ATTRIBUTE = re.compile(r"""\sstyle\s*=\s*["']""")
# A data: URL in a CSS url() or in a quoted string. The admin CSP has no img-src (it falls back to 'self'): blocked.
DATA_URL = re.compile(r"""url\(\s*["']?data:[^)]*\)|["'`]data:[a-z]+/""")
CHECKS = {"remote URL": REMOTE_URL, "inline handler": INLINE_HANDLER, "javascript: URL": JAVASCRIPT_URL,
          "source-map comment": SOURCE_MAP, "style attribute": STYLE_ATTRIBUTE, "data: URL": DATA_URL}


def key_of(text: str) -> str:
    """How an allow-list entry names a finding: the text itself, or for a long one its sha256 prefix (an Element Plus
    upgrade that changes the text then fails the guard and shows the new text to judge)."""
    return text if len(text) <= 100 else "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]

# Findings that are allowed, each with the reason. (check name, the exact matched text) -> why it is not a problem.
# An entry that matches nothing in the dist is itself a failure, so the list cannot outlive what it excuses.
_XML_NAMESPACE = "an XML namespace name (an identifier of the vocabulary), never requested by a browser"
ALLOWED: dict[tuple[str, str], str] = {
    ("remote URL", "http://www.w3.org/2000/svg"): _XML_NAMESPACE + " (createElementNS, xmlns in an inline SVG image)",
    ("remote URL", "http://www.w3.org/1999/xlink"): _XML_NAMESPACE + " (Vue's xlink attribute namespace)",
    ("remote URL", "http://www.w3.org/1998/Math/MathML"): _XML_NAMESPACE + " (Vue's MathML element namespace)",
    ("remote URL", "https://vuejs.org/error-reference/#runtime-${r}"): "Vue's production error message template; text",
    # key = sha256 prefix of the whole url("data:...") text, so only this exact image is excused, not data: URLs
    ("data: URL", "sha256:c8faacbdb989b684"): (
        "the tick mask of a selected option in an Element Plus multiple select (`-webkit-mask` and `mask` of "
        "`.el-select-dropdown__item.is-selected`); theme.css replaces it with a border-drawn tick, so the URL is never "
        "requested and the CSP never sees it; only the rule text stays in the bundle"),
    ("data: URL", "`data:attachment/"): (
        "the string a Vue devtools-free file-download helper writes into a Blob reader result (data:attachment/file;); "
        "code that rewrites a string, never a request or an element source"),
}


# Element Plus names the documentation page of a deprecated prop in its warning data (`ref:` of a deprecation notice).
# Allowed only as a whole quoted string `https://element-plus.org/en-US/component/<name>.html[#anchor]`: any other path
# or host, or a URL that is not a complete string, still fails.
DOC_URL = re.compile(r"https://element-plus\.org/en-US/component/[a-z0-9-]+\.html(?:#[A-Za-z0-9_-]+)?")
DOC_URL_REASON = (
    "the documentation page that an Element Plus deprecation notice names (`ref:` of its warning data, for a prop of "
    "checkbox, button, dialog, link ...); the notice only prints it in development, and the page never requests it")
QUOTES = "\"'`"


def _hits(files: dict[str, str]):
    """(file, check, matched text, character before, character after) for every finding, allowed or not."""
    for name, text in sorted(files.items()):
        for check, pattern in CHECKS.items():
            for match in pattern.finditer(text):
                before = text[match.start() - 1] if match.start() else ""
                after = text[match.end()] if match.end() < len(text) else ""
                yield name, check, match.group(0), before, after


def findings(files: dict[str, str]) -> list[tuple[str, str, str]]:
    """(file, check, matched text) for every finding in `files` (name -> text), allowed or not."""
    return [(n, c, m) for n, c, m, _, _ in _hits(files)]


def is_doc_link(check: str, matched: str, before: str, after: str) -> bool:
    return check == "remote URL" and bool(DOC_URL.fullmatch(matched)) and before in QUOTES and after in QUOTES


def scan(files: dict[str, str]) -> list[tuple[str, str, str]]:
    """The findings that are not in ALLOWED and are not an Element Plus documentation link inside a string."""
    return [(n, c, m) for n, c, m, b, a in _hits(files)
            if (c, key_of(m)) not in ALLOWED and not is_doc_link(c, m, b, a)]


def dist_files() -> dict[str, str]:
    return {p.relative_to(APP).as_posix(): p.read_text(encoding="utf-8") for p in sorted(APP.rglob("*"))
            if p.is_file() and p.suffix in SCANNED}


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[str] = []
        self.html_lang = ""
        self.viewport = ""
        self.title = ""
        self._in_title = False
        self.style_elements = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.html_lang = a.get("lang") or ""
        elif tag == "meta" and a.get("name") == "viewport":
            self.viewport = a.get("content") or ""
        elif tag == "title":
            self._in_title = True
        elif tag == "style":
            self.style_elements += 1
        for attr in ("src", "href"):
            if a.get(attr) and tag in ("script", "link", "img"):
                self.refs.append(a[attr])

    def handle_endtag(self, tag):
        self._in_title = self._in_title and tag != "title"

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def parse_index(text: str) -> _Page:
    page = _Page()
    page.feed(text)
    return page


def index_problems(text: str, exists) -> list[str]:
    """What is wrong with an entry page: a missing title, language or viewport, an element the CSP blocks, a reference
    to something that is not a file under /ui/app/ (`exists(relative name)`) or that leaves the app."""
    page = parse_index(text)
    problems = []
    if not page.title.strip():
        problems.append("no <title>")
    if not page.html_lang:
        problems.append("no lang on <html>")
    if "width=device-width" not in page.viewport:
        problems.append("no viewport meta")
    if page.style_elements:
        problems.append("a <style> element")
    if re.search(r"<script(?![^>]*\bsrc=)[^>]*>\s*[^<\s]", text):
        problems.append("an inline <script>")
    if not page.refs:
        problems.append("the page references no script or stylesheet")
    for ref in page.refs:
        if not ref.startswith("/ui/app/"):
            problems.append("%s is not under /ui/app/" % ref)
        elif not exists(ref.removeprefix("/ui/app/")):
            problems.append("%s is not a file of the dist" % ref)
    return problems
