"""Test kit for gpt: planted secrets, a Cookie-Editor export of chatgpt.com, a fake curl_cffi `AsyncSession` fed from a
route table, a recording `RunCtx`, planned jobs and the recorded spike captures (fixtures/gpt, read at call time).

Every request the fake session answers and every platform step the ctx takes go, in order, into one shared `log`, so a
test reads the whole run as one sequence. Planted values all hold `PLANTED`: `assert_no_secret` proves none of them
reached a text the run produced."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from relay_core.providers import AccountView, CreateRequest, JobView, MediaIn, MediaRef, Proxy
from relay_core.testing.fake_provider import FakeSettings
from relay_gpt.constants import SESSION_COOKIE
from relay_gpt.credential import parse_credential
from relay_gpt.spec import SPEC

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "gpt" / "spike-261104"
NOW = 1_793_000_000.0             # the wall clock of the runs

# ---- planted secrets: never in a log, an error, an event, a state or a result -----------------------------------
SESSION0 = "eyJ-PLANTED-session-0-0123456789"
SESSION1 = "eyJ-PLANTED-session-1-0123456789"
USER_ID = "user-UID1"
ACCOUNT_ID = "acc-00000000-0000-4000-8000-000000000001"
SENTINEL = "gAAAA-PLANTED-sentinel"
CONDUIT = "conduit-PLANTED"
TURNSTILE = "ts-PLANTED"
PROXY = Proxy("10.0.0.9", 3128, "user:PLANTED-pw")


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()


def access_token(*, exp: float = NOW + 10 * 86400, plan: str = "plus", user: str = USER_ID,
                 account: str = ACCOUNT_ID) -> str:
    """A three-part JWT shaped like chatgpt.com's: the payload names the user, the workspace and its plan; the
    signature is planted."""
    payload = {"exp": int(exp), "iat": int(exp) - 10 * 86400,
               "https://api.openai.com/auth": {"chatgpt_user_id": user, "chatgpt_account_id": account,
                                               "chatgpt_plan_type": plan}}
    return "%s.%s.sig-PLANTED-0123456789" % (_b64({"alg": "RS256", "typ": "JWT"}), _b64(payload))


ACCESS = access_token()
PLANTED: tuple[str, ...] = (SESSION0, SESSION1, ACCESS, SENTINEL, CONDUIT, TURNSTILE, PROXY.auth)


def assert_no_secret(*texts: object) -> None:
    """No planted value (nor any piece of the access token) in any of `texts` (each read as `str`)."""
    for text in texts:
        text = str(text)
        assert "PLANTED" not in text, "a planted secret leaked: %r" % text[:200]
        for piece in (*PLANTED, *ACCESS.split(".")[1:]):
            assert piece not in text, "a planted secret leaked: %r" % text[:200]


def fixture(name: str) -> dict:
    """A recorded capture of the 04/11 spike by its file stem (fixtures/gpt/spike-261104/<name>.json)."""
    return json.loads((FIX / (name + ".json")).read_text(encoding="utf-8"))


# ---- the account ------------------------------------------------------------------------------------------------
def export() -> list[dict]:
    """A Cookie-Editor export of chatgpt.com: the split session, two site cookies, and two strangers."""
    return [
        {"domain": "chatgpt.com", "name": SESSION_COOKIE + ".0", "value": SESSION0, "path":"/", "hostOnly": True,
         "secure": True, "httpOnly": True, "expirationDate": 1800000000.5},
        {"domain": "chatgpt.com", "name": SESSION_COOKIE + ".1", "value": SESSION1, "path":"/", "hostOnly": True,
         "secure": True, "httpOnly": True, "expirationDate": 1800000000.5},
        {"domain": ".chatgpt.com", "name": "oai-did", "value": "did-0000-0000-0001", "path":"/", "hostOnly": False},
        {"domain": ".chatgpt.com", "name": "__cf_bm", "value": "cf-bm-value", "path":"/", "hostOnly": False},
        {"domain": ".openai.com", "name": "oai-sc", "value": "stranger-openai", "path":"/", "hostOnly": False},
        {"domain": ".google.com", "name": "NID", "value": "stranger-google", "path":"/", "hostOnly": False},
    ]


BLOB = parse_credential(json.dumps(export()).encode()).blob


def settings(**over) -> FakeSettings:
    """The spec's defaults (and the core's), with `gpt.<key>` overridden by `key=value`."""
    return FakeSettings(SPEC, **{"gpt__" + k: v for k, v in over.items()})


# ---- a fake curl_cffi AsyncSession --------------------------------------------------------------------------------
class Cut(Exception):
    """The peer broke the stream (what a dropped connection raises in a test)."""


class Resp:
    """One answer. `text` is the body; `lines` makes it a stream (`aiter_lines` yields them as bytes); `cut` breaks
    the stream with `error` after that many lines; `cookies` are set on the session as a Set-Cookie would."""

    def __init__(self, status: int = 200, headers: dict | None = None, text: str = "", lines: list[str] | None = None,
                 *, json_body: object = None, cut: int | None = None, error: BaseException | None = None,
                 cookies: dict[str, str] | None = None, content: bytes | None = None):
        self.status_code = status
        self.headers = dict(headers or {})
        self.text = json.dumps(json_body) if json_body is not None else text
        self.lines = lines
        self.cut, self.error = cut, error
        self.cookies = dict(cookies or {})
        self.content = content if content is not None else self.text.encode()
        self.closed = False

    def json(self):
        return json.loads(self.text)

    async def aiter_lines(self):
        lines = self.lines if self.lines is not None else self.text.splitlines()
        for line in lines if self.cut is None else lines[:self.cut]:
            yield line.encode()
        if self.cut is not None:
            raise self.error or Cut("stream cut")

    async def aiter_content(self):
        yield self.content

    async def aclose(self):
        self.closed = True


class Cookies:
    """curl_cffi's `session.cookies`: `set(name, value, domain, path)`, `get(name)`, and `jar` (cookie objects)."""

    def __init__(self):
        self.jar: list[SimpleNamespace] = []

    def set(self, name, value, domain="", path="/"):
        self.jar = [c for c in self.jar if c.name != name] + [SimpleNamespace(name=name, value=value, domain=domain,
                                                                             path=path)]

    def get(self, name, default=None):
        return next((c.value for c in self.jar if c.name == name), default)


