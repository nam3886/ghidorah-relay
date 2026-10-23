"""Test kit for the submit side (composer, config patch, submit, check): fakes that need no Chrome and no network.

- `World`: one dola conversation, built message by message; it answers the page's own traffic (fed to a REAL
  `NetRecorder`, so the receipt and the replies are read the way production reads them) and the engine's own fetch.
- `FakeDriver`: a `PageDriver` that records every page action in one shared `log`, next to `ctx.mark_sent` and
  `ctx.fence`, so a test can assert the ORDER of the irreversible steps.
- `FakeCtx`, `make_job`, `account`, `open_fake_session`: the platform side.
"""

import asyncio
import functools
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from relay_browser.evidence import Evidence
from relay_browser.net import NetRecorder
from relay_core import frames
from relay_core.leases import LeaseLost
from relay_core.providers import Accepted, AccountView, Completed, JobView, MediaRef, Success
from relay_dola.credential import IDENTITY_COOKIE_NAMES
from relay_dola.engine.site import CAPTURE_PATHS
from relay_dola.engine.submit import Built, Deps, PageDriver

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "dola"
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
CONV = "38400000000000001"
PROMPT = "a paper lantern rising over a quiet lake"
MARKER = "7100"
SENT = frames.render(frames.CORE_FRAME, PROMPT, MARKER)         # the core frame: the words, then ` [7100]`

# dola's own sentences, copied from measured conversations (earlier samples, redacted of ids)
ACCEPTED = ("The video will be generated using the **Dreamina Seedance 2.5 model**. It will use 2 credits and be ready "
            "in 25 minutes. I'll send it to you when it's done. You still have 2 video credits left today.")
GATE = "Video generation currently supports durations from 4 to 15 seconds. Do you want to proceed with 15 seconds?"
DAILY_LIMIT = "You've reached the daily limit for video generation."
NOT_ENOUGH = ("Generating with the current parameters will use 2 video credits. You only have 1 left today. "
              "Change the parameters and try again.")
SOMETHING_WRONG = "Something went wrong. Please try again."
COPYRIGHT = "I can't generate the requested content. Try something else."
LIKENESS = ("For likeness protection, Dreamina Seedance 2.5 only supports generating videos featuring yourself. "
            "Use another image, or create a video from text.")
GOOD_PROBE = {"cells": 5, "ids": ["111", "222"], "name": "", "modal": False,
              "passport": {"message": "success", "uid": True, "name": "Tester One"}}


def sync(test):
    """Run an `async def` test to the end on its own event loop."""
    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


class Settings:
    """The settings view: every key reads as unset."""

    def get(self, key):
        return None


# ---- the platform side -------------------------------------------------------------------------------------------
def credential_blob() -> bytes:
    cookies = [{"name": "sessionid", "value": "S-FAKE", "domain": ".dola.com", "path": "/", "session": True},
               {"name": "uid_tt", "value": "U-FAKE", "domain": ".dola.com", "path": "/", "session": True}]
    return json.dumps({"cookies": cookies}).encode()


def account(*, composer: str | None = None, video: int | None = None) -> AccountView:
    state = {"composer": composer} if composer else {}
    return AccountView("acct-1", "acct", credential_blob(), 1, None, state, {"video": video, "image": None})


