"""The request log (contracts §4.4, §13): one `request_logs` row per request a known key made.

Pure ASGI middleware, so it sees every outcome: a response, a framework error and a crash (answered here with the
500 envelope). Rules:
- only a request that carried a known key is logged, so anyone can send junk without filling the log; a disabled
  key is known (its whole hash matched), so its 403 is logged under it; a 422 or a broken JSON body is logged when
  the key is known (the validation handler resolves it);
- a read that worked (GET, 2xx) is not logged; every failure and every write is;
- the body and the error answer go through `redact_log`: secrets masked, every string over 2,000 characters stored
  as `[omitted len=N]`;
- a route whose body holds a credential depends on `deps.skip_body_log`: its row keeps no body;
- a logging failure never fails the request.
A client hang-up (`ClientDisconnect`) is a normal event: logged at INFO, row status 499, never re-raised.
A route may set `request.state.job_id` and `request.state.provider` for the row.
"""

import json
import logging
import time
import uuid

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from relay_core.api.errors_http import crash_response
from relay_core.redact import redact_log

log = logging.getLogger("relay.api")

MAX_BODY_BYTES = 1 << 20      # ponytail: a larger body is not parsed for the log; raise it if a real client needs it
MAX_ANSWER_BYTES = 8 << 10    # the error answers we send are small; only they are kept
_READS = {"GET", "HEAD", "OPTIONS"}
CLIENT_CLOSED = 499

_INSERT = ("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, error_code, "
           "body_redacted, response_redacted) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)")


def _json_or_none(raw: bytes):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _error_code(answer) -> str | None:
    """The code of either error shape (OpenAI envelope, Gemini body)."""
    err = answer.get("error") if isinstance(answer, dict) else None
    if not isinstance(err, dict):
        return None
    if isinstance(err.get("code"), str):
        return err["code"]
    details = err.get("details")
    return details[0].get("reason") if isinstance(details, list) and details and isinstance(details[0], dict) else None


class RequestLogMiddleware:
    def __init__(self, app: ASGIApp, pool: AsyncConnectionPool):
        self.app, self.pool = app, pool

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})          # routes write request.state.* into this very dict
        seen = {"status": 0, "body": bytearray(), "over": 0, "answer": bytearray(), "started": False}

        async def tee_receive() -> Message:
            msg = await receive()
            if msg["type"] == "http.request":
                chunk = msg.get("body", b"")
                room = MAX_BODY_BYTES - len(seen["body"])
                seen["body"] += chunk[:max(room, 0)]
                seen["over"] += max(len(chunk) - max(room, 0), 0)
            return msg

        async def tee_send(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                seen["status"], seen["started"] = msg["status"], True
            elif msg["type"] == "http.response.body" and seen["status"] >= 400:
                seen["answer"] += msg.get("body", b"")[:MAX_ANSWER_BYTES]
            await send(msg)

        started = time.monotonic()
        try:
            await self.app(scope, tee_receive, tee_send)
        except ClientDisconnect:
            # The client hung up mid-request: nobody is left to answer and nothing failed on our side. Not an
            # error, so no traceback and no 500; the row says 499 (client closed request) when no answer was begun.
            log.info("client disconnected on %s %s", scope["method"], scope["path"])
            seen["status"] = seen["status"] or CLIENT_CLOSED
        except Exception:
            log.exception("unhandled error on %s %s", scope["method"], scope["path"])
            if seen["started"]:
                raise                              # the answer is already on the wire; nothing to rewrite
            await crash_response(scope["path"])(scope, receive, tee_send)
        await self._write(scope, seen, int((time.monotonic() - started) * 1000))

    async def _write(self, scope: Scope, seen: dict, duration_ms: int) -> None:
        state = scope["state"]
        key_id = state.get("key_id")
        status = seen["status"]
        if key_id is None or (scope["method"] in _READS and status < 400):
            return
        route = scope.get("route")
        where = getattr(route, "path", None) or scope["path"][:200]
        answer = _json_or_none(bytes(seen["answer"])) if seen["answer"] else None
        try:
            if state.get("log_body") is False:
                body = {}
            elif seen["over"]:
                body = {"_body": "[omitted len=%d]" % (len(seen["body"]) + seen["over"])}
            else:
                parsed = _json_or_none(bytes(seen["body"])) if seen["body"] else {}
                body = redact_log(parsed) if isinstance(parsed, (dict, list)) else {"_unparsed": True}
            async with self.pool.connection() as conn:
                await conn.execute(_INSERT, (
                    key_id, state.get("provider"), "%s %s" % (scope["method"], where), status,
                    _uuid_or_none(state.get("job_id")), duration_ms, _error_code(answer),
                    Jsonb(body), Jsonb(redact_log(answer)) if answer is not None else None))
        except Exception:
            log.warning("request log row not written", exc_info=True)


def _uuid_or_none(value) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
