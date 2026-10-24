"""The Ghidorah brand in Chrome against the real app (browser tier), under the CSP watch: the page title, the favicon
served from the same origin, the header's mark and word as the Ledger design draws them (the mark on the word's optical
middle) from a phone width to a desktop width, the login mark and heading, and the SVG files themselves (no script, no
foreign reference, no data: URL)."""

import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
from patchright.sync_api import expect

pytestmark = pytest.mark.browser

STATIC = Path(__file__).resolve().parents[2] / "packages/relay-core/src/relay_core/admin/static"
PUBLIC = Path(__file__).resolve().parents[2] / "apps/admin-web/public"
SVG_FORBIDDEN = re.compile(r"<script|<style|<image|<foreignObject|href\s*=|url\(|data:|on[a-z]+\s*=|javascript:", re.I)
ONLY_HOST = re.compile(r"https?://(?!www\.w3\.org/2000/svg)", re.I)


def test_the_page_is_titled_ghidorah_admin_and_its_favicon_is_a_same_origin_svg(app, app_page):
    page = app_page
    page.goto(app.url + "#/login")
    expect(page).to_have_title("Ghidorah admin")
    href = page.locator("link[rel=icon]").get_attribute("href")
    assert href, "no favicon link"
    full = page.evaluate("h => new URL(h, location.href).href", href)
    assert urlparse(full).netloc == urlparse(page.url).netloc, "the favicon is not on this origin: " + full
    served = page.request.get(full)
    assert served.status == 200 and served.headers["content-type"] == "image/svg+xml", (
        served.status, served.headers.get("content-type"))
    assert b"<svg" in served.body()


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_login_page_has_the_mark_in_its_tile_and_the_heading(app, app_page, color_scheme):
    page = app_page
    page.goto(app.url + "#/login")
    expect(page.get_by_role("heading", name="Sign in to Ghidorah")).to_be_visible()
    mark = page.get_by_role("main").locator(".login-mark svg")
    expect(mark).to_be_visible()
    assert mark.get_attribute("aria-hidden") == "true", "the heading names the page; the mark is decoration"
    box = mark.bounding_box()
    assert round(box["width"]) == 30 and round(box["height"]) == 30, box
    tile = page.evaluate("el => { const r = el.parentElement.getBoundingClientRect(); return [r.width, r.height]; }",
                         mark.element_handle())
    assert tile == [52, 52], tile
    brand = page.evaluate("getComputedStyle(document.querySelector('.login-mark svg')).fill")
    assert brand == ("rgb(139, 92, 246)" if color_scheme == "dark" else "rgb(106, 53, 232)"), brand


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_header_logo_is_the_designs_mark_and_word_with_the_mark_on_the_words_optical_middle(
        app, app_session, color_scheme):
    page = app_session
    home = page.get_by_role("banner").get_by_role("link", name="Ghidorah admin home")
    mark, word = home.locator("svg.brand__mark"), home.locator(".brand__word")
    for width in (1279, 768, 390):                  # a tablet and a phone: the mark alone before the page title
        page.set_viewport_size({"width": width, "height": 800})
        expect(word).to_be_hidden()
        box = mark.bounding_box()
        assert round(box["width"]) == 24 and round(box["height"]) == 24, (width, box)
    for width in (1440, 1280):
        page.set_viewport_size({"width": width, "height": 800})
        expect(word).to_have_text("Ghidorah")
        expect(word).to_be_visible()
        box = mark.bounding_box()
        assert round(box["width"]) == 24 and round(box["height"]) == 24, (width, box)
        got = page.evaluate(BRAND_CENTRES, home.element_handle())
        assert got["font"] == ["800", "16px", "-0.32px"] and got["family"].startswith("Manrope"), (width, got)
        assert abs(got["gap"] - 10) < 0.6, (width, got)
        assert abs(got["icon"] - got["text"]) <= 1, (
            "the mark's centre is %.2f px off the word's at %d px wide" % (got["icon"] - got["text"], width))
    expect(page.get_by_role("banner").get_by_text("Relay")).to_have_count(0)
    page.csp.check()


# The vertical centre of the mark's drawing and the visual centre of the word: its baseline (a zero-size inline box set
# on it) less half of the glyphs' ink above and below it, measured by the canvas in the word's own font; in px. The
# font is ready before anything is read.
BRAND_CENTRES = """async home => {
  await document.fonts.ready;
  const word = home.querySelector(".brand__word"), mark = home.querySelector("svg.brand__mark");
  const probe = document.createElement("span");
  probe.style.display = "inline-block";
  probe.style.width = "0";
  probe.style.height = "0";
  word.appendChild(probe);
  const baseline = probe.getBoundingClientRect().top;
  probe.remove();
  const css = getComputedStyle(word);
  const ctx = document.createElement("canvas").getContext("2d");
  ctx.font = css.fontWeight + " " + css.fontSize + " " + css.fontFamily;
  const ink = ctx.measureText(word.textContent);
  const icon = mark.querySelector("g").getBoundingClientRect();
  return {
    icon: icon.top + icon.height / 2,
    text: baseline - (ink.actualBoundingBoxAscent - ink.actualBoundingBoxDescent) / 2,
    gap: word.getBoundingClientRect().left - mark.getBoundingClientRect().right,
    font: [css.fontWeight, css.fontSize, css.letterSpacing],
    family: css.fontFamily,
  };
}"""


def test_every_svg_the_app_ships_has_no_script_style_link_or_data_url():
    files = sorted(STATIC.glob("app/**/*.svg")) + sorted((PUBLIC / "brand").glob("*.svg"))
    for prefix in ("ghidorah-favicon", "ghidorah-logo-dark", "ghidorah-logo-light"):
        shipped = [f.name for f in files]
        assert any(n.startswith(prefix) for n in shipped), "%s is not shipped: %s" % (prefix, shipped)
    for svg in files:
        text = svg.read_text()
        assert not SVG_FORBIDDEN.search(text), "%s holds something a CSP-clean SVG must not: %s" % (
            svg.name, SVG_FORBIDDEN.search(text).group(0))
        assert not ONLY_HOST.search(text), "%s references a remote host" % svg.name
    # the planted SVG proves the guard sees each thing
    for bad in ('<svg><script>1</script></svg>', '<svg><a href="x"/></svg>', '<svg onload="x()"/>',
                '<svg><style>a{}</style></svg>', '<svg><image href="data:image/png;base64,AA"/></svg>'):
        assert SVG_FORBIDDEN.search(bad), bad
    assert ONLY_HOST.search('<svg><image href="https://evil.example/x.png"/></svg>')