class FakeSession:
    """A curl_cffi `AsyncSession` stand-in answering from `routes`: `{(METHOD, path): answer}` where `path` is the
    URL's path (a key may also be `host + path`, or end in `*` for a prefix). An answer is a `Resp`, an exception
    (raised), a callable of the request (a SimpleNamespace with method, url, path, headers, data, json) or a list
    taken in order (the last repeats). No route: 404. Every request is logged as (METHOD, url, headers) in `log`;
    a request that would follow a redirect fails the test."""

    def __init__(self, routes: dict | None = None, log: list | None = None, *, proxies=None, impersonate=None):
        self.routes = dict(routes or {})
        self.log = log if log is not None else []
        self.requests: list[SimpleNamespace] = []
        self.cookies = Cookies()
        self.proxies, self.impersonate = proxies, impersonate
        self.closed = False

    def _answer(self, method: str, url: str):
        parts = urlsplit(url)
        for key in ((method, parts.path), (method, parts.netloc + parts.path)):
            if key in self.routes:
                return self.routes[key]
        for (m, path), answer in self.routes.items():
            if m == method and path.endswith("*") and (parts.path.startswith(path[:-1])
                                                       or (parts.netloc + parts.path).startswith(path[:-1])):
                return answer
        return Resp(404, text='{"detail":"not found"}')

    async def request(self, method, url, *, headers=None, data=None, json=None, timeout=None, allow_redirects=True,
                      stream=False, **kw):
        method = method.upper()
        assert allow_redirects is False, "a request may follow a redirect: %s %s" % (method, url)
        req = SimpleNamespace(method=method, url=url, path=urlsplit(url).path, headers=dict(headers or {}),
                              data=data, json=json, timeout=timeout, stream=stream,
                              cookies={c.name: c.value for c in self.cookies.jar})
        self.requests.append(req)
        self.log.append((method, url, req.headers))
        answer = self._answer(method, url)
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer) and not isinstance(answer, Resp):
            answer = answer(req)
        if isinstance(answer, BaseException):
            raise answer
        for name, value in answer.cookies.items():
            self.cookies.set(name, value, urlsplit(url).hostname or "")
        return answer

    async def get(self, url, **kw):
        return await self.request("GET", url, **kw)

    async def post(self, url, **kw):
        return await self.request("POST", url, **kw)

    async def put(self, url, **kw):
        return await self.request("PUT", url, **kw)

    async def close(self):
        self.closed = True


# ---- the run context ----------------------------------------------------------------------------------------------
class Ctx:
    """The RunCtx an engine sees. Every platform step goes to the shared `log` ("report_ref", "mark_sent", "renew");
    `refs`, `events`, `evidences` and `renewed` keep what was written. `fail` names a step that raises (a lost
    lease)."""

    def __init__(self, log: list | None = None, *, blob: bytes | None = None, proxy: Proxy | None = PROXY,
                 settings_=None, state: dict | None = None, account_id: str = "acc-1", fail: str | None = None):
        self.log = log if log is not None else []
        self.account = AccountView(account_id, "gpt-1", BLOB if blob is None else blob, 1, proxy, state or {},
                                   {"image": None}, "pk")
        self.browser_token = None
        self.settings = settings_ if settings_ is not None else settings()
        self.refs: list[tuple[str, dict]] = []
        self.events: list[tuple[str, str, str]] = []
        self.evidences: list[tuple[str, bytes, str]] = []
        self.renewed: list[bytes] = []
        self.stored = self.account.credential
        self.fail = fail

    def _step(self, name: str) -> None:
        self.log.append(name)
        if self.fail == name:
            raise RuntimeError("lease lost")

    async def fence(self):
        self._step("fence")

    async def mark_sent(self):
        self._step("mark_sent")

    async def report_ref(self, ref, state):
        self.refs.append((ref, dict(state)))
        self._step("report_ref")

    async def event(self, text, stage, source):
        self.events.append((text, stage, source))

    async def evidence(self, name, data, mime):
        self.evidences.append((name, data, mime))

    async def renew_credential(self, blob, merge=None):
        """Stores `merge(stored, blob)` (or `blob`) and returns what is stored now."""
        self.stored = merge(self.stored, blob) if merge is not None else blob
        self.renewed.append(self.stored)
        self._step("renew")
        return self.stored


# ---- jobs -----------------------------------------------------------------------------------------------------
INPUT_REF = MediaRef(0, "inputs/one.png", "image/png", 9)


def job(prompt: str = "a red paper boat", *, ratio: str | None = None, image: bool = False, state: dict | None = None,
        sent_at=None, external_ref: str | None = None) -> JobView:
    """A job as `SPEC.plan` makes it: text to image, or an edit of one input image (`image=True`)."""
    images = (MediaIn("0" * 64, "image/png", 9),) if image else ()
    plan = SPEC.plan(CreateRequest(model="gpt-image", prompt=prompt, ratio=ratio, images=images), settings(), {})

    async def inputs():
        return [INPUT_REF] if image else []
    return JobView("job-1", "gpt", plan.operation, plan.model, plan.params, prompt, "7100", prompt, None, sent_at,
                   external_ref, state or {}, inputs)
