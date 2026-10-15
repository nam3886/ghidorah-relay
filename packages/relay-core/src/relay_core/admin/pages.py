"""The admin pages and their static files (contracts §13, §16, D26): `/`, `/login`, `/test`, `/manage`, `/reference` and
`/ui/...` on the API's own port.

Every response of this router carries the admin CSP, so no page can run an inline script or load a foreign asset;
`/reference` alone also allows inline styles (D75). The files are read once at import: they are part of the package
and never change while the app runs. Only the names found in `static/` are served, so a request cannot reach any
other path.
"""

import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, Response

from relay_core.admin.router import CSP
from relay_core.api.errors_http import ApiError

STATIC = Path(__file__).parent / "static"
# The admin is the Vue app built into static/app/: `/manage` is its entry, `/login` and `/test` are routes inside it
# (the redirects below keep the old links working). The entry is also reachable at /ui/app/index.html.
APP_ENTRY = "app/index.html"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".svg": "image/svg+xml"}
HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
# The API reference renderer writes its styles into the page at run time, so /reference alone may use inline styles
# (D75). Its scripts stay `'self'`, and its own assets under /ui keep the exact header above.
REFERENCE_CSP = CSP.replace("script-src 'self';", "script-src 'self'; style-src 'self' 'unsafe-inline';")
REFERENCE_HEADERS = {**HEADERS, "Content-Security-Policy": REFERENCE_CSP}

# Vite names every built asset `<name>-<8 character content hash>.<ext>`; only those are cached for good.
IMMUTABLE = re.compile(r"app/assets/.+-[A-Za-z0-9_-]{8}\.[a-z0-9]+")
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def _load(static: Path) -> dict[str, bytes]:
    """The served files, read once. A package without the built app fails here, at import, not at the first request."""
    files = {p.relative_to(static).as_posix(): p.read_bytes() for p in static.rglob("*")
             if p.is_file() and p.suffix in TYPES}
    if APP_ENTRY not in files:
        raise RuntimeError("the admin web is not built: %s is missing in %s (run scripts/web.sh build)"
                           % (APP_ENTRY, static))
    return files


_FILES = _load(STATIC)


def _serve(name: str, headers: dict[str, str] = HEADERS) -> Response:
    body = _FILES.get(name)
    if body is None:
        raise ApiError("resource.not_found")
    if IMMUTABLE.fullmatch(name):
        headers = {**headers, "Cache-Control": IMMUTABLE_CACHE}
    return Response(body, media_type=TYPES[Path(name).suffix], headers=headers)


router = APIRouter(include_in_schema=False)
for _path, _name in {"/manage": APP_ENTRY, "/reference": "reference.html"}.items():
    _headers = REFERENCE_HEADERS if _path == "/reference" else HEADERS
    router.add_api_route(_path, lambda name=_name, headers=_headers: _serve(name, headers), methods=["GET"])


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/manage", headers=HEADERS)


@router.get("/login")
async def login_page() -> RedirectResponse:
    return RedirectResponse("/manage#/login", headers=HEADERS)


@router.get("/test")
async def test_page() -> RedirectResponse:
    """The model test is a route of the app; old links keep working."""
    return RedirectResponse("/manage#/test", headers=HEADERS)


@router.get("/ui/{name:path}")
async def asset(name: str) -> Response:
    return _serve(name)
