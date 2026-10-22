"""meta's image submit and fetch, driven by the redacted captures of the live run (fixtures/meta/spike-261023) and a
scripted page: no Chrome, no meta.ai, no real cookie. Each money rule has a test here and a mutation that breaks it."""

import asyncio
import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from relay_core import errors
from relay_core.net import UnsafeUrl
from relay_core.providers import Ambiguous, Completed, Cooldown, Dead, Fail, Proxy, Retry, Success, Suspect
from relay_core.testing.fake_media import MediaSite, public_resolver
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_meta.engine import fetch as fetch_mod
from relay_meta.engine import submit as S
from relay_meta.engine.check import SIGNALS_JS
from relay_meta.spec import SPEC

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "meta" / "spike-261023"
SUBMIT_FILE = "packages/provider-meta/src/relay_meta/engine/submit.py"
CONSTANTS = "packages/provider-meta/src/relay_meta/constants.py"
ERRORS_FILE = "packages/provider-meta/src/relay_meta/errors.py"
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
JAR = json.dumps({"cookies": [{"name": "ecto_1_sess", "value": "v-fake", "domain": ".meta.ai", "session": True}]})
EDITABLE, TEXTAREA = "the hydrated composer", "the placeholder textarea"


# ---- the captures ------------------------------------------------------------------------------------------------
def capture(folder: str) -> SimpleNamespace:
    """One scenario of the live run: the prompt, the sign-in signals, the GraphQL calls and the final answer."""
    base = FIX / folder
    http = json.loads((base / "http.json").read_text())["http"]
    events = [SimpleNamespace(path="/api/graphql", req=json.dumps({"doc_id": e["doc_id"], "variables": e["variables"]}),
                              body=e["body"]) for e in http if e.get("doc_id")]
    loaded = json.loads((base / "loaded.state.json").read_text())
    signals = {"menu": loaded["signals"]["user_menu_visible"], "out": False, "name": "",
               "title": loaded["state"]["title"], "failed": "Failed to verify" in loaded.get("page_text", "")}
    final = base / "final.dom.html"
    answer = final.read_text().split("<!-- composer")[0] if final.exists() else ""
    send = base / "send.json"
    prompt = json.loads(send.read_text())["prompt"] if send.exists() else ""
    return SimpleNamespace(events=events, signals=signals, answer=answer, prompt=prompt)


def streaming(html: str) -> str:
    """The same answer while meta still draws: STREAMING, and the tiles show the low-res preview."""
    done = 'data-streaming-state="DONE" data-streaming-complete="true"'
    return html.replace(done, 'data-streaming-state="STREAMING"').replace("/t39.105495-1/", "/t58.90637-1/")


def warmed_uuid(cap) -> str:
    return next(S.warmup_id(e) for e in cap.events if S.warmup_id(e))


# ---- a scripted page ---------------------------------------------------------------------------------------------
class Recorder:
    def __init__(self, events):
        self._events = list(events)

    def now(self):
        return 0.0

    def events(self, since=0.0):
        return list(self._events)

    async def wait_for(self, predicate, timeout, since=0.0):
        return next((e for e in self._events if predicate(e)), None)


class Keyboard:
    def __init__(self, page):
        self.page = page

    async def insert_text(self, text):
        self.page.typed[self.page.focus] = self.page.typed.get(self.page.focus, "") + text


class Box:
    """A locator: a selector that does not ask for the contenteditable reaches the placeholder textarea, which comes
    first in the DOM (fixtures/meta/spike-261023/*/final.dom.html)."""

    def __init__(self, page, selector):
        self.page = page
        self.target = EDITABLE if '[contenteditable="true"]' in selector else TEXTAREA

    async def wait_for(self, state="visible", timeout=None):
        if self.target == EDITABLE and not self.page.hydrates:
            raise TimeoutError("Timeout %sms exceeded" % timeout)

    async def click(self):
        self.page.focus = self.target
        self.page.log.append("click " + self.target)

    async def inner_text(self):
        return "" if self.target == TEXTAREA else self.page.typed.get(EDITABLE, "")

    async def press(self, key):
        self.page.log.append(key)
        if self.page.press_error:
            raise self.page.press_error
        if key == "Enter" and self.page.opens is not None:
            self.page.url = "https://www.meta.ai/prompt/" + self.page.opens


