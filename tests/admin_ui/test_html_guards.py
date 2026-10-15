"""Static guards over the admin pages (contracts §13, §16): markup the CSP allows, assets pinned, every class the pages
use present in the built stylesheet, tables built from one column list, the session token and the test tab's key kept
where they may be. They read the files in `admin/static/`; no browser is needed (the browser checks are in
test_smoke.py)."""

import base64
import hashlib
import re
from pathlib import Path

import pytest

from relay_core.testing.mutation import mutation

STATIC = Path(__file__).resolve().parents[2] / "packages/relay-core/src/relay_core/admin/static"
TAB_KEYS_FILE = "packages/relay-core/src/relay_core/admin/static/tab-keys.js"
REFERENCE_FILE = "packages/relay-core/src/relay_core/admin/static/reference.html"
TEST_JS_FILE = "packages/relay-core/src/relay_core/admin/static/tab-test.js"
LOGS_FILE = "packages/relay-core/src/relay_core/admin/static/tab-logs.js"
ALERTS_FILE = "packages/relay-core/src/relay_core/admin/static/tab-alerts.js"
INPUT_CSS_FILE = "packages/relay-core/src/relay_core/admin/tailwind/input.css"
PAGES = ["login.html", "manage.html", "reference.html"]
HTML = sorted(p for p in STATIC.glob("*.html"))
OWN_JS = sorted(p for p in STATIC.glob("*.js"))
TAB_JS = sorted(p for p in STATIC.glob("tab-*.js"))

SCALAR = STATIC / "vendor" / "scalar.min.js"
SCALAR_URL = "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.25.122/dist/browser/standalone.min.js"
SCALAR_SHA384 = "GSEN1x/V+skrQFYZQ1d4OxNM7t1xg7f8WND43lCquTJEO+mRo9l0AAa3HzrNOLqt"
ADMIN_CSS = STATIC / "vendor" / "admin.css"
ADMIN_CSS_SHA384 = "k9VlogWCkpLFZ0brX2jti0AiXeyI3bKP6fwEcuJxuwXOdoYhGFA27aovGSa8vMQF"

CLASS_ATTR = re.compile(r'class="([^"]*)"')
JS_STRING = re.compile(r"'((?:[^'\\\n]|\\.)*)'")
CLASS_CONSTANT = re.compile(r"^const (?:ALIGN|BADGE|BTN\w*|FIELD|STEP_WIDTH) =.*?;\n", re.S | re.M)
UTILITY_START = re.compile(
    r"^(?:[a-z]+:)*(?:-?(?:bg|text|border|w|h|px|py|p|m|mx|my|mt|mb|mr|ml|gap|space|divide|max|min|font|rounded|z|top|left|"
    r"right|bottom|inset|shadow|opacity|leading|tracking|align|list|grid|col|row|items|justify|overflow|whitespace|"
    r"line|ring|outline|transition|duration|animate|backdrop)-|flex|grid|hidden|block|inline|fixed|absolute|relative|"
    r"sticky|truncate|underline|uppercase|sr-only|peer|table|rounded|border|container|antialiased|transition)")


def sha384(path: Path) -> str:
    return base64.b64encode(hashlib.sha384(path.read_bytes()).digest()).decode()


def css_classes(sheet: Path = ADMIN_CSS) -> set[str]:
    names = set()
    for raw in re.findall(r"\.((?:\\.|[A-Za-z0-9_-])+)", sheet.read_text()):
        names.add(re.sub(r"\\(.)", r"\1", raw))
    return names


# a class token: lower case words, variants with `:`, fractions, and an arbitrary value in brackets
CLASS_TOKEN = re.compile(r"^!?[a-z0-9][a-z0-9:/.%_-]*(?:\[[^\s\]]*\][a-z0-9:/.%_-]*)?$")


def sheet_of(name: str) -> Path:
    """Each page group has its own stylesheet: the dashboard pages share the built one."""
    return {"reference.html": STATIC / "reference.css"}.get(name, ADMIN_CSS)


def used_classes() -> dict[str, set[str]]:
    """Class tokens each page file uses: `class="…"` attributes anywhere (also inside JS strings; pieces that are
    code, not words, are skipped), the class constants of the scripts, and any JS string of only utility classes."""
    used: dict[str, set[str]] = {}
    for path in [*HTML, *OWN_JS]:
        text = path.read_text()
        # in a script, `class="a ' + x + ' b"` holds words (even pieces between the quotes) and code (odd pieces)
        values = [" ".join(v.split("'")[::2]) for v in CLASS_ATTR.findall(text)]
        tokens = {t for value in values for t in value.split()}
        if path.suffix == ".js":
            literals = JS_STRING.findall("".join(CLASS_CONSTANT.findall(text)))
            literals += [s for s in JS_STRING.findall(text)
                         if s.split() and all(UTILITY_START.match(t) for t in s.split())]
            tokens |= {t.replace("\\'", "'") for literal in literals for t in literal.split()}
        used[path.name] = {t for t in tokens if CLASS_TOKEN.match(t.replace("\\'", "'"))}
    return used


