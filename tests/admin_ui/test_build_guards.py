"""Static guards over the built admin web (`admin/static/app/`, committed): what the CSP would block or what must never
ship, found in every html, css and js file of the dist, lazy chunks included, without opening a browser. The run-time
proof (zero CSP violations in every route, dialog and popper) is the E2E sweep; this reaches the code that no route
visits. The checks are in webguards.py beside it. The Scalar pin and the guards of `reference.html` are in
test_reference.py."""

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
    assert webguards.DOC_URL_REASON.strip()
    assert any(webguards.DOC_URL.fullmatch(t) for _, c, t in findings(dist_files()) if c == "remote URL"), \
        "the Element Plus documentation-link rule matches nothing in the dist: remove it"


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
@mutation(LIB, "if (c, key_of(m)) not in ALLOWED and not is_doc_link(c, m, b, a)]", "if not is_doc_link(c, m, b, a)]",
          expect="was reported")
@mutation(LIB, 'component/[a-z0-9-]+\\.html', 'component/.+\\.html',
          expect="a link that is not a documentation page was excused")
@mutation(LIB, "and before in QUOTES and after in QUOTES", "and before in QUOTES",
          expect="a longer URL was excused")
@mutation(LIB, 'https://element-plus\\.org/en-US', 'https://[a-z.-]+/en-US',
          expect="a link that is not a documentation page was excused")
@mutation(LIB, '|["\'`]data:(?:[a-z]+/|[;,])"""', '"""', expect="data: URL not found")
@mutation(LIB, '(?:[a-z]+/|[;,])""", re.I)', '(?:[a-z]+/)""", re.I)', expect="a data: URL with no media type")
@mutation(LIB, '(?:[a-z]+/|[;,])""", re.I)', '(?:[a-z]+/|[;,])""")', expect="a data: URL in capitals")
@mutation(LIB, 'CSS_DATA = re.compile(r"data:")', 'CSS_DATA = re.compile(r"dataX:")',
          expect="data: URL not found")
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
    # a data: URL in a stylesheet or in a script string is found; a stylesheet may hold no data: at all
    other = {"e.css": 'a{background:url("data:image/png;base64,AAAA")}', "f.js": 'img.src = "data:image/gif;base64,R0"'}
    assert {(n, c) for n, c, _ in scan(other)} == {("e.css", "data: URL"), ("e.css", "data: in CSS"),
                                                   ("f.js", "data: URL")}, "data: URL not found"
    assert {(n, c) for n, c, _ in scan({"g.css": "a{mask:url(data:image/svg+xml,%3Csvg%3E)}"})} >= {
        ("g.css", "data: in CSS")}, "data: in CSS not found"
    assert scan({"h.css": 'a::after{content:"data:x"}'}) != [], "data: in CSS not found"
    assert scan({"h.js": 'const hint = "data:"'}) == [], "a bare data: in a script was reported"
    # every form of the scheme in a script string: media type, parameter only, nothing, base64, other case, other quote
    for form in ('"data:,payload"', "'data:;base64,AAAA'", "`data:text/plain,hi`", '"data:text/plain;charset=utf-8,hi"',
                 '"data:image/png;base64,AAAA"'):
        assert scan({"i.js": "const u = %s" % form}) != [], "a data: URL with no media type or another form: " + form
    assert scan({"j.js": 'const u = "DATA:,x"'}) != [], "a data: URL in capitals"
    # negative control: clean files and an allowed finding produce nothing
    assert scan({"ok.js": 'const ns = "x"; el.className = "a b";', "ok.css": "a { color: red }"}) == []
    monkeypatch.setitem(webguards.ALLOWED, ("remote URL", "http://www.w3.org/2000/svg"),
                        "an XML namespace name, never fetched")
    assert scan({"svg.js": 'document.createElementNS("http://www.w3.org/2000/svg", "svg")'}) == [], \
        "an allowed finding was reported"
    assert scan({"svg.js": 'fetch("http://www.w3.org/other")'}) != []
    # Element Plus documentation links inside a string are excused; nothing else on that host or any other host is
    doc = "https://element-plus.org/en-US/component/checkbox.html"
    assert scan({"a.js": "ref:`%s`" % doc, "b.js": 'x="%s#slots"' % doc}) == [], "a documentation link was reported"
    for bad in ("https://element-plus.org/x.js", "https://element-plus.org/en-US/component/checkbox.js",
                "https://evil.example.com/en-US/component/checkbox.html",
                "https://element-plus.org/en-US/component/../x.html"):
        assert scan({"c.js": "u=`%s`" % bad}) != [], "a link that is not a documentation page was excused: " + bad
    assert scan({"d.js": "fetch(%s)" % doc}) != [], "a link outside a string was excused"
    assert scan({"e.js": 'u="%s/more"' % doc}) != [], "a longer URL was excused"
    assert scan({"f.js": 'u="%s and more"' % doc}) != [], "a longer URL was excused"