class FakeCtx:
    """A RunCtx that records what the engine does, in order, next to the driver's page actions."""

    def __init__(self, log: list, acct: AccountView | None = None, *, fence_error: Exception | None = None,
                 mark_error: Exception | None = None, report_error: Exception | None = None):
        self.log, self.account = log, acct or account()
        self.browser_token, self.settings, self.log_ = object(), Settings(), None
        self.fence_error, self.mark_error, self.report_error = fence_error, mark_error, report_error
        self.events: list[tuple[str, str, str]] = []
        self.evidence_items: list[tuple[str, bytes, str]] = []
        self.refs: list[tuple[str, dict]] = []
        self.fences = 0

    async def fence(self):
        self.log.append("fence")
        self.fences += 1
        if self.fence_error is not None:
            raise self.fence_error

    async def mark_sent(self):
        self.log.append("mark_sent")
        if self.mark_error is not None:
            raise self.mark_error

    async def report_ref(self, ref, state):
        self.log.append("report_ref")
        if self.report_error is not None:
            raise self.report_error
        self.refs.append((ref, dict(state)))

    async def event(self, text, stage, source):
        self.events.append((text, stage, source))
        if stage == "compose":
            self.log.append("flush")          # the composer steps reach the timeline: must be before mark_sent

    async def evidence(self, name, data, mime):
        self.evidence_items.append((name, data, mime))

    def said(self, stage: str) -> list[tuple[str, str]]:
        return [(t, src) for t, st, src in self.events if st == stage]


def lease_lost() -> LeaseLost:
    return LeaseLost(7)


def make_job(kind: str = "video", *, duration: int = 30, ratio: str | None = "16:9", style: str | None = None,
             sent_text: str | None = SENT, images: tuple = (), model: str | None = None, prompt: str = PROMPT,
             marker: str = MARKER, frame: str | None = None, provider_state: dict | None = None) -> JobView:
    """A job in the production form: `prompt`, `marker` and `sent_text` agree. `frame` renders the sent text from that
    frame (the real renderer); `sent_text` overrides it for a test that needs another text."""
    if frame is not None:
        sent_text = frames.render(frame, prompt, marker)
    params = {"kind": kind, "model": model or ("seedance_v2.5" if kind == "video" else "seedream-4.5")}
    if kind == "video":
        params.update(duration=duration, ratio=ratio)
    else:
        params.update(ratio=ratio, style=style)

    async def inputs():
        return list(images)
    return JobView("job-1", "dola", "text_to_video" if kind == "video" else "text_to_image", params["model"], params,
                   prompt, marker, sent_text, None, None, None, dict(provider_state or {}), inputs)


def media_ref(idx: int = 0, mime: str = "image/png") -> MediaRef:
    return MediaRef(idx, "job-1/input/%d.png" % idx, mime, 10)


# ---- the page's traffic ----------------------------------------------------------------------------------------------
class _Request:
    def __init__(self, post_data):
        self.method, self.post_data = "POST", post_data


class _Response:
    def __init__(self, url, body, status, post_data):
        self.url, self.status, self.request, self._body = url, status, _Request(post_data), body

    async def text(self):
        return self._body


class _Page:
    """What `NetRecorder.attach` needs from a page: the `on(...)` hook."""

    def __init__(self):
        self.handlers = {}
        self.url = "https://www.dola.com/chat"

    def on(self, event, handler):
        self.handlers[event] = handler


def chain_request(conversation: str, anchor: int = 9007199254740991) -> str:
    return json.dumps({"cmd": 3100, "uplink_body": {"pull_singe_chain_uplink_body": {
        "conversation_id": conversation, "anchor_index": anchor, "conversation_type": 3, "direction": 1,
        "limit": 20, "ext": {}, "filter": {"index_list": []}}}, "sequence_id": "x", "channel": 2, "version": "1"})


