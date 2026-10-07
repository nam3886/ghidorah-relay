"""Test kit of the poll side (poll, reconcile, fetch, commands): the recorded chains, an in-process fake HTTP
transport and the run context. Nothing here opens a socket, a browser or reads a secret; the credential is a made-up
cookie pair."""

import datetime as dt
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from relay_core.providers import AccountView, JobView, Proxy

ROOT = Path(__file__).resolve().parents[3]          # the relay/ workspace
FIXTURES = ROOT / "fixtures" / "dola"
CHAINS = FIXTURES / "chains"
ENGINE = "packages/provider-dola/src/relay_dola/engine/"   # for @mutation paths (relative to the repo root)

CONV = "38417999999999999"


def chain_files() -> list[Path]:
    return sorted(CHAINS.glob("*.json"))


def load_case(name: str) -> dict:
    """A recorded case by file stem or prefix: {"case", "fetched_at", "job", "dola_events", "chain", ...}."""
    path = next(p for p in chain_files() if p.stem.startswith(name))
    return json.loads(path.read_text(encoding="utf-8"))


def at(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def fetched_at(case: dict) -> float:
    """When the chain was read: the clock the resolver must see (the quiet rule and the grace look at it)."""
    return at(case["fetched_at"]).timestamp()


def created_at(case: dict) -> dt.datetime | None:
    m = re.search(r"Instant\((.*)\)", str((case.get("job") or {}).get("created_at") or ""))
    return dt.datetime.fromisoformat(m.group(1)) if m else None


def first_user_text(body: dict) -> str:
    """The prompt part of the first user message ('Generated video: ' / 'Generate image: ' stripped)."""
    for msg in body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]:
        content = msg.get("content")
        content = json.loads(content) if isinstance(content, str) else content
        if isinstance(content, dict) and content.get("text"):
            return re.sub(r"^Generate[d]? (video|image): ", "", content["text"])[:60]
    return ""


def strip_main_url(body: dict) -> dict:
    """The watermark-only chain: a done chain with `main_url` deleted from every `video_model` (derived, not captured:
    none of the 38 real done videos lacked it)."""
    body = json.loads(json.dumps(body))
    for msg in body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]:
        content = msg.get("content")
        try:
            blocks = json.loads(content) if isinstance(content, str) else content
        except ValueError:
            continue
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if block.get("block_type") != 2074:
                continue
            for creation in block["content"]["creation_block"]["creations"]:
                video = creation.get("video")
                if video and video.get("video_model"):
                    model = json.loads(video["video_model"])
                    for entry in (model.get("video_list") or {}).values():
                        if isinstance(entry, dict):
                            entry.pop("main_url", None)
                    video["video_model"] = json.dumps(model)
        msg["content"] = json.dumps(blocks)
    return body


def cookie_blob() -> bytes:
    cookies = [{"name": n, "value": "fake-%s" % n, "domain": ".dola.com", "path": "/", "secure": True}
               for n in ("sessionid", "uid_tt")]
    return json.dumps({"cookies": cookies}).encode()


def account(*, proxy: bool = True, state: dict | None = None, id: str = "acc-1") -> AccountView:
    return AccountView(id=id, label="acc", credential=cookie_blob(), credential_gen=1,
                       proxy=Proxy("proxy.invalid", 8080, "user:pass") if proxy else None,
                       provider_state=state or {}, capacity={"video": None, "image": None})


async def _no_inputs():
    return []


def job(*, kind: str = "video", ref: str | None = CONV, prompt: str = "a red kite", marker: str = "4100",
        sent_text: str | None = None, since: dt.datetime | None = None, sent_at: dt.datetime | None = None,
        state: dict | None = None, duration: int | None = 30, id: str = "job-1") -> JobView:
    params: dict[str, Any] = {"kind": kind}
    if kind == "video":
        params["duration"] = duration
    return JobView(id=id, provider="dola", operation="text_to_video" if kind == "video" else "text_to_image",
                   model="seedance_v2.5", params=params, prompt=prompt, marker=marker,
                   sent_text=sent_text if sent_text is not None else "%s [%s]" % (prompt, marker),
                   render_since=since, sent_at=sent_at, external_ref=ref, provider_state=state or {},
                   inputs=_no_inputs)


class Settings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class Ctx:
    """A RunCtx for the poll side: records events; the browser token is a stand-in object."""

    def __init__(self, acc: AccountView | None = None, settings: Settings | None = None, token: object | None = None):
        self.account = acc if acc is not None else account()
        self.settings = settings or Settings()
        self.browser_token = token
        self.log = None
        self.events: list[tuple[str, str, str]] = []

    async def event(self, text: str, stage: str, source: str) -> None:
        self.events.append((text, stage, source))

    async def fence(self) -> None:
        return None

    async def evidence(self, name: str, data: bytes, mime: str) -> None:
        return None


class Resp:
    def __init__(self, status: int = 200, body: Any = None, content: bytes = b"", bad_json: bool = False):
        self.status_code, self._body, self.content, self._bad = status, body, content, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._body


