"""The checks behind tests/admin_ui/test_build_guards.py: what the CSP would block or what must never ship, searched in
the built admin web (`admin/static/app/`). They take text and return findings, so the tests can also run them on planted
files and see each one found."""

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "packages/relay-core/src/relay_core/admin/static"
APP = STATIC / "app"
SCANNED = (".html", ".css", ".js")

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
_DOC_LINK = "text inside an Element Plus development warning string that names its documentation page; never fetched"
ALLOWED: dict[tuple[str, str], str] = {
    ("remote URL", "http://www.w3.org/2000/svg"): _XML_NAMESPACE + " (createElementNS, xmlns in an inline SVG image)",
    ("remote URL", "http://www.w3.org/1999/xlink"): _XML_NAMESPACE + " (Vue's xlink attribute namespace)",
    ("remote URL", "http://www.w3.org/1998/Math/MathML"): _XML_NAMESPACE + " (Vue's MathML element namespace)",
    ("remote URL", "https://vuejs.org/error-reference/#runtime-${r}"): "Vue's production error message template; text",
    ("remote URL", "https://element-plus.org/en-US/component/button.html#button-attributes"): _DOC_LINK,
    ("remote URL", "https://element-plus.org/en-US/component/dialog.html#slots"): _DOC_LINK,
    ("remote URL", "https://element-plus.org/en-US/component/link.html#underline"): _DOC_LINK,
    # key = sha256 prefix of the whole url("data:...") text, so only this exact image is excused, not data: URLs
    ("data: URL", "sha256:c8faacbdb989b684"): (
        "the tick mask of a selected option in an Element Plus multiple select (`-webkit-mask` and `mask` of "
        "`.el-select-dropdown__item.is-selected`); theme.css replaces it with a border-drawn tick, so the URL is never "
        "requested and the CSP never sees it; only the rule text stays in the bundle"),
    ("data: URL", "`data:attachment/"): (
        "the string a Vue devtools-free file-download helper writes into a Blob reader result (data:attachment/file;); "
        "code that rewrites a string, never a request or an element source"),
}


def findings(files: dict[str, str]) -> list[tuple[str, str, str]]:
    """(file, check, matched text) for every finding in `files` (name -> text), allowed or not."""
    return [(name, check, match.group(0)) for name, text in sorted(files.items())
            for check, pattern in CHECKS.items() for match in pattern.finditer(text)]


def scan(files: dict[str, str]) -> list[tuple[str, str, str]]:
    """The findings that are not in ALLOWED."""
    return [f for f in findings(files) if (f[1], key_of(f[2])) not in ALLOWED]


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