def message(kind: str, text: str, index: int, conv: str = CONV) -> dict:
    """One dola message: a user's is `{"text": ...}`, an assistant's is a list of text blocks."""
    base = {"conversation_id": conv, "index_in_conv": str(index), "create_time": str(1790000000 + index)}
    if kind == "images":          # `text` is a list of clean image URLs, as dola's creation block carries them
        creations = [{"image": {"image_ori_raw": {"url": url}}} for url in text]
        content = {"creation_block": {"creations": creations}}
        block = [{"block_type": 3000, "block_id": "c%d" % index, "content": content}]
        return {**base, "user_type": 2, "content_type": 9999, "content": json.dumps(block)}
    if kind == "user":
        return {**base, "user_type": 1, "content_type": 1, "content": json.dumps({"text": text})}
    if kind == "user_blocks":     # the shape of every i2v order and of the page's own incremental reads
        block = [{"block_type": 10000, "block_id": "u%d" % index, "content": {"text_block": {"text": text}},
                  "is_finish": True}]
        return {**base, "user_type": 1, "content_type": 9999, "content": json.dumps(block)}
    # an assistant message: `text` is one text block, or a list of blocks (one reply may hold several, and blank lines
    # of its own). "assistant_open" is a reply still being written, EXACTLY as the S1 captures show it: its text block
    # says is_finish true (the 'Video' read of t2v.json / i2v.json), and the MESSAGE's `ext` has no is_finish at all.
    done = kind != "assistant_open"
    texts = list(text) if isinstance(text, (list, tuple)) else [text]
    block = [{"block_type": 10000, "block_id": "b%d.%d" % (index, i), "content": {"text_block": {"text": t}},
              "is_finish": True} for i, t in enumerate(texts)]
    return {**base, "user_type": 2, "content_type": 9999, "content": json.dumps(block), "message_id": "m%d" % index,
            "ext": {"agent_id": "kit", **({"is_finish": "1"} if done else {})}}


def chain_body(thread: list[tuple[str, str]], conv: str = CONV, extra: list[dict] | None = None) -> dict:
    """A conversation read: `thread` is [(kind, text)] oldest first; dola lists the newest message first."""
    msgs = [message(kind, text, i + 1, conv) for i, (kind, text) in enumerate(thread)]
    return {"status_code": 0, "downlink_body": {"pull_singe_chain_downlink_body": {
        "messages": [*(extra or []), *reversed(msgs)]}}}


class Feeder:
    """Feeds responses to a REAL `NetRecorder`, as the page does, and waits until their bodies are read."""

    def __init__(self, recorder: NetRecorder):
        self.page, self.recorder, self.count = _Page(), recorder, 0
        recorder.attach(self.page)

    async def feed(self, path: str, req: str, body: str, status: int = 200) -> None:
        self.page.handlers["response"](_Response("https://www.dola.com" + path, body, status, req))
        self.count += 1
        for _ in range(200):
            if len(self.recorder.events()) >= self.count:
                return
            await asyncio.sleep(0)
        raise AssertionError("the recorder did not read the fed response")

    async def chain(self, body: dict, conv: str = CONV, status: int = 200, anchor: int = 9007199254740991) -> None:
        await self.feed("/im/chain/single", chain_request(conv, anchor), json.dumps(body), status)

    async def sse(self, text: str) -> None:
        await self.feed("/chat/completion", "{}", text)


class World:
    """One conversation as dola holds it. `sends` are what the engine typed; each send appends a user message and then
    the assistant lines scripted for it in `replies` (a list per send number, from 1). `late` lines arrive one per tick
    (each `sleep` of the engine is a tick), as a slow dola would."""

    def __init__(self, feeder: Feeder, *, conv: str = CONV, replies: dict[int, list] | None = None,
                 late: list | None = None, polls: bool = True, on_send=None, user_kind: str = "user"):
        self.feeder, self.conv, self.polls, self.on_send = feeder, conv, polls, on_send
        self.user_kind = user_kind
        self.replies = replies or {}
        self.late = list(late or [])
        self.thread: list[tuple[str, str]] = []
        self.sends: list[str] = []
        self.reads = 0

    def body(self) -> dict:
        return chain_body(self.thread, self.conv)

    async def user_sent(self, text: str) -> None:
        self.sends.append(text)
        self.thread.append((self.user_kind, text))
        for line in self.replies.get(len(self.sends), []):
            self.thread.append(line if isinstance(line, tuple) else ("assistant", line))
        if self.polls:
            await self.feeder.chain(self.body(), self.conv)
        if self.on_send is not None:
            await self.on_send(self, len(self.sends))

    async def tick(self) -> None:
        if self.late:
            line = self.late.pop(0)
            if isinstance(line, tuple) and line[0] == "complete":      # the reply being written is now finished
                self.thread[-1] = ("assistant", line[1])
            elif isinstance(line, tuple) and line[0] == "grow":        # it is still being written, and longer
                self.thread[-1] = ("assistant_open", line[1])
            else:
                self.thread.append(line if isinstance(line, tuple) else ("assistant", line))
            if self.polls:
                await self.feeder.chain(self.body(), self.conv)


