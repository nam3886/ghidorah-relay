"""The admin pages on the API port: served with the exact CSP, only from the static folder (contracts §13, D26)."""

from pathlib import Path

import pytest
from apikit import run_app

from relay_core.testing.mutation import mutation

PAGES_PY = "packages/relay-core/src/relay_core/admin/pages.py"
APP = "packages/relay-core/src/relay_core/api/app.py"
CSP = "default-src 'self'; script-src 'self'; object-src 'none'; frame-ancestors 'none'"
PAGE_ROUTES = ["/login", "/manage", "/ui/common.js", "/ui/manage.js", "/ui/login.js", "/ui/vendor/admin.css",
               "/ui/app/index.html", "/ui/app/theme-init.js"]
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
                     "/ui/vendor", "/ui/nope.js", "/ui/app", "/ui/app/%2e%2e/login.html",
                     "/ui/app/%2e%2e/%2e%2e/pages.py",
                     "/ui/app/assets/nope-12345678.js", "/ui/app/index.html.map"]:
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


def _app_assets() -> list[Path]:
    return sorted((Path(PAGES_PY).parent / "static" / "app" / "assets").glob("*"))


@mutation(PAGES_PY, 'IMMUTABLE_CACHE = "public, max-age=31536000, immutable"', 'IMMUTABLE_CACHE = "no-cache"',
          expect="a hashed asset is not immutable")
@mutation(PAGES_PY, "    if IMMUTABLE.fullmatch(name):", "    if name.startswith(\"app/\"):",
          expect="a file that is not a hashed asset is immutable")
def test_only_hash_named_files_under_app_assets_are_immutable_and_everything_else_is_no_cache(db):
    async def scenario(env):
        assets = _app_assets()
        assert assets, "the built app has no assets"
        for asset in assets:
            r = await env.client.get("/ui/app/assets/" + asset.name)
            assert r.headers["cache-control"] == "public, max-age=31536000, immutable", \
                "a hashed asset is not immutable: " + asset.name
        for path in ["/ui/app/index.html", "/ui/app/theme-init.js", "/ui/common.js", "/ui/reference.js", "/manage",
                     "/reference"]:
            r = await env.client.get(path)
            assert r.headers["cache-control"] == "no-cache", "a file that is not a hashed asset is immutable: " + path

    run_app(db, scenario)


@pytest.mark.parametrize("name, immutable", [
    ("app/assets/index-DlNMMzRU.js", True), ("app/assets/FramesView-D45aiX6-.js", True),
    ("app/assets/index.js", False), ("app/assets/index-short.js", False), ("app/assets/x-12345678", False),
    ("app/theme-init.js", False), ("app/index.html", False), ("assets/index-DlNMMzRU.js", False),
    ("app/assets/index-DlNMMzRU.js.map", False), ("vendor/scalar.min.js", False)])
def test_the_immutable_rule_needs_the_assets_folder_and_a_content_hash_in_the_name(name, immutable):
    from relay_core.admin import pages

    assert bool(pages.IMMUTABLE.fullmatch(name)) is immutable, name


def test_every_file_under_app_assets_has_a_content_hash_in_its_name():
    from relay_core.admin import pages

    for asset in _app_assets():
        assert pages.IMMUTABLE.fullmatch("app/assets/" + asset.name), "an asset without a hash: " + asset.name


@mutation(PAGES_PY, "    if APP_ENTRY not in files:", "    if False:", expect="a package without the app booted")
def test_a_package_without_the_built_app_fails_at_import_and_one_with_it_loads(tmp_path):
    from relay_core.admin import pages

    (tmp_path / "login.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not built"):
        pages._load(tmp_path)
        raise AssertionError("a package without the app booted")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "index.html").write_text("<html></html>", encoding="utf-8")
    assert "app/index.html" in pages._load(tmp_path)               # the negative control: with the entry it loads


def _client(new_admin: bool):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from relay_core.admin import pages

    app = FastAPI()
    app.include_router(pages.build_router(new_admin))
    return TestClient(app, follow_redirects=False)


def test_the_switch_is_off_in_the_shipped_code_until_the_cut_over():
    from relay_core.admin import pages

    assert pages.NEW_ADMIN is False, "the old admin is served until the cut-over"
    assert any(r.path == "/login" for r in pages.router.routes) and not _client(False).get("/login").is_redirect


@mutation(PAGES_PY, 'pages = {"/manage": APP_ENTRY} if new_admin else LEGACY_PAGES',
          'pages = {"/manage": APP_ENTRY} if not new_admin else LEGACY_PAGES', expect="the switch position")
def test_switch_off_serves_the_old_pages_and_redirects_and_switch_on_serves_the_vue_entry_and_its_redirects():
    old, new = _client(False), _client(True)
    legacy_manage = (Path(PAGES_PY).parent / "static" / "manage.html").read_bytes()
    app_entry = (Path(PAGES_PY).parent / "static" / "app" / "index.html").read_bytes()
    assert old.get("/manage").content == legacy_manage, "the switch position: off must serve the old dashboard"
    assert new.get("/manage").content == app_entry, "the switch position: on must serve the Vue entry"
    assert old.get("/login").status_code == 200 and old.get("/login").content != app_entry
    for client, login, test in ((old, None, "/manage#test"), (new, "/manage#/login", "/manage#/test")):
        r = client.get("/test")
        assert r.status_code == 307 and r.headers["location"] == test, "the switch position: " + test
        assert r.headers["content-security-policy"] == CSP
        root = client.get("/")
        assert root.status_code == 307 and root.headers["location"] == "/manage"
        if login:
            r = client.get("/login")
            assert r.status_code == 307 and r.headers["location"] == login, "the switch position: " + login
            assert r.headers["content-security-policy"] == CSP
    # the reference page and the app files are served in both positions, the reference alone with the looser header
    for client in (old, new):
        assert client.get("/reference").headers["content-security-policy"] == REFERENCE_CSP
        r = client.get("/ui/app/index.html")
        assert r.status_code == 200 and r.content == app_entry and r.headers["content-security-policy"] == CSP
        assert r.headers["cache-control"] == "no-cache"


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