# ---- markup the CSP allows --------------------------------------------------------------------------------------
@mutation(TAB_KEYS_FILE, "'<div class=\"flex items-center justify-end gap-1.5\">'",
          "'<div onclick=\"x()\" class=\"flex items-center justify-end gap-1.5\">'", expect="inline handler")
def test_no_page_or_script_builds_an_inline_script_handler_or_style():
    problems = []
    for path in [*HTML, *OWN_JS]:
        text = path.read_text()
        if path.suffix == ".html":
            problems += [path.name + ": inline <script> body"
                         for _ in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>\s*[^<\s]", text)]
            problems += [path.name + ": <style> element" for _ in re.finditer(r"<style\b", text)]
        problems += [path.name + ": inline handler " + m.group(0)
                     for m in re.finditer(r"""[\s"']on[a-z]+\s*=\s*["']""", text)]
        problems += [path.name + ": style attribute" for _ in re.finditer(r"""\sstyle\s*=\s*["']""", text)]
        problems += [path.name + ": javascript: URL" for _ in re.finditer(r"javascript:", text, re.I)]
    assert not problems, "inline handler, style or script: %s" % problems


def test_no_page_loads_anything_from_another_host():
    problems = []
    for path in HTML:
        for attr, url in re.findall(r"""\b(src|href)\s*=\s*["']([^"']+)["']""", path.read_text()):
            if re.match(r"(?:https?:)?//", url) or url.startswith("data:") and not url == "data:,":
                problems.append("%s: %s=%s" % (path.name, attr, url))
            elif url.startswith("/ui/") and not (STATIC / url.removeprefix("/ui/")).is_file():
                problems.append("%s: %s=%s is not a file of the static folder" % (path.name, attr, url))
    assert not problems, "a page names an external or missing asset: %s" % problems


def test_scripts_never_assemble_a_class_name_from_pieces():
    """The stylesheet is built by scanning for whole class names, so `'bg-' + tone` would style nothing."""
    problems = [p.name for p in OWN_JS if re.search(r"""['"](?:bg|text|border|w|h)-['"]\s*\+""", p.read_text())]
    assert not problems, "a class name is built by concatenation in %s" % problems


# ---- vendored assets -----------------------------------------------------------------------------------------------
@mutation(REFERENCE_FILE, "sha384-GSEN1x", "sha384-XSEN1x", expect="does not pin the renderer")
def test_the_renderer_is_the_pinned_vendored_build_with_its_licence_and_the_page_pins_it_by_hash():
    assert sha384(SCALAR) == SCALAR_SHA384, ("the vendored renderer changed: pinned %s from %s"
                                             % (SCALAR_SHA384, SCALAR_URL))
    assert "MIT License" in (STATIC / "vendor" / "scalar.LICENSE.txt").read_text()
    page = (STATIC / "reference.html").read_text()
    assert 'data-integrity="sha384-%s"' % SCALAR_SHA384 in page, "reference.html does not pin the renderer"
    assert 'data-renderer="/ui/vendor/scalar.min.js"' in page


def test_the_stylesheet_is_the_pinned_build_and_has_every_class_the_pages_use():
    sheets: dict[Path, set[str]] = {}
    missing = {}
    for name, tokens in used_classes().items():
        known = sheets.setdefault(sheet_of(name), css_classes(sheet_of(name)))
        gone = sorted(t for t in tokens if t not in known)
        if gone:
            missing[name] = gone
    assert not missing, ("the stylesheet lacks classes the pages use (regenerate it, see tailwind/input.css): %s"
                         % missing)
    assert sha384(ADMIN_CSS) == ADMIN_CSS_SHA384, "admin.css changed: pin %s" % sha384(ADMIN_CSS)


# ---- tables, pagers, switches ----------------------------------------------------------------------------------------
def test_every_table_is_built_by_the_one_column_list_helper_and_every_listing_paginates():
    problems = []
    for path in TAB_JS:
        text = path.read_text()
        if re.search(r"<t[hd]\b", text):
            problems.append(path.name + ": a hand-written <th>/<td> instead of tableHtml(columns, rows)")
        for name, block in re.findall(r"const (\w+_COLUMNS) = \[(.*?)\n\];", text, re.S):
            keys = re.findall(r"\{ key: '(\w+)'", block)
            if not keys or len(set(keys)) != len(keys):
                problems.append("%s: %s has no columns or repeats a column key" % (path.name, name))
    paged = {"tab-accounts.js", "tab-jobs.js", "tab-logs.js", "tab-keys.js"}
    for name in paged:
        text = (STATIC / name).read_text()
        if not re.search(r"pagerHtml|cursorPagerHtml", text):
            problems.append(name + ": a listing without a pager")
    assert not problems, problems


def test_the_account_enable_switch_is_hidden_while_the_account_is_in_use():
    text = (STATIC / "tab-accounts.js").read_text()
    assert re.search(r"\(a\.in_use \? '' : switchHtml\(a\.state !== 'disabled', 'toggleAccount'", text), \
        "the switch must not render for an account a worker holds"


def test_copy_prompt_copies_the_customers_words_and_never_the_marker_or_a_frame():
    text = (STATIC / "tab-jobs.js").read_text()
    copy = text[text.index("ACTIONS.copyPrompt"):text.index("ACTIONS.renameJob")]
    assert "jobPrompt" in copy and "marker" not in copy and "sent_text" not in copy
    assert "textContent = job.prompt;" in text and "job.marker" not in text


# ---- where the token and the key may live ------------------------------------------------------------------
def test_the_admin_token_lives_in_session_storage_only_and_goes_out_as_the_session_header():
    scripts = {p.name: p.read_text() for p in OWN_JS}
    assert not [n for n, t in scripts.items()
                if n not in ("tab-test.js", "reference.js")      # reference.js reads the stored theme only
                and re.search(r"localStorage|document\.cookie|indexedDB", t)], \
        "an admin script uses a store the token must not live in"
    assert not re.search(r"document\.cookie", scripts["tab-test.js"])
    common = scripts["common.js"]
    assert "sessionStorage.getItem(TOKEN_KEY)" in common and "'X-Admin-Session': token()" in common
    assert "sessionStorage.setItem(TOKEN_KEY" in scripts["login.js"]


@mutation(TEST_JS_FILE, "testState.apiKey = tid('apiKey').value.trim();",
          "testState.apiKey = tid('apiKey').value.trim(); localStorage.setItem('k', testState.apiKey);",
          expect="test tab keeps the key in a persistent store")
def test_the_test_tab_keeps_its_key_in_session_storage_only_and_saves_nothing_but_the_form_and_the_images():
    text = (STATIC / "tab-test.js").read_text()
    stores = re.findall(r"(localStorage|sessionStorage)\.(?:setItem|getItem|removeItem)\(([A-Z_]+)", text)
    assert ("sessionStorage", "TEST_KEY") in stores
    assert {name for kind, name in stores if kind == "localStorage"} == {"TEST_FORM_KEY"}, \
        "test tab keeps the key in a persistent store: %s" % stores
    assert not re.search(r"localStorage[^\n]*apiKey|testDb\('put'[^\n]*apiKey", text), \
        "test tab keeps the key in a persistent store"
    assert "'apiKey'" not in re.search(r"const TEST_FIELDS = \[(.*?)\]", text).group(1), \
        "test tab keeps the key in a persistent store"
    # every store access sits in a try block, so a blocked store cannot break the tab
    for line in (ln for ln in text.splitlines() if re.search(r"(?:local|session)Storage\.|indexedDB\.open", ln)):
        assert "try" in line or line.strip().startswith(("const req = indexedDB", "try")), \
            "unguarded store access: %s" % line.strip()
    assert "TEST_MAX_IMAGES = 10" in text and "TEST_MAX_BYTES = 12 * 1024 * 1024" in text


def test_the_test_tab_sends_no_template_field_and_the_dashboard_has_no_phrase_or_template_control():
    for name in ("tab-test.js", "tab-jobs.js", "tab-settings.js"):
        text = (STATIC / name).read_text()
        assert "template_category" not in text and not re.search(r"(?<![\w.])template\s*:", text), name


def test_the_old_test_page_files_are_gone_and_the_test_is_a_tab_of_the_dashboard():
    assert not [n for n in ("test.html", "test.js", "test.css") if (STATIC / n).exists()]
    assert 'src="/ui/tab-test.js"' in (STATIC / "manage.html").read_text()
    assert "id: 'test'" in (STATIC / "tab-test.js").read_text()


@mutation(INPUT_CSS_FILE, "html { font-size: 14px; }", "html { font-size: 16px; }", expect="base size is not 14 px")
def test_the_base_text_size_of_every_admin_page_is_14_px():
    assert "html { font-size: 14px; }" in (STATIC.parent / "tailwind" / "input.css").read_text(), \
        "base size is not 14 px (input.css)"
    assert "html{font-size:14px}" in ADMIN_CSS.read_text(), "base size is not 14 px (admin.css is not rebuilt)"
    for page in ("login.html", "manage.html"):
        assert "vendor/admin.css" in (STATIC / page).read_text(), page


@mutation(TAB_KEYS_FILE, "button('copyText', 'Copy', { from: '#newKeyValue' }, BTN)", "''",
          expect="no Copy button next to the new key")
def test_the_new_key_and_the_key_prefix_have_copy_buttons_and_no_other_place_holds_the_raw_key():
    keys = (STATIC / "tab-keys.js").read_text()
    assert "{ from: '#newKeyValue' }" in keys, "no Copy button next to the new key"
    assert "{ copy: k.prefix }" in keys and "not shown again" in keys
    assert keys.count("out.key") == 1, "the raw key is read once, to be shown"
    assert not re.search(r"(?:local|session)Storage[^\n]*\.key\b", keys)
    assert "ACTIONS.copyText" in (STATIC / "common.js").read_text()


@mutation(LOGS_FILE, "'<button data-act=\"refreshLogs\" class=\"' + BTN + '\">Refresh</button>' +", "",
          expect="a list tab has no Refresh button: logs")
@mutation(ALERTS_FILE, "'<button data-act=\"alertsFilter\" class=\"' + BTN + '\">Refresh</button></div>' +",
          "'</div>' +", expect="a list tab has no Refresh button: alerts")
def test_every_list_tab_has_a_refresh_button_that_keeps_its_filters():
    missing = [n for n in ("accounts", "jobs", "keys", "logs", "alerts")
               if ">Refresh</button>" not in (STATIC / ("tab-%s.js" % n)).read_text()]
    assert not missing, "a list tab has no Refresh button: %s" % ", ".join(missing)
    logs = (STATIC / "tab-logs.js").read_text()
    refresh = logs[logs.index("ACTIONS.refreshLogs"):]
    assert refresh.split("};")[0].count("offset = 0") == 0 and "readLogFilters();" in refresh.split("};")[0]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_has_a_title_a_language_and_a_viewport(page):
    html = (STATIC / page).read_text()
    assert re.search(r"<html lang=\"en\"", html) and "<title>" in html and 'name="viewport"' in html


def test_the_dashboard_reads_errors_in_the_current_envelope_shape():
    common = (STATIC / "common.js").read_text()
    assert "body.error" in common and "err.message" in common and "err.code" in common and "err.param" in common
    assert not [p.name for p in OWN_JS if re.search(r"\.detail\b", p.read_text())], "an old error shape is read"


def test_the_log_table_and_its_detail_use_the_same_status_formatter_and_blobs_never_feed_a_link_or_preview():
    logs = (STATIC / "tab-logs.js").read_text()
    assert logs.count("statusTone(") >= 3 and "fmtTime(l.at_ms)" in logs
    for path in OWN_JS:
        text = path.read_text()
        for line in (ln.strip() for ln in text.splitlines() if "createObjectURL" in ln):
            assert "window.open(URL.createObjectURL(" in line or line.startswith("link.href = URL.createObjectURL("), \
                "%s: a blob URL feeds something other than a download or a new tab: %s" % (path.name, line)
    jobs = (STATIC / "tab-jobs.js").read_text()
    assert "safeMediaUrl(m.url)" in jobs and "'<img src=\"' + esc(url)" in jobs, "a preview must come from a /m/ link"


def test_a_job_row_opens_its_detail_and_shows_counts_a_refunded_tag_and_a_model_differs_tag():
    jobs = (STATIC / "tab-jobs.js").read_text()
    assert "data-act=\"openJob\"" in jobs and "model differs" in jobs and "'refunded'" in jobs
    assert "key: 'results'" in jobs and "key: 'references'" in jobs
    assert "e.stage === 'recovered' ? 'green'" in jobs


ACCOUNTS_FILE = "packages/relay-core/src/relay_core/admin/static/tab-accounts.js"


@mutation(ACCOUNTS_FILE, "const WAKE_LABEL = 'Wake: clear cooldown and strikes';",
          "const WAKE_LABEL = 'Clear cooldown';", expect="the wake label names only the cooldown")
def test_the_wake_label_and_help_name_both_the_cooldown_and_the_strikes():
    text = (STATIC / "tab-accounts.js").read_text()
    label = re.search(r"const WAKE_LABEL = '([^']*)'", text).group(1)
    help_text = re.search(r"const WAKE_HELP = '([^']*)'", text).group(1)
    assert "cooldown" in label and "strike" in label, "the wake label names only the cooldown: %r" % label
    assert "cooldown" in help_text and "strike" in help_text, "the wake help names only the cooldown: %r" % help_text
    assert "WAKE_HELP" in text.split("button('wakeAccount'", 1)[1].split("\n", 1)[0], "the Wake button has no help text"