class FakeEvidence:
    def __init__(self):
        self.tags: list[str] = []

    async def after_send(self, page):
        return Evidence("after-send-1.jpg", b"jpg", "image/jpeg")

    async def ui_error(self, page, tag):
        self.tags.append(tag)
        return [Evidence("ui-error-%s.png" % tag, b"png", "image/png")]


class FakeSession:
    def __init__(self, patched: int = 1, cold: bool = False):
        self.page = _Page()
        self.recorder = NetRecorder(CAPTURE_PATHS)
        self.feeder = Feeder(self.recorder)
        self.evidence, self.patched, self.ctx, self.cold = FakeEvidence(), patched, None, cold


def opener(session: FakeSession, calls: list | None = None, error: Exception | None = None):
    """A stand-in for `open_session`: yields the fake session and records how it was asked for."""
    @asynccontextmanager
    async def open_(site, acct, **kwargs):
        if calls is not None:
            calls.append((site, kwargs))
        if error is not None:
            raise error
        yield session
    return open_


class FakeWatch:
    """What the driver's navigation watch says at the send: scripted by `moved`."""

    def __init__(self, moved: bool):
        self._moved, self.stopped = moved, False

    def moved(self) -> bool:
        return self._moved

    def stop(self) -> None:
        self.stopped = True

    def load(self) -> None:
        self._moved = True


class FakeTraffic:
    """What the driver's traffic watch says: did the browser send anything that could carry the order from the Enter
    to the end of the send? `scripted` None: exactly when the Enter happened (the fake's `send`, or a real send's Enter
    key, logged it): the money-safe default, so only a test that says so proves a send never left. With `scripted`
    False a late send (`hit`, while armed and not frozen) is still seen."""

    def __init__(self, log: list, scripted: bool | None):
        self._log, self._scripted, self.stopped = log, scripted, False
        self.armed = self.frozen = False
        self.count = 0

    def hit(self) -> None:
        if self.armed and not self.frozen:
            self.count += 1

    def arm(self) -> None:
        self.armed, self.frozen = True, False

    def freeze(self) -> None:
        self.frozen = True

    def seen(self) -> bool:
        if self._scripted is None:
            return "send" in self._log or "Enter" in self._log
        return self._scripted or self.count > 0

    def stop(self) -> None:
        self.stopped = True