class Page:
    def __init__(self, log, cap, *, answers=None, hydrates=True, opens="warmed", press_error=None):
        self.log, self.signals, self.hydrates, self.press_error = log, cap.signals, hydrates, press_error
        self.url = "https://www.meta.ai/"
        self.opens = warmed_uuid(cap) if opens == "warmed" and cap.events else (None if opens == "warmed" else opens)
        self.answers = list(answers if answers is not None else ["", streaming(cap.answer), cap.answer])
        self.typed, self.focus = {}, None
        self.keyboard = Keyboard(self)

    async def goto(self, url, **kw):
        self.log.append("goto")

    def locator(self, selector):
        return Box(self, selector)

    async def evaluate(self, script, arg=None):
        if script is SIGNALS_JS:
            return self.signals
        if script is S.ANSWER_JS:
            return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        raise AssertionError("an unexpected script")


class Cookies:
    def __init__(self):
        self.jar = []

    async def clear_cookies(self):
        self.jar = []

    async def add_cookies(self, cookies):
        self.jar = list(cookies)

    async def cookies(self, url):
        return self.jar


class Ctx:
    def __init__(self, log, provider_state=None):
        self.log = log
        self.account = SimpleNamespace(id="acct-meta-1", credential=JAR.encode(), credential_gen=1, proxy=None,
                                       provider_state=provider_state or {})
        self.browser_token = object()
        self.events = []

    async def report_ref(self, ref, state):
        self.log.append("report_ref %s" % ref)
        assert state == {"conversation_id": ref}

    async def mark_sent(self):
        self.log.append("mark_sent")

    async def fence(self):
        self.log.append("fence")

    async def event(self, text, stage, source):
        self.events.append((text, stage, source))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s


def run(page, cap, *, log=None, ctx=None, **settings):
    log = page.log if log is None else log
    ctx = ctx or Ctx(log)

    @contextlib.asynccontextmanager
    async def fake_open(site, account, **kw):
        assert kw["purpose"] == "submit" and kw["images"] is True
        yield SimpleNamespace(page=page, ctx=Cookies(), recorder=Recorder(cap.events))

    clock = Clock()
    deps = S.Deps(open_session=fake_open, sleep=clock.sleep, clock=clock, now=lambda: NOW, signal_wait_s=0,
                  composer_wait_s=1, warmup_wait_s=0, receipt_wait_s=2, tiles_grace_s=3)
    job = SimpleNamespace(params={"kind": "image"}, sent_text="%s [7100]" % (cap.prompt or "a lighthouse"))
    result = asyncio.run(S.submit(ctx, job, FakeSettings(SPEC, **settings), deps=deps))
    return result, ctx


def sent(log) -> list[str]:
    """The irreversible part of the log: the chat id, mark_sent and every Enter, in order."""
    return [x for x in log if x.startswith("report_ref") or x in ("mark_sent", "Enter")]


# ---- the captures, read the way the engine reads the page ---------------------------------------------------------
IMAGE_RUNS = (("image-1-acc1", 1), ("image-1-acc2", 1), ("image-2x169-acc1", 2), ("image-2x169-acc2", 2))


def test_the_captured_answers_read_as_done_with_their_final_images_and_never_a_preview():
    for folder, count in IMAGE_RUNS:
        cap = capture(folder)
        answer = S.read_answer(cap.answer)
        assert answer.done and len(answer.images) == count and answer.previews == 0, (folder, answer)
        assert all("/t39.105495-1/" in u and u.startswith("https://scontent.xx.fbcdn.net/") for u in answer.images)
        drawing = S.read_answer(streaming(cap.answer))
        assert not drawing.done and drawing.images == () and drawing.previews == count, folder
    assert S.read_answer("") == S.Answer(False, (), 0)
    assert not S.is_final_image("https://scontent.xx.fbcdn.net/v/t58.90637-1/x.webp")
    assert not S.is_final_image("https://evil.example/v/t39.105495-1/x.webp")


def test_the_conversation_id_is_the_warmed_up_one_and_the_address_after_enter_names_it():
    cap = capture("image-1-acc1")
    assert warmed_uuid(cap) == "90308813-ac0b-4fe6-9315-d603c8ddc37b"
    assert [S.warmup_id(e) for e in cap.events].count(None) == len(cap.events) - 1, "only warmupConversation names it"
    assert S.chat_id("https://www.meta.ai/prompt/90308813-ac0b-4fe6-9315-d603c8ddc37b") == warmed_uuid(cap)
    assert S.chat_id("https://www.meta.ai/") is None and S.chat_id("https://www.meta.ai/prompt/x") is None


# ---- the send ----------------------------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, '        await self._core(self.ctx.mark_sent)\n        self.sent = True\n'
          '        await box.press("Enter")\n',
          '        self.sent = True\n        await box.press("Enter")\n        await self._core(self.ctx.mark_sent)\n',
          expect="mark_sent right before the one Enter")
@mutation(SUBMIT_FILE, '        await self._core(lambda: ctx.report_ref(conversation, '
          '{"conversation_id": conversation}))\n        await self._core(self.ctx.mark_sent)',
          "        await self._core(self.ctx.mark_sent)", expect="the chat id before the send")
