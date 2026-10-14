"""Static guards over the built admin web (`admin/static/app/`, committed): what the CSP would block or what must never
ship, found in every html, css and js file of the dist, lazy chunks included, without opening a browser. The run-time
proof (zero CSP violations in every route, dialog and popper) is the E2E sweep; this reaches the code that no route
visits. The checks are in webguards.py beside it. The Scalar pin and licence check stays with `reference.html` in
test_html_guards.py."""

import webguards
from webguards import ALLOWED, APP, dist_files, findings, index_problems, key_of, parse_index, scan

from relay_core.testing.mutation import mutation

LIB = "tests/admin_ui/webguards.py"


def test_the_dist_is_built_and_holds_the_entry_the_theme_script_and_hashed_assets():
    files = dist_files()
    assert "index.html" in files and "theme-init.js" in files, sorted(files)
    assert any(n.startswith("assets/") and n.endswith(".js") for n in files), "no bundle: %s" % sorted(files)
    assert any(n.startswith("assets/") and n.endswith(".css") for n in files), "no stylesheet: %s" % sorted(files)


def test_the_dist_has_no_remote_url_inline_handler_javascript_url_source_map_or_style_attribute():
    found = scan(dist_files())
    assert not found, "the dist holds what the CSP or the release rules forbid:\n" + "\n".join(
        "  %s: %s %r" % f for f in found[:40])


def test_every_allow_list_entry_is_used_and_has_a_reason():
    present = {(check, key_of(text)) for _, check, text in findings(dist_files())}
    for entry, reason in ALLOWED.items():
        assert reason.strip(), "an allow-list entry without a reason: %r" % (entry,)
        assert entry in present, "an allow-list entry that matches nothing in the dist: %r" % (entry,)


@mutation(LIB, 'REMOTE_URL = re.compile(r"""(?:https?:)?//', 'REMOTE_URL = re.compile(r"""(?:https:)?//XX',
          expect="remote URL not found")
@mutation(LIB, "INLINE_HANDLER = re.compile(r", "INLINE_HANDLER = re.compile(r'X' + r",
          expect="inline handler not found")
@mutation(LIB, 'JAVASCRIPT_URL = re.compile(r"javascript:"', 'JAVASCRIPT_URL = re.compile(r"javascriptX:"',
          expect="javascript: URL not found")
@mutation(LIB, 'SOURCE_MAP = re.compile(r"sourceMappingURL")', 'SOURCE_MAP = re.compile(r"sourceMappingURLX")',
          expect="source-map comment not found")
@mutation(LIB, 'STYLE_ATTRIBUTE = re.compile(r"""\\sstyle', 'STYLE_ATTRIBUTE = re.compile(r"""\\sstyleX',
          expect="style attribute not found")
@mutation(LIB, "if (f[1], key_of(f[2])) not in ALLOWED]", "if True]", expect="was reported")
@mutation(LIB, '|["\'`]data:[a-z]+/"""', '"""', expect="data: URL not found")
@mutation(LIB, 'DATA_URL = re.compile(r"""url\\(\\s*["\']?data:[^)]*\\)|', 'DATA_URL = re.compile(r"""',
          expect="the tick mask is not found as a data: URL")
@mutation(LIB, 'return text if len(text) <= 100 else', 'return text if len(text) <= 10000 else',
          expect="the allowed data: image was reported")