class FakeDriver(PageDriver):
    """The page actions, scripted. Every action is appended to `log`; nothing touches a browser."""

    def __init__(self, log: list, world: World | None = None, *, probe: dict | None = None,
                 url: str = "https://www.dola.com/chat", new_composer: bool | None = True, page_text: str = "",
                 build_error: Exception | None = None, attach_reason: str = "", send_error: Exception | None = None,
                 open_error: Exception | None = None, rebuild_error: Exception | None = None,
                 passport: dict | None = None, kind: str | None = "new", type_error: Exception | None = None,
                 cookie_names: set | None = ..., injected: list | None = ..., build_error_times: int | None = None,
                 moves: list | None = None, region_after_settle: bool = False,
                 build_reloads: int = 0, attach_errors: dict | None = None, order_request: bool | None = None,
                 probe_after_send: dict | None = None, late_send: bool = False, halt_ok: bool = True):
        self.log, self.world = log, world
        self.probe_result = GOOD_PROBE if probe is None else probe
        self.url, self.new_composer, self.text = url, new_composer, page_text
        self.build_error, self.attach_reason, self.send_error = build_error, attach_reason, send_error
        self.open_error, self.rebuild_error = open_error, rebuild_error
        self.passport_result, self.kind = passport, kind
        self.type_error = type_error
        # the identity cookies the browser still holds: all of them unless a test says otherwise; None = not readable
        self.cookie_names = set(IDENTITY_COOKIE_NAMES) if cookie_names is ... else cookie_names
        # the identity names `inject` reports as verified in the browser: both unless a test says otherwise
        self.injected = sorted(IDENTITY_COOKIE_NAMES) if injected is ... else injected
        self.build_error_times, self.builds = build_error_times, 0     # None = the build error is raised every time
        self.moves = list(moves or [])         # per compose: did the page load again before the send? (default no)
        self.region_after_settle, self.watches = region_after_settle, []
        self.build_reloads = build_reloads        # builds that reload the page themselves (the engine's own net)
        self.attach_errors, self.attaches = dict(attach_errors or {}), 0    # {call number: error}
        self.opens = 0
        self.order_request, self.traffic_watches = order_request, []   # see FakeTraffic
        self.probe_after_send = probe_after_send     # the login read once the Enter happened (None: as before it)
        self.builds_cold: list[bool] = []
        self.late_send, self.halt_ok = late_send, halt_ok   # halt: a send fires late; the browser close works
        self.attached, self.images_at_send = 0, []   # thumbnails on the page: a settle keeps them, a load clears them
        self.sent: list[str] = []
        self.snapshots: list[dict] = []           # the conversation as it stood just before each send
        self.bodies: dict[str, dict | None] = {}     # other conversations the engine's own fetch can read

    async def inject(self, session, acct):
        self.log.append("inject")
        return self.injected

    async def settle(self, page, *, goto=False):
        self.log.append("settle")

    def watch_navigation(self, page):
        watch = FakeWatch(self.moves.pop(0) if self.moves else False)
        self.watches.append(watch)
        return watch

    async def halt(self, session):
        if self.late_send:                 # a sender fires late (a worker, a beacon), before the close ends it
            self.traffic_watches[-1].hit()
        self.log.append("halt")
        return self.halt_ok

    def watch_traffic(self, session):
        watch = FakeTraffic(self.log, self.order_request)
        self.traffic_watches.append(watch)
        return watch

    async def identity_cookie_names(self, session):
        return self.cookie_names

    async def open_chat(self, page):
        self.log.append("open_chat")
        self.opens += 1
        self.attached = 0
        if self.region_after_settle and self.opens > 1:
            self.url = "https://www.dola.com/security/region-restricted"
        if self.open_error is not None:
            raise self.open_error

    async def probe(self, page):
        if self.probe_after_send is not None and "mark_sent" in self.log:
            return self.probe_after_send
        return self.probe_result

    async def page_url(self, page):
        return self.url

    async def page_text(self, page):
        return self.text

    async def read_chain(self, page, conversation_id):
        if conversation_id in self.bodies:
            return self.bodies[conversation_id]
        if self.world is None or conversation_id != self.world.conv:
            return None
        self.world.reads += 1
        return self.world.body() if self.world.thread else None

    async def build(self, page, spec, *, cold=False):
        self.log.append("build")
        self.builds_cold.append(cold)
        self.builds += 1
        if self.builds <= self.build_reloads:
            for watch in self.watches:
                if not watch.stopped:
                    watch.load()
        if self.build_error is not None and (self.build_error_times is None or self.builds <= self.build_error_times):
            raise self.build_error
        return Built(("video mode: chip Model 2.5",), self.new_composer if spec.kind == "video" else None)

    async def rebuild(self, page, spec):
        self.log.append("rebuild")
        if self.rebuild_error is not None:
            raise self.rebuild_error
        return Built(("re-send composer",), self.new_composer)

    async def attach(self, page, images, *, raise_navigation=True):
        self.log.append("attach")
        self.attaches += 1
        error = self.attach_errors.get(self.attaches)
        if error is not None:                    # what the real attach does with an error from the page
            if raise_navigation and "execution context was destroyed" in str(error).lower():
                raise error
            return "attach error: %s" % str(error)[:120]
        self.attached += len(images)
        return self.attach_reason

    async def send(self, page, text, *, before_enter):
        if self.type_error is not None:
            raise self.type_error                    # the prompt never went into the input
        await before_enter()
        self.log.append("send")
        self.images_at_send.append(self.attached)
        if self.world is not None:
            try:
                self.snapshots.append(self.world.body())
            except TypeError:          # a replayed capture holds no read yet
                self.snapshots.append({})
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(text)
        if self.world is not None:
            await self.world.user_sent(text)

    async def passport(self, page):
        return self.passport_result

    async def screenshot(self, page):
        return b"png-bytes"

    async def composer_kind(self, page):
        return self.kind