def test_a_signed_in_page_persists_the_chat_id_marks_sent_presses_enter_once_and_delivers_the_final_images():
    for folder, count in IMAGE_RUNS:
        cap = capture(folder)
        page = Page([], cap)
        result, ctx = run(page, cap)
        uuid = warmed_uuid(cap)
        assert sent(page.log)[:1] == ["report_ref " + uuid], "the chat id before the send: %s" % sent(page.log)
        assert sent(page.log) == ["report_ref " + uuid, "mark_sent", "Enter"], (
            "mark_sent right before the one Enter: %s" % sent(page.log))
        assert isinstance(result.job, Completed) and len(result.job.outputs) == count, (folder, result.job)
        assert [o.mime for o in result.job.outputs] == ["image/webp"] * count
        assert all(o.meta == {"conversation_id": uuid} and S.is_final_image(o.ref) for o in result.job.outputs)
        assert result.account == (Success(),)
        assert page.typed == {EDITABLE: "%s [7100]" % cap.prompt}


@mutation(CONSTANTS, """SEL_COMPOSER = ('[data-testid="composer-input"][contenteditable="true"]'""",
          """SEL_COMPOSER = ('[data-testid="composer-input"]'""", expect="placeholder")
def test_the_placeholder_textarea_is_never_typed_into_and_a_composer_that_never_hydrates_sends_nothing():
    cap = capture("image-1-acc1")
    page = Page([], cap)
    run(page, cap)
    assert TEXTAREA not in page.typed and "click " + TEXTAREA not in page.log, "typed into the placeholder"
    late = Page([], cap, hydrates=False)
    result, _ = run(late, cap)
    assert result.job == Retry("meta.ui_page_not_ready") and sent(late.log) == [] and not late.typed


@mutation(SUBMIT_FILE, "        if conversation is None:", "        if False:", expect="no chat id, no send")
def test_without_a_warmed_up_conversation_nothing_is_sent():
    cap = capture("image-1-acc1")
    cap.events = [e for e in cap.events if S.warmup_id(e) is None]
    page = Page([], cap, opens="11111111-2222-3333-4444-555555555555")
    result, _ = run(page, cap)
    assert sent(page.log) == [], "no chat id, no send: %s" % sent(page.log)
    assert result.job == Retry("meta.ui_send_failed")


# ---- after the Enter: unknown, never a second Enter ---------------------------------------------------------------
@mutation(SUBMIT_FILE, '            return await self._unknown("the page showed no %s address within %g s of Enter"',
          '            await self.page.locator(SEL_COMPOSER).press("Enter")\n'
          '            return await self._unknown("the page showed no %s address within %g s of Enter"',
          expect="one Enter")
@mutation(SUBMIT_FILE, "        if self.sent:\n", "        if False:\n", expect="never a Retry after mark_sent")
def test_whatever_goes_wrong_after_the_enter_is_unknown_and_never_a_second_enter():
    cap = capture("image-1-acc1")
    uuid = warmed_uuid(cap)
    cases = {
        "no address": Page([], cap, opens=None),
        "never done": Page([], cap, answers=[streaming(cap.answer)]),
        "a browser error at the Enter": Page([], cap, press_error=RuntimeError("Target page closed")),
    }
    for what, page in cases.items():
        result, _ = run(page, cap)
        assert page.log.count("Enter") == 1, "%s: one Enter, got %s" % (what, page.log)
        assert sent(page.log) == ["report_ref " + uuid, "mark_sent", "Enter"], what
        assert isinstance(result.job, Ambiguous), "%s: never a Retry after mark_sent: %r" % (what, result.job)


def test_an_address_other_than_the_warmed_up_one_is_persisted_too_without_a_send():
    cap = capture("image-1-acc1")
    page = Page([], cap, opens="11111111-2222-3333-4444-555555555555")
    result, _ = run(page, cap)
    assert sent(page.log) == ["report_ref " + warmed_uuid(cap), "mark_sent", "Enter",
                              "report_ref 11111111-2222-3333-4444-555555555555"]
    assert isinstance(result.job, Completed)


def test_a_done_answer_without_an_image_fails_refunded_and_previews_only_stay_unknown():
    cap = capture("image-1-acc1")
    bare = '<div data-testid="assistant-message" data-streaming-state="DONE" data-streaming-complete="true"><p>No.</p>'
    result, _ = run(Page([], cap, answers=[bare]), cap)
    assert result.job == Fail("meta.no_image", "meta finished its answer with no image")
    r = errors.rule("meta.no_image")
    assert (r.terminal, r.refund) == (True, True)
    previews = streaming(cap.answer).replace('data-streaming-state="STREAMING"',
                                             'data-streaming-state="DONE" data-streaming-complete="true"')
    result, _ = run(Page([], cap, answers=[previews]), cap)
    assert isinstance(result.job, Ambiguous), "a preview is never delivered"