class FakeTransport:
    """In-process dola. `chains`: conversation id -> a chain body (200), an int (that HTTP status), a `Resp`, or a
    callable(call number) of those. `names`: conversation id -> name. `packs`: category id -> the `image` part.
    `get_routes`: url -> bytes | status int | Exception, or callable(url, proxies). Every call is recorded."""

    def __init__(self, chains: dict | None = None, names: dict | None = None, packs: dict | None = None,
                 get_routes: dict | Callable | None = None):
        self.chains, self.names, self.packs = chains or {}, names or {}, packs or {}
        self.get_routes = get_routes or {}
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        self.reads: dict[str, int] = {}

    def post(self, url, *, params, headers, json, timeout, proxies):
        self.posts.append({"url": url, "json": json, "headers": dict(headers), "proxies": proxies, "params": params})
        if url.endswith("/im/chain/single"):
            conv = json["uplink_body"]["pull_singe_chain_uplink_body"]["conversation_id"]
            self.reads[conv] = self.reads.get(conv, 0) + 1
            item = self.chains.get(conv)
            if callable(item):
                item = item(self.reads[conv])
            if item is None:
                return Resp(200, {"status_code": 712017001})
            return self._wrap(item)
        if url.endswith("/im/conversation/batch_get"):
            ids = json["uplink_body"]["batch_get_conv_info_uplink_body"]["conversation_id"]
            infos = [{"conversation_id": i, "name": self.names[i]} for i in ids if i in self.names]
            return Resp(200, {"status_code": 0, "downlink_body": {"batch_get_conv_info_downlink_body": {
                "conversation_info_list": infos}}})
        if url.endswith("/samantha/skill/pack"):
            cid = json["condition"]["image_condition"]["category_id"]
            return Resp(200, {"data": {"image": self.packs[cid]}})
        raise AssertionError("an unexpected POST %s" % url)

    @staticmethod
    def _wrap(item):
        if isinstance(item, Resp):
            return item
        if isinstance(item, int):
            return Resp(item, {})
        return Resp(200, item)

    def get(self, url, *, headers, timeout, proxies):
        self.gets.append({"url": url, "headers": dict(headers or {}), "proxies": proxies})
        route = self.get_routes(url, proxies) if callable(self.get_routes) else self.get_routes.get(url)
        if isinstance(route, Exception):
            raise route
        if isinstance(route, int):
            return Resp(route, None, b"")
        if isinstance(route, Resp):
            return route
        if route is None:
            return Resp(404, None, b"")
        return Resp(200, None, route) if isinstance(route, bytes) else Resp(200, route, b"")

    # ---- what the tests assert on --------------------------------------------------------------------------------
    def chain_reads(self) -> list[str]:
        return [p["json"]["uplink_body"]["pull_singe_chain_uplink_body"]["conversation_id"]
                for p in self.posts if p["url"].endswith("/im/chain/single")]

    def batch_calls(self) -> list[list[str]]:
        return [p["json"]["uplink_body"]["batch_get_conv_info_uplink_body"]["conversation_id"]
                for p in self.posts if p["url"].endswith("/im/conversation/batch_get")]


def http_for(transport: FakeTransport, acc: AccountView | None = None):
    from relay_dola.engine.http import DolaHttp
    return DolaHttp.for_account(acc or account(), transport)


def chain_with(messages: list[dict]) -> dict:
    """A chain body from messages (newest first, as dola returns them)."""
    return {"status_code": 0, "downlink_body": {"pull_singe_chain_downlink_body": {"messages": messages}}}


def reply(text: str, at_s: float = 0, index: int = 2) -> dict:
    return {"index_in_conv": index, "content_type": 9999, "create_time": at_s,
            "content": json.dumps([{"content": {"text_block": {"text": text}}}])}


def user(text: str, at_s: float = 0, index: int = 1) -> dict:
    return {"index_in_conv": index, "content_type": 1, "create_time": at_s, "content": json.dumps({"text": text})}


def user_block(text: str, at_s: float = 0, index: int = 1) -> dict:
    """The customer's message as the page's own reads and every i2v conversation carry it (S1 capture): `user_type 1`,
    `content_type 9999`, the text in a `text_block` of a block list, exactly like an assistant message."""
    return {"index_in_conv": index, "content_type": 9999, "user_type": 1, "create_time": at_s,
            "content": json.dumps([{"block_type": 10000, "content": {"text_block": {"text": text}}}])}


S1 = FIXTURES / "s1-network"


def s1_capture(name: str) -> dict:
    return json.loads((S1 / ("%s.json" % name)).read_text(encoding="utf-8"))


def s1_reads(name: str, swap: str | None = None) -> list[tuple[float, int, dict]]:
    """Every `/im/chain/single` read of a recorded S1 session as (seconds after send, anchor_index, parsed body).
    `swap` is put in front of the job's marker inside every body: the customer's prompt now says that."""
    capture = s1_capture(name)
    reads = []
    for ev in capture["http"]:
        if ev.get("path") != "/im/chain/single":
            continue
        raw = ev["body"].replace(capture["marker"], (swap + " " if swap else "") + capture["marker"])
        try:
            body = json.loads(raw)
        except ValueError:
            continue
        anchor = json.loads(ev["req"])["uplink_body"]["pull_singe_chain_uplink_body"]["anchor_index"]
        reads.append((ev["t"], anchor, body))
    return reads