def deps(session: FakeSession, driver: FakeDriver, world: World | None = None, *, calls: list | None = None,
         read_media=None, hold_s: float = 5.0, open_error: Exception | None = None) -> Deps:
    async def sleep(_seconds):
        if world is not None:
            await world.tick()
        else:
            await asyncio.sleep(0)
    clock = iter(range(10 ** 6))
    out = Deps(open_session=opener(session, calls, open_error), driver=driver, sleep=sleep, now=lambda: NOW,
               clock=lambda: float(next(clock)), poll_s=0.01, image_poll_s=1.0, image_hold_s=hold_s)
    if read_media is not None:
        out.read_media = read_media
    return out


class Scene:
    """The usual scene: a log, a ctx, a session, a world, a driver and deps wired together."""

    def __init__(self, *, replies=None, late=None, composer_kind: str | None = None, new_composer: bool | None = True,
                 job: JobView | None = None, patched: int = 1, acct: AccountView | None = None, polls: bool = True,
                 on_send=None, user_kind: str = "user", fence_error: Exception | None = None,
                 mark_error: Exception | None = None,
                 open_error: Exception | None = None, read_media=None, hold_s: float = 5.0, world_factory=None,
                 cold: bool = False, report_error: Exception | None = None, **driver_kw):
        self.log: list = []
        self.session = FakeSession(patched, cold)
        self.world = (world_factory(self.session.feeder) if world_factory else
                      World(self.session.feeder, replies=replies, late=late, polls=polls, on_send=on_send,
                           user_kind=user_kind))
        self.driver = FakeDriver(self.log, self.world, new_composer=new_composer, **driver_kw)
        self.ctx = FakeCtx(self.log, acct or account(composer=composer_kind), fence_error=fence_error,
                           mark_error=mark_error, report_error=report_error)
        self.job = job or make_job()
        self.calls: list = []
        self.deps = deps(self.session, self.driver, self.world, calls=self.calls, read_media=read_media,
                         hold_s=hold_s, open_error=open_error)

    async def run(self):
        from relay_dola.engine.submit import submit
        return await submit(self.ctx, self.job, self.ctx.settings, deps=self.deps)


def effects_of(result):
    """The account effects of a result without the `Success` that a logged-in read adds in front of an outcome that
    carries none of its own (what a test about the order's effects means; the clear itself has its own tests)."""
    owns = isinstance(result.job, Completed) or (isinstance(result.job, Accepted) and result.job.taken)
    acc = tuple(result.account)
    return acc[1:] if acc[:1] == (Success(),) and not owns else acc


def types(result) -> list[str]:
    """The names of the effects of a result, in order."""
    return [type(e).__name__ for e in result.account]