def test_the_fonts_are_hashed_woff2_files_of_the_dist_and_none_is_vendored_or_inlined():
    names = webguards.font_files()
    assert webguards.missing_fonts(names) == [], "missing font files of the dist: %s" % webguards.missing_fonts(names)
    assert webguards.vendored_fonts() == [], "font files vendored in public/: %s" % webguards.vendored_fonts()
    css = "".join(text for name, text in dist_files().items() if name.endswith(".css"))
    for family in ("Manrope", "JetBrains Mono"):
        assert family in css, "no @font-face for %s in the built css" % family


@mutation(LIB, '(400, 500, 600, 700, 800)', '(400, 500, 600, 700)', expect="font check: the full set passes")
@mutation(LIB, '("latin", "vietnamese"), (400, 500))', '("latin",), (400, 500))',
          expect="font check: the full set passes")
@mutation(LIB, "-normal-[A-Za-z0-9_-]+", "-[a-z]+-[A-Za-z0-9_-]+", expect="font check: only the normal style counts")
@mutation(LIB, "if not any(pattern.match(name) for name in names):", "if False:",
          expect="font check: a missing file is reported")
@mutation(LIB, 'p.suffix.lower() in FONT_SUFFIXES', 'False', expect="font check: a vendored font is found")
def test_the_font_check_finds_what_is_missing_and_what_is_vendored(tmp_path, monkeypatch):
    def file(family, subset, weight, style="normal", ext="woff2"):
        return "assets/%s-%s-%d-%s-AbC123_-.%s" % (family, subset, weight, style, ext)

    full = [file(f, s, w) for f, (subsets, weights) in webguards.FONTS.items() for s in subsets for w in weights]
    assert len(full) == 14 and webguards.missing_fonts(full) == [], "font check: the full set passes"
    assert len(webguards.missing_fonts([])) == 14, "font check: a missing file is reported"
    assert webguards.missing_fonts([n for n in full if "jetbrains-mono-vietnamese-500" not in n]) == [
        "jetbrains-mono vietnamese 500"], "font check: every subset is required"
    assert webguards.missing_fonts([n for n in full if "manrope-latin-800" not in n]) == [
        "manrope latin 800"], "font check: every weight is required"
    italic = [n.replace("-normal-", "-italic-") if "manrope-latin-400" in n else n for n in full]
    assert webguards.missing_fonts(italic) == ["manrope latin 400"], "font check: only the normal style counts"
    assert webguards.missing_fonts([n.replace(".woff2", ".woff") for n in full]) != [], "font check: only woff2 counts"
    public = tmp_path / "public"
    (public / "brand").mkdir(parents=True)
    (public / "brand" / "logo.svg").write_text("<svg/>")
    monkeypatch.setattr(webguards, "PUBLIC", public)
    assert webguards.vendored_fonts() == []
    (public / "fonts").mkdir()
    (public / "fonts" / "Inter.woff2").write_bytes(b"x")
    assert webguards.vendored_fonts() == ["Inter.woff2"], "font check: a vendored font is found"


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
