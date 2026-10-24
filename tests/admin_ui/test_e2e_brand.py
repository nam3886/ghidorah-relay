"""The Ghidorah brand in Chrome against the real app (browser tier), under the CSP watch: the page title, the favicon
served from the same origin, the logo at a phone width and a desktop width, the login heading, and the SVG files
themselves (no script, no foreign reference, no data: URL)."""

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


def test_the_login_page_has_the_mark_and_the_heading(app, app_page):
    page = app_page
    page.goto(app.url + "#/login")
    expect(page.get_by_role("heading", name="Sign in to Ghidorah")).to_be_visible()
    logo = page.get_by_role("main").get_by_role("img", name="Ghidorah", exact=True)
    expect(logo).to_be_visible()
    box = logo.bounding_box()
    assert round(box["width"]) == 30 and round(box["height"]) == 30, box


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_the_header_logo_is_the_wide_one_at_every_width_26_px_high_and_24_on_a_phone(app, app_session, color_scheme):
    page = app_session
    word = "dark" if color_scheme == "dark" else "light"
    logo = page.get_by_role("banner").get_by_role("img", name="Ghidorah", exact=True)
    for width, height in ((1280, 26), (768, 26), (390, 24)):
        page.set_viewport_size({"width": width, "height": 800})
        expect(logo).to_be_visible()
        box = logo.bounding_box()
        assert abs(box["height"] - height) < 0.6, (width, box)
        assert box["width"] >= (160 if height == 26 else 148), (width, box)
        assert "ghidorah-logo-%s" % word in logo.get_attribute("src"), logo.get_attribute("src")
        assert page.evaluate("el => el.naturalWidth", logo.element_handle()) > 0, "the logo did not load"
    expect(page.get_by_role("banner").get_by_text("Relay")).to_have_count(0)
    page.csp.check()


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