class SendPage:
    """The composer input as the send path sees it. Enter empties it after `clear_after` waits (a slow page; -1 = Enter
    does nothing). A script the kit does not know (a Send-button click) raises. Every keypress goes to `log`; `text`
    at each Enter is kept in `entered`."""

    def __init__(self, log, *, clear_after=0, typeable=True):
        self.log, self.clear_after, self.typeable = log, clear_after, typeable
        self.text, self.enter_at, self.waits, self.entered = "", None, 0, []
        self.keyboard = self

    async def evaluate(self, js, arg=None):
        from relay_browser import ui
        if js is ui._FOCUS_COMPOSER_JS:
            return self.typeable
        if js is ui._FAST_SET_JS:
            self.text = arg
            return len(arg)
        if js is ui._COMPOSER_TEXT_JS:
            return self.text
        raise AssertionError("an unscripted script reached the page (a Send click?)")

    async def wait_for_timeout(self, ms):
        self.waits += 1
        if self.enter_at is not None and self.waits - self.enter_at >= self.clear_after:
            self.text = ""

    async def press(self, key):
        self.log.append(key)
        if key == "Enter":
            self.entered.append(self.text)
            if self.clear_after >= 0:
                self.enter_at = self.waits


def fail_own_reads(scene, fails) -> dict:
    """Make the engine's OWN conversation read fail when `fails(sends, k)` is true: `sends` = sends so far, `k` = the
    1-based count of own reads since that send (reads before the receipt are left alone). The recorder (the page's own
    traffic) is not touched, so a test can serve an OLDER full page read next to a failing own read. Returns the
    counters (`reads`: every own read after the receipt, in order, as (sends, k, served))."""
    real, state = scene.driver.read_chain, {"k": {}, "reads": []}

    async def read_chain(page, cid):
        if not scene.ctx.refs:
            return await real(page, cid)
        sends = scene.log.count("send")
        k = state["k"][sends] = state["k"].get(sends, 0) + 1
        out = None if fails(sends, k) else await real(page, cid)
        state["reads"].append((sends, k, out is not None))
        return out
    scene.driver.read_chain = read_chain
    return state


def stale_page_read(partial: str):
    """An `on_send` hook: after the first send the PAGE makes one full read of the conversation while dola's first reply
    is still half-written (`partial`), older than anything the engine reads later."""
    async def on_send(world, n):
        if n == 1:
            body = chain_body([("user", world.sends[0]), ("assistant", partial)], world.conv)
            await world.feeder.chain(body, world.conv)
    return on_send


def real_reply(case: str, n: int = 0) -> list[str]:
    """The text blocks of the n-th (oldest first) assistant message of a REAL captured conversation in
    fixtures/dola/chains: several blocks and blank lines of its own, as dola writes them."""
    path = next(FIXTURES.joinpath("chains").glob(case + "*.json"))
    messages = json.loads(path.read_text())["chain"]["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]
    replies = []
    for msg in sorted(messages, key=lambda m: int(m["index_in_conv"])):
        if msg["user_type"] == 2:
            blocks = [b["content"]["text_block"]["text"] for b in json.loads(msg["content"])
                      if (b.get("content") or {}).get("text_block")]
            if blocks:
                replies.append(blocks)
    return replies[n]


def stale_own_reads(scene, stale) -> None:
    """The engine's OWN read serves the conversation as it stood JUST BEFORE the latest send (fewer user messages than
    sends made) while `stale(sends, k)` holds: `sends` = sends so far, `k` = the 1-based count of own reads since it."""
    real, counts = scene.driver.read_chain, {}

    async def read_chain(page, cid):
        if not scene.ctx.refs:
            return await real(page, cid)
        sends = scene.log.count("send")
        k = counts[sends] = counts.get(sends, 0) + 1
        if sends >= 2 and stale(sends, k):
            return scene.driver.snapshots[sends - 1]          # as it stood just before the LATEST send
        return await real(page, cid)
    scene.driver.read_chain = read_chain
