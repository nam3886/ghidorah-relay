"""The admin pages on the API port: served with the exact CSP, only from the static folder (contracts §13, D26)."""

from pathlib import Path

import pytest
from apikit import run_app

from relay_core.testing.mutation import mutation

PAGES_PY = "packages/relay-core/src/relay_core/admin/pages.py"
APP = "packages/relay-core/src/relay_core/api/app.py"
CSP = "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
PAGE_ROUTES = ["/login", "/manage", "/ui/common.js", "/ui/manage.js", "/ui/login.js", "/ui/vendor/admin.css"]
REFERENCE_CSP = CSP.replace("script-src 'self';", "script-src 'self'; style-src 'self' 'unsafe-inline';")
API_ROUTES = ["/healthz", "/openapi.json"]


@mutation(PAGES_PY, 'HEADERS = {"Content-Security-Policy": CSP, ', 'HEADERS = {"X-Frame": CSP, ',
          expect="content-security-policy header")
@pytest.mark.parametrize("path", PAGE_ROUTES)
def test_every_page_route_carries_the_exact_csp_and_an_api_route_does_not(db, path):
    async def scenario(env):
        page = await env.client.get(path)
        assert page.status_code == 200, path
        assert page.headers.get("content-security-policy") == CSP, "content-security-policy header on " + path
        assert page.headers["x-content-type-options"] == "nosniff"
        for api in API_ROUTES:
            assert "content-security-policy" not in (await env.client.get(api)).headers, api

    run_app(db, scenario)


@mutation(PAGES_PY, "body = _FILES.get(name)", "body = (STATIC / name).read_bytes()", expect="outside static")
def test_only_files_of_the_static_folder_are_served(db):
    async def scenario(env):
        for path in ["/ui/tailwind.config.js", "/ui/..%2Ftailwind%2Ftailwind.config.js", "/ui/../pages.py",
                     "/ui/vendor", "/ui/nope.js"]:
            r = await env.client.get(path)
            assert r.status_code == 404, "outside static: " + path

    run_app(db, scenario)


def test_the_root_redirects_to_the_dashboard_and_the_dashboard_needs_no_session_to_load(db):
    async def scenario(env):
        root = await env.client.get("/", follow_redirects=False)
        assert root.status_code == 307 and root.headers["location"] == "/manage"
        assert root.headers.get("content-security-policy") == CSP
        # The page is a shell: the data comes from /admin with the session header, so loading it is public.
        assert (await env.client.get("/manage")).status_code == 200

    run_app(db, scenario)


@mutation(PAGES_PY, 'RedirectResponse("/manage#test", headers=HEADERS)', 'RedirectResponse("/manage", headers=HEADERS)',
          expect="old test link")
def test_the_old_test_link_redirects_to_the_test_tab_of_the_dashboard_with_the_csp(db):
    async def scenario(env):
        old = await env.client.get("/test", follow_redirects=False)
        assert old.status_code == 307 and old.headers["location"] == "/manage#test", "old test link"
        assert old.headers.get("content-security-policy") == CSP
        assert (await env.client.get("/ui/test.html")).status_code == 404

    run_app(db, scenario)


def _ui_assets() -> list[str]:
    root = Path(PAGES_PY).parent / "static"
    return ["/ui/" + p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))
            if p.is_file() and p.suffix in (".js", ".css", ".txt", ".html")]


@mutation(PAGES_PY, "if _path == \"/reference\" else HEADERS", "if _path else HEADERS", expect="looser header")
def test_the_reference_page_alone_may_use_inline_styles_and_keeps_script_src_self(db):
    async def scenario(env):
        ref = await env.client.get("/reference")
        assert ref.status_code == 200
        assert ref.headers["content-security-policy"] == REFERENCE_CSP
        for path in ["/login", "/manage", "/test", "/"]:
            r = await env.client.get(path, follow_redirects=False)
            assert r.headers.get("content-security-policy") == CSP, "looser header on " + path

    run_app(db, scenario)


@mutation(PAGES_PY, "def _serve(name: str, headers: dict[str, str] = HEADERS)",
          "def _serve(name: str, headers: dict[str, str] = REFERENCE_HEADERS)", expect="looser header")
def test_every_ui_asset_keeps_the_exact_csp_even_the_ones_the_reference_page_loads(db):
    async def scenario(env):
        assets = _ui_assets()
        assert "/ui/reference.js" in assets and "/ui/vendor/scalar.min.js" in assets, assets
        for path in assets:
            r = await env.client.get(path)
            assert r.status_code == 200, path
            assert r.headers.get("content-security-policy") == CSP, "looser header on " + path

    run_app(db, scenario)
