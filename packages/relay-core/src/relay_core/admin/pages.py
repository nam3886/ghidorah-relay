"""The admin pages and their static files (contracts §13, §16, D26): `/`, `/login`, `/manage`, `/test`,
`/reference` and `/ui/...` on the API's own port.

Every response of this router carries the admin CSP, so no page can run an inline script or load a foreign asset;
`/reference` alone also allows inline styles (D75). The files are read once at import: they are part of the package
and never change while the app runs. Only the names found in `static/` are served, so a request cannot reach any
other path.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, Response

from relay_core.admin.router import CSP
from relay_core.api.errors_http import ApiError

STATIC = Path(__file__).parent / "static"
PAGES = {"/login": "login.html", "/manage": "manage.html", "/test": "test.html", "/reference": "reference.html"}
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
# The API reference renderer writes its styles into the page at run time, so /reference alone may use inline styles
# (D75). Its scripts stay `'self'`, and its own assets under /ui keep the exact header above.
REFERENCE_CSP = CSP.replace("script-src 'self';", "script-src 'self'; style-src 'self' 'unsafe-inline';")
REFERENCE_HEADERS = {**HEADERS, "Content-Security-Policy": REFERENCE_CSP}

router = APIRouter(include_in_schema=False)
_FILES = {p.relative_to(STATIC).as_posix(): p.read_bytes() for p in STATIC.rglob("*")
          if p.is_file() and p.suffix in TYPES}


def _serve(name: str, headers: dict[str, str] = HEADERS) -> Response:
    body = _FILES.get(name)
    if body is None:
        raise ApiError("resource.not_found")
    return Response(body, media_type=TYPES[Path(name).suffix], headers=headers)


for _path, _name in PAGES.items():
    _headers = REFERENCE_HEADERS if _path == "/reference" else HEADERS
    router.add_api_route(_path, lambda name=_name, headers=_headers: _serve(name, headers), methods=["GET"])


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/manage", headers=HEADERS)


@router.get("/ui/{name:path}")
async def asset(name: str) -> Response:
    return _serve(name)
