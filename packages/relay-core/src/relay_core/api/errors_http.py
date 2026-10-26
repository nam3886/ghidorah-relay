"""Every error the HTTP layer sends, in one place.

Routes raise `ApiError(code, param)`; the status, `type` and text come from the code's `Rule`, never from the call
site. Errors the framework makes itself (404, 405, 422) and crashes go through the same renderer, so no path can
leave with another shape. The admin tree answers the internal code and message. A client sees only `errors.shown`:
a client code with its message, any other code as its neutral public code and customer sentence; never an internal
code, an exception message, an engine text or an upstream text.

Two shapes: OpenAI style `{"error": {"code", "message", "type", "param"}}` everywhere, and the Gemini body on the
Gemini doors (`/v1beta…`, `/models…`).
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from relay_core.errors import INTERNAL, envelope, http_status, rule, shown
from relay_core.redact import redact_text

log = logging.getLogger("relay.api")

# Gemini's gRPC-style `status` by HTTP status.
_GRPC = {400: "INVALID_ARGUMENT", 401: "UNAUTHENTICATED", 403: "PERMISSION_DENIED", 404: "NOT_FOUND",
         405: "INVALID_ARGUMENT", 409: "ABORTED", 429: "RESOURCE_EXHAUSTED", 500: "INTERNAL", 502: "UNAVAILABLE",
         503: "UNAVAILABLE", 504: "DEADLINE_EXCEEDED"}
# Errors the framework raises carry no code: the status picks one. A safety net, not a destination: every route
# of ours raises ApiError with a specific code.
_BY_STATUS = {401: "auth.invalid_key", 403: "auth.forbidden", 404: "resource.not_found"}
# A bad field names its own code (a wrong `duration` is not the catch-all `input.invalid`).
_FIELD_CODE = {"duration": "input.bad_duration", "ratio": "input.bad_ratio", "model": "input.bad_model",
               "style": "input.bad_style", "image": "input.image_unreadable", "images": "input.image_unreadable"}
_LOC_ROOTS = {"body", "query", "path", "header", "cookie"}


MAX_PARAM = 200      # a field name of ours is far shorter; a longer one is the client's own text, not echoed


def clean_param(param: str | None) -> str | None:
    """A `param` the client sees: a field name it sent may be any text (names it), so a long one is cut to its
    length and a short one goes through the redactor (I24)."""
    if param is None:
        return None
    return "[omitted len=%d]" % len(param) if len(param) > MAX_PARAM else redact_text(param)


class ApiError(Exception):
    """An error with a registered code. `param` names the offending request field; `headers` ride along."""

    def __init__(self, code: str, param: str | None = None, *, headers: dict[str, str] | None = None):
        super().__init__(code)
        self.code, self.param, self.headers = code, clean_param(param), headers


def is_gemini_path(path: str) -> bool:
    return path.startswith(("/v1beta/", "/models/")) or path in ("/v1beta", "/models")


FACADE_CREATE_PATH = "/v1/chat/completions"
NO_RETRY = ("x-should-retry", "false")        # the openai SDK obeys it: no second send, so no second job


def is_facade_path(path: str) -> bool:
    return path == FACADE_CREATE_PATH or is_gemini_path(path)


def gemini_body(code: str, param: str | None = None) -> dict:
    """The Gemini error body. The text and the shown code are `errors.shown`, as in the OpenAI envelope."""
    shown_code, message = shown(code)
    status = http_status(code)
    return {"error": {"code": status, "message": message, "status": _GRPC.get(status, "UNKNOWN"),
                      "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": shown_code,
                                   "domain": "relay", "metadata": {} if param is None else {"param": param}}]}}


def error_response(path: str, code: str, param: str | None = None, *, status: int | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    """The response for a code, in the shape the path's door speaks. `status` overrides the rule's only for errors
    the framework made (a 405 keeps its status and its `Allow` header)."""
    admin = path == "/admin" or path.startswith("/admin/")         # the operator sees the internal code
    body = gemini_body(code, param) if is_gemini_path(path) else envelope(code, param, internal=admin)
    if is_facade_path(path):                      # an SDK must not re-send a request whose job exists
        headers = {**(headers or {}), NO_RETRY[0]: NO_RETRY[1]}
    if status is not None and is_gemini_path(path):
        body["error"]["code"] = status
        body["error"]["status"] = _GRPC.get(status, "UNKNOWN")
    return JSONResponse(body, status_code=status or http_status(code), headers=headers)


def crash_response(path: str) -> JSONResponse:
    return error_response(path, INTERNAL)


def field_param(loc: tuple[Any, ...], kind: str = "") -> str | None:
    """The request field a validation error is about: `("body", "images", 0)` -> `images.0`. A body that is not
    JSON has no field (its `loc` is the character offset)."""
    if kind == "json_invalid":
        return None
    parts = [str(p) for p in (loc[1:] if loc and loc[0] in _LOC_ROOTS else loc)]
    return clean_param(".".join(parts) or None)


def validation_code(error: dict) -> str:
    """The code for one validation error: a validator that names a registered code in `type` wins, then the field."""
    if rule(str(error.get("type"))).code == error.get("type"):
        return error["type"]
    for part in error.get("loc") or ():
        if part in _FIELD_CODE:
            return _FIELD_CODE[part]
    return "input.invalid"


def install(app: FastAPI) -> None:
    """Register the handlers. Called once by `create_app`."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        request.state.error_code = exc.code          # the request log keeps the internal code (`request_log`)
        return error_response(request.url.path, exc.code, exc.param, headers=exc.headers)

    # Starlette's class, not FastAPI's: FastAPI's subclasses it, and the 404 of an unknown path and the 405 of a
    # wrong method are raised by Starlette itself.
    @app.exception_handler(StarletteHTTPException)
    async def _framework(request: Request, exc: StarletteHTTPException):
        # No route ran, so no key was resolved: a known key's wrong path or method is still logged under it.
        from relay_core.api.deps import resolve_key_quietly
        await resolve_key_quietly(request)
        code = _BY_STATUS.get(exc.status_code) or ("input.invalid" if exc.status_code < 500 else INTERNAL)
        request.state.error_code = code
        return error_response(request.url.path, code, status=exc.status_code if code == "input.invalid" else None,
                              headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Let the request log learn who sent this: a body that is not JSON fails before the key dependency runs.
        from relay_core.api.deps import resolve_key_quietly
        await resolve_key_quietly(request)
        first = (exc.errors() or [{}])[0]
        param = field_param(tuple(first.get("loc") or ()), str(first.get("type")))
        return error_response(request.url.path, validation_code(first), param)