@mutation(SUBMIT_FILE, "        if answer.previews:\n            # a part delivery",
          "        if False:\n            # a part delivery", expect="part of the answer")
def test_a_done_answer_with_one_final_image_and_one_preview_left_is_unknown_never_a_part_delivery():
    cap = capture("image-2x169-acc1")
    head, _, tail = cap.answer.rpartition("/t39.105495-1/")
    half = head + "/t58.90637-1/" + tail                    # the second tile still shows its preview
    answer = S.read_answer(half)
    assert answer.done and len(answer.images) == 1 and answer.previews == 1
    page = Page([], cap, answers=[half])
    result, _ = run(page, cap)
    assert isinstance(result.job, Ambiguous), "part of the answer was delivered, the other image lost: %r" % (
        result.job,)
    assert page.log.count("Enter") == 1


# ---- before the Enter: the page's state ---------------------------------------------------------------------------
@mutation(SUBMIT_FILE, '        if state == "checkpoint":', "        if False:", expect="meta.checkpoint")
@mutation(ERRORS_FILE, '    Rule(CHECKPOINT, 503, "api_error", False, False, False, True, True,',
          '    Rule(CHECKPOINT, 503, "api_error", True, True, False, True, True,',
          expect="never a job failure")
def test_a_checkpoint_sends_nothing_rests_the_account_with_a_recheck_and_never_fails_or_charges_the_job():
    cap = capture("checkpoint-acc1")
    page = Page([], cap, opens=None)
    result, _ = run(page, cap, meta__checkpoint_rest_s=1200)
    assert result.job == Retry("meta.checkpoint"), "a checkpoint is meta.checkpoint: %r" % (result.job,)
    assert sent(page.log) == [] and not page.typed
    assert result.account == (Cooldown("browser checkpoint (seen by a submit), to be checked again",
                                       NOW + timedelta(seconds=1200), recheck=True),)
    assert not any(isinstance(e, Suspect | Dead | Success) for e in result.account), "a rest, never a strike"
    r = errors.rule("meta.checkpoint")
    assert (r.terminal, r.refund, r.counts_attempt) == (False, False, False), "never a job failure: %r" % (r,)


def test_a_signed_out_page_is_a_strike_and_a_rest_and_sends_nothing():
    cap = capture("image-1-acc1")
    cap.signals = {"menu": False, "out": True, "name": "", "title": "Meta AI", "failed": False}
    page = Page([], cap)
    result, _ = run(page, cap)
    assert result.job == Retry("pool.account_logged_out") and sent(page.log) == []
    assert [type(e) for e in result.account] == [Suspect, Cooldown]


def test_the_codes_meta_uses_are_registered_with_their_rules():
    assert set(SPEC.codes()) == {"meta.checkpoint", "meta.ui_page_not_ready", "meta.ui_send_failed", "meta.no_image"}
    assert all(errors.RULES[c] == r for c, r in SPEC.codes().items()) and errors.violations(errors.RULES) == []
    for code in ("meta.ui_page_not_ready", "meta.ui_send_failed"):
        r = errors.rule(code)
        assert (r.terminal, r.counts_attempt, r.refund, r.public) == (False, True, True, False), code


def test_video_submit_is_not_built_yet():
    with pytest.raises(NotImplementedError, match="meta video submit"):
        asyncio.run(S.submit(Ctx([]), SimpleNamespace(params={"kind": "video"}, sent_text="x"), None))


# ---- fetch -------------------------------------------------------------------------------------------------------
def drain(ref, site, proxy=None) -> bytes:
    async def go():
        ctx = SimpleNamespace(account=SimpleNamespace(proxy=proxy))
        out = SimpleNamespace(ref=ref)
        return b"".join([p async for p in fetch_mod.fetch(ctx, out, media_transport=site,
                                                          media_resolver=public_resolver)])
    return asyncio.run(go())


def test_fetch_takes_fbcdn_links_only_and_falls_back_to_the_account_proxy():
    ref = S.read_answer(capture("image-1-acc1").answer).images[0]
    site = MediaSite(500, b"webp-bytes")
    proxy = Proxy("proxy.example", 8080, "user:p@ss")
    assert drain(ref, site, proxy) == b"webp-bytes"
    assert site.routes == [None, "http://user:p%40ss@proxy.example:8080"]
    with pytest.raises(UnsafeUrl):
        drain("https://evil.example/v/t39.105495-1/x.webp", MediaSite(b"x"))