def test_the_scanner_finds_each_forbidden_thing_in_planted_files_and_skips_what_is_allowed(monkeypatch):
    planted = {
        "a.js": ('fetch("https://cdn.example.com/x.js"); el.innerHTML = \'<img onerror="x()">\'; '
                 'const u = "javascript:void 0";'),
        "b.js": "var a = 1;\n//# sourceMappingURL=b.js.map\n",
        "c.html": '<div style="color:red"></div><script src="//evil.example.org/p.js"></script>',
        "d.css": "a { background: url(https://fonts.example.net/f.woff2) }",
    }
    seen = {(name, check) for name, check, _ in scan(planted)}
    for expected in [("a.js", "remote URL"), ("a.js", "inline handler"), ("a.js", "javascript: URL"),
                     ("b.js", "source-map comment"), ("c.html", "style attribute"), ("c.html", "remote URL"),
                     ("d.css", "remote URL")]:
        assert expected in seen, "%s not found in %s: %s" % (expected[1], expected[0], sorted(seen))
    # a data: URL: any image other than the one allowed tick mask is found, in a stylesheet and in a script string
    other = {"e.css": 'a{background:url("data:image/png;base64,AAAA")}', "f.js": 'img.src = "data:image/gif;base64,R0"'}
    assert {(n, c) for n, c, _ in scan(other)} == {("e.css", "data: URL"), ("f.js", "data: URL")}, \
        "data: URL not found"
    # the allowed tick mask, as the dist holds it, is excused (negative control for the check above)
    tick = next((t for _, c, t in findings(dist_files()) if c == "data: URL" and t.startswith("url(")), None)
    assert tick, "the tick mask is not found as a data: URL"
    assert scan({"x.css": "a{mask:%s 0 0/100%% no-repeat}" % tick}) == [], "the allowed data: image was reported"
    assert scan({"x.css": "a{mask:%s}" % tick.replace("%3E", "%3F", 1)}) != [], "a changed image was excused"
    # negative control: clean files and an allowed finding produce nothing
    assert scan({"ok.js": 'const ns = "x"; el.className = "a b";', "ok.css": "a { color: red }"}) == []
    monkeypatch.setitem(webguards.ALLOWED, ("remote URL", "http://www.w3.org/2000/svg"),
                        "an XML namespace name, never fetched")
    assert scan({"svg.js": 'document.createElementNS("http://www.w3.org/2000/svg", "svg")'}) == [], \
        "an allowed finding was reported"
    assert scan({"svg.js": 'fetch("http://www.w3.org/other")'}) != []


def test_the_entry_page_has_a_title_a_language_a_viewport_and_only_references_files_of_the_dist():
    text = (APP / "index.html").read_text(encoding="utf-8")
    problems = index_problems(text, lambda name: (APP / name).is_file())
    assert not problems, "index.html: %s" % problems
    refs = parse_index(text).refs
    assert any(r.endswith("theme-init.js") for r in refs) and any("/assets/" in r for r in refs), refs


@mutation(LIB, 'problems.append("no <title>")', "pass", expect="no <title> not found")
@mutation(LIB, 'problems.append("no lang on <html>")', "pass", expect="no lang on <html> not found")
@mutation(LIB, 'problems.append("no viewport meta")', "pass", expect="no viewport meta not found")
@mutation(LIB, 'problems.append("%s is not a file of the dist" % ref)', "pass",
          expect="not a file of the dist not found")
@mutation(LIB, 'problems.append("%s is not under /ui/app/" % ref)', "pass", expect="not under /ui/app/ not found")
@mutation(LIB, 'problems.append("an inline <script>")', "pass", expect="an inline <script> not found")
@mutation(LIB, 'problems.append("a <style> element")', "pass", expect="a <style> element not found")
def test_the_entry_page_check_finds_each_problem_in_planted_pages_and_passes_a_good_one():
    good = ('<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>x</title><script src="/ui/app/theme-init.js"></script>'
            '<script type="module" src="/ui/app/assets/a-12345678.js"></script></head><body></body></html>')
    files = {"theme-init.js", "assets/a-12345678.js"}
    assert index_problems(good, files.__contains__) == []                      # the negative control
    cases = {
        "no <title>": good.replace("<title>x</title>", ""),
        "no lang on <html>": good.replace(' lang="en"', ""),
        "no viewport meta": good.replace('name="viewport"', 'name="other"'),
        "not a file of the dist": good.replace("a-12345678.js", "gone-12345678.js"),
        "not under /ui/app/": good.replace("/ui/app/theme-init.js", "/ui/other/theme-init.js"),
        "an inline <script>": good.replace("</head>", "<script>alert(1)</script></head>"),
        "a <style> element": good.replace("</head>", "<style>a{}</style></head>"),
    }
    for needle, page in cases.items():
        problems = index_problems(page, files.__contains__)
        assert any(needle in p for p in problems), "%s not found: %s" % (needle, problems)
