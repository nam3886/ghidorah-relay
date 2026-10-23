"""meta's image submit and fetch, driven by the redacted captures of the live run (fixtures/meta/spike-261023) and a
scripted page: no Chrome, no meta.ai, no real cookie. Each money rule has a test here and a mutation that breaks it."""

import asyncio
import base64
import contextlib
import json
import re
from datetime import UTC, datetime, timedelta
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from relay_core import errors, frames
from relay_core.net import FetchFailed, UnsafeUrl
from relay_core.providers import Ambiguous, Completed, Cooldown, Dead, Fail, MediaRef, Proxy, Retry, Success, Suspect
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
FETCH_FILE = "packages/provider-meta/src/relay_meta/engine/fetch.py"
NOW = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
JAR = json.dumps({"cookies": [{"name": "ecto_1_sess", "value": "v-fake", "domain": ".meta.ai", "session": True}]})
EDITABLE, TEXTAREA = "the hydrated composer", "the placeholder textarea"


# ---- the captures ------------------------------------------------------------------------------------------------
def capture(folder: str) -> SimpleNamespace:
    """One scenario of the live run: the prompt, the sign-in signals, the GraphQL calls and the final answer."""
    base = FIX / folder
    http = json.loads((base / "http.json").read_text())["http"]
    events = [SimpleNamespace(kind="http", method="POST", status=200, path="/api/graphql",
                              req=json.dumps({"doc_id": e["doc_id"], "variables": e["variables"]}),
                              body=e["body"]) for e in http if e.get("doc_id")]
    # the image upload's requests (rupload.meta.ai), in the order the page sent them: they arrive on the file choice
    uploads = [SimpleNamespace(kind="http", method=e["method"], status=e["status"] or 0, path=urlsplit(e["url"]).path,
                               req="", body=e.get("body") or "") for e in http if "rupload.meta.ai" in e["url"]]
    loaded = json.loads((base / "loaded.state.json").read_text())
    signals = {"menu": loaded["signals"]["user_menu_visible"], "out": False, "name": "",
               "title": loaded["state"]["title"], "failed": "Failed to verify" in loaded.get("page_text", "")}
    final = base / "final.dom.html"
    answer = final.read_text().split("<!-- composer")[0] if final.exists() else ""
    send = base / "send.json"
    prompt = json.loads(send.read_text())["prompt"] if send.exists() else ""
    if folder == T2V:
        # The capture pressed Enter 0.1 s after typing, before meta's `warmupConversation` (1.2-1.5 s after the typing
        # in every other capture), so it holds none; the engine's own live text->video run saw it (FINDINGS.md). The
        # call of image-1-acc1 stands in, naming the chat this capture opened.
        chat = S.chat_id(json.loads((base / "timeline.json").read_text())[0]["url"])
        warm = next(e for e in capture("image-1-acc1").events if S.warmup_id(e))
        events.insert(0, SimpleNamespace(**{**vars(warm), "req": warm.req.replace(S.warmup_id(warm), chat)}))
    return SimpleNamespace(events=events, uploads=uploads, signals=signals, answer=answer, prompt=prompt)


def streaming(html: str) -> str:
    """The same answer while meta still draws: STREAMING, and the tiles show the low-res preview."""
    done = 'data-streaming-state="DONE" data-streaming-complete="true"'
    return html.replace(done, 'data-streaming-state="STREAMING"').replace("/t39.105495-1/", "/t58.90637-1/")


def warmed_uuid(cap) -> str:
    return next(S.warmup_id(e) for e in cap.events if S.warmup_id(e))


# ---- a scripted page ---------------------------------------------------------------------------------------------
class Recorder:
    """The page's network log: the list itself, so what the page adds later (the upload) shows up."""

    def __init__(self, events):
        self._events = events

    def now(self):
        return 0.0

    def events(self, since=0.0):
        return list(self._events)

    async def wait_for(self, predicate, timeout, since=0.0):
        return next((e for e in self._events if predicate(e)), None)


class FileInput:
    """The composer's file input: choosing a file shows its thumbnail at once and starts the upload, whose requests
    reach the network log only when meta answers (`page.upload_answers`)."""

    def __init__(self, page):
        self.page = page
        self.first = self

    async def count(self):
        return self.page.file_inputs

    async def set_input_files(self, payload):
        self.page.log.append("attach")
        self.page.chosen.append(payload)
        if self.page.upload_answers:
            self.page.events.extend(self.page.uploads)
            self.page.uploaded = True


class Attached:
    def __init__(self, page):
        self.page = page

    async def count(self):
        if not self.page.chosen:
            return 0
        shown = self.page.thumbnails
        if isinstance(shown, list):     # one count per read, the last one repeats
            return shown.pop(0) if len(shown) > 1 else shown[0]
        return shown


class Keyboard:
    def __init__(self, page):
        self.page = page

    async def insert_text(self, text):
        self.page.typed[self.page.focus] = self.page.typed.get(self.page.focus, "") + text

    async def press(self, key):
        self.page.log.append("key " + key)


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
        # meta ignores Enter while an upload is pending (FINDINGS.md): nothing is sent, the address stays
        if key == "Enter" and self.page.opens is not None and (not self.page.chosen or self.page.uploaded):
            self.page.url = "https://www.meta.ai/prompt/" + self.page.opens


class Page:
    def __init__(self, log, cap, *, answers=None, hydrates=True, opens="warmed", press_error=None,
                 upload_answers=True, thumbnails=1, file_inputs=1, shrunk=b"small-jpeg"):
        self.log, self.signals, self.hydrates, self.press_error = log, cap.signals, hydrates, press_error
        self.url = "https://www.meta.ai/"
        self.opens = warmed_uuid(cap) if opens == "warmed" and cap.events else (None if opens == "warmed" else opens)
        self.answers = list(answers if answers is not None else ["", streaming(cap.answer), cap.answer])
        self.typed, self.focus = {}, None
        self.keyboard = Keyboard(self)
        self.events, self.uploads = list(cap.events), cap.uploads
        self.upload_answers, self.thumbnails, self.file_inputs = upload_answers, thumbnails, file_inputs
        self.chosen, self.uploaded, self.shrunk = [], False, shrunk
        self.lands = None       # where meta sends a goto instead of its address (None: the address itself)

    async def goto(self, url, **kw):
        self.log.append("goto " + url)
        self.url = self.lands or url

    def locator(self, selector):
        if selector == S.SEL_FILE_INPUT:
            return FileInput(self)
        if selector == S.SEL_ATTACHED:
            return Attached(self)
        return Box(self, selector)

    async def evaluate(self, script, arg=None):
        if script is SIGNALS_JS:
            return self.signals
        if script is S.ANSWER_JS:
            return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if script is S.SHRINK_JS:
            self.log.append("shrink")
            return base64.b64encode(self.shrunk).decode() if self.shrunk else ""
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


INPUT = b"RIFF-webp-560k"     # the input image of a video job, as the object store hands it out


DEFAULT_OP = {"image": "text_to_image", "video": "image_to_video"}


def run(page, cap, *, log=None, ctx=None, kind="image", op=None, image=(INPUT, "image/webp"), ratio=None,
        sent=None, **settings):
    log = page.log if log is None else log
    ctx = ctx or Ctx(log)

    @contextlib.asynccontextmanager
    async def fake_open(site, account, **kw):
        assert kw["purpose"] == "submit" and kw["images"] is True
        yield SimpleNamespace(page=page, ctx=Cookies(), recorder=Recorder(page.events))

    op = op or DEFAULT_OP[kind]

    async def inputs():
        return [MediaRef(0, "inputs/job/0", image[1], len(image[0]))] if op in S.FROM_IMAGE else []

    async def read_media(ref):
        assert ref.object_key == "inputs/job/0"
        return image[0]

    clock = Clock()
    deps = S.Deps(open_session=fake_open, read_media=read_media, sleep=clock.sleep, clock=clock, now=lambda: NOW,
                  signal_wait_s=0, composer_wait_s=1, warmup_wait_s=0, receipt_wait_s=2, tiles_grace_s=3,
                  upload_wait_s=0, attached_wait_s=1)
    params = {"kind": kind, **({"ratio": ratio} if ratio else {})}
    sent = "%s [7100]" % (cap.prompt or "a lighthouse") if sent is None else sent
    job = SimpleNamespace(params=params, operation=op, sent_text=sent, inputs=inputs)
    result = asyncio.run(S.submit(ctx, job, FakeSettings(SPEC, **settings), deps=deps))
    return result, ctx


def sent(log) -> list[str]:
    """The irreversible part of the log: the image choice, the chat id, mark_sent and every Enter, in order."""
    return [x for x in log if x.startswith("report_ref") or x in ("attach", "mark_sent", "Enter")]


# ---- the captures, read the way the engine reads the page ---------------------------------------------------------
IMAGE_RUNS = (("image-1-acc1", 1), ("image-1-acc2", 1), ("image-2x169-acc1", 2), ("image-2x169-acc2", 2))


def test_the_captured_answers_read_as_done_with_their_final_images_and_never_a_preview():
    for folder, count in IMAGE_RUNS:
        cap = capture(folder)
        answer = S.read_answer(cap.answer)
        assert answer.done and len(answer.media) == count and answer.previews == 0, (folder, answer)
        assert all("/t39.105495-1/" in u and u.startswith("https://scontent.xx.fbcdn.net/") for u in answer.media)
        drawing = S.read_answer(streaming(cap.answer))
        assert not drawing.done and drawing.media == () and drawing.previews == count, folder
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
          '{"conversation_id": conversation}))\n', "", expect="the chat id before the send")
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
    assert answer.done and len(answer.media) == 1 and answer.previews == 1
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
    assert set(SPEC.codes()) == {"meta.checkpoint", "meta.ui_page_not_ready", "meta.ui_send_failed", "meta.no_image",
                                 "meta.no_video"}
    assert all(errors.RULES[c] == r for c, r in SPEC.codes().items()) and errors.violations(errors.RULES) == []
    for code in ("meta.ui_page_not_ready", "meta.ui_send_failed"):
        r = errors.rule(code)
        assert (r.terminal, r.counts_attempt, r.refund, r.public) == (False, True, True, False), code


def test_a_job_of_another_kind_or_an_operation_of_another_kind_is_refused_before_anything_opens():
    with pytest.raises(ValueError, match="image or a video"):
        asyncio.run(S.submit(Ctx([]), SimpleNamespace(params={"kind": "audio"}, sent_text="x"), None))
    for kind, op in (("image", "text_to_video"), ("video", "image_to_image"), ("video", None)):
        with pytest.raises(ValueError, match="cannot be"):
            asyncio.run(S.submit(Ctx([]), SimpleNamespace(params={"kind": kind}, operation=op, sent_text="x"), None))


# ---- image to video ----------------------------------------------------------------------------------------------
VIDEO = "i2v-acc2"
I2I = "i2i-acc1"
T2V = "t2v-acc1"
FROM_IMAGE_RUNS = ((VIDEO, "video", "image_to_video"), (I2I, "image", "image_to_image"))   # the jobs that upload


def test_the_captured_video_answer_reads_as_done_with_its_mp4_and_the_upload_answer_is_the_post():
    cap = capture(VIDEO)
    answer = S.read_answer(cap.answer, "video")
    assert answer.done and len(answer.media) == 1 and answer.previews == 0
    assert answer.media[0].startswith("https://scontent.xx.fbcdn.net/v/t39.105495-1/") and ".mp4?" in answer.media[0]
    assert S.read_answer(cap.answer, "image") == S.Answer(True, (), 0, 1), "a video job's answer holds no image"
    blob = cap.answer.replace(answer.media[0], "blob:https://www.meta.ai/1234")
    assert S.read_answer(blob, "video") == S.Answer(True, (), 1), "a blob: player source is never delivered"
    assert [S.upload_done(e) for e in cap.uploads] == [False, False, True], "GET, OPTIONS, then the POST {media_id}"
    stalled = capture("i2v-upload-stall-acc1").uploads
    assert stalled and not any(S.upload_done(e) for e in stalled), "the stalled POST never answered"


def test_a_video_job_types_attaches_waits_for_the_upload_then_marks_sent_presses_enter_once_and_delivers_the_mp4():
    cap = capture(VIDEO)
    page = Page([], cap)
    result, _ = run(page, cap, kind="video")
    uuid = warmed_uuid(cap)
    assert uuid == "6f281912-e3c0-4b47-afdc-612c73f1a2c8"
    assert sent(page.log) == ["attach", "report_ref " + uuid, "mark_sent", "Enter"], sent(page.log)
    assert page.chosen == [{"name": "upload.webp", "mimeType": "image/webp", "buffer": INPUT}]
    assert "shrink" not in page.log, "a small webp goes up unchanged"
    assert isinstance(result.job, Completed) and len(result.job.outputs) == 1, result.job
    out = result.job.outputs[0]
    assert (out.mime, out.idx, out.meta) == ("video/mp4", 0, {"conversation_id": uuid}) and S.is_final_video(out.ref)
    assert result.account == (Success(),)
    assert page.typed == {EDITABLE: "%s [7100]" % cap.prompt}


@mutation(SUBMIT_FILE, "        if await recorder.wait_for(upload_done, self.deps.upload_wait_s, since=since) is None:",
          "        if False:", expect="upload finished before Enter")
def test_an_upload_meta_never_answers_sends_nothing():
    for folder, kind, op in FROM_IMAGE_RUNS:
        cap = capture(folder)
        page = Page([], cap, upload_answers=False)
        result, _ = run(page, cap, kind=kind, op=op)
        assert sent(page.log) == ["attach"], "upload finished before Enter: %s %s" % (op, sent(page.log))
        assert result.job == Retry("meta.ui_send_failed"), op


def test_a_composer_without_exactly_one_attached_image_sends_nothing():
    for folder, kind, op in FROM_IMAGE_RUNS:
        cap = capture(folder)
        for what, page in {"no thumbnail": Page([], cap, thumbnails=0), "two thumbnails": Page([], cap, thumbnails=2),
                           "no file input": Page([], cap, file_inputs=0)}.items():
            result, _ = run(page, cap, kind=kind, op=op)
            assert "mark_sent" not in page.log and "Enter" not in page.log, (
                "%s %s: exactly one image: %s" % (op, what, page.log))
            assert result.job == Retry("meta.ui_send_failed"), (op, what)


@mutation(SUBMIT_FILE, "        if image is not None and (shown := await page.locator(SEL_ATTACHED).count()) != 1:",
          "        if False:", expect="read again before the send")
def test_a_thumbnail_dropped_or_doubled_after_the_attach_is_read_again_before_the_send_and_nothing_is_sent():
    for folder, kind, op in FROM_IMAGE_RUNS:
        cap = capture(folder)
        for what, counts in (("dropped", [1, 0]), ("doubled", [1, 2])):
            page = Page([], cap, thumbnails=counts)
            result, _ = run(page, cap, kind=kind, op=op)
            assert "mark_sent" not in page.log and "Enter" not in page.log, (
                "%s %s: read again before the send: %s" % (op, what, sent(page.log)))
            assert result.job == Retry("meta.ui_send_failed"), (op, what)


@mutation(SUBMIT_FILE, "        if len(data) <= UPLOAD_MAX_BYTES and mime in UPLOAD_AS_IS:", "        if True:",
          expect="re-encoded")
def test_a_large_input_is_reencoded_by_the_browser_before_meta_loads_and_one_that_cannot_be_sends_nothing():
    big = (b"P" * (S.UPLOAD_MAX_BYTES + 1), "image/png")
    for folder, kind, op in FROM_IMAGE_RUNS:
        cap = capture(folder)
        page = Page([], cap)
        result, _ = run(page, cap, kind=kind, op=op, image=big)
        assert page.chosen == [{"name": "upload.jpg", "mimeType": "image/jpeg", "buffer": b"small-jpeg"}], (
            "re-encoded: %s %r" % (op, [{**c, "buffer": c["buffer"][:12]} for c in page.chosen]))
        assert page.log.index("shrink") < page.log.index("goto " + S.SITE_URL), "before meta's page loads"
        assert isinstance(result.job, Completed), op
        page = Page([], cap, shrunk=b"")
        result, _ = run(page, cap, kind=kind, op=op, image=big)
        assert result.job == Retry("meta.ui_send_failed") and sent(page.log) == [] and not page.chosen, op


def test_a_video_that_never_finishes_is_unknown_after_one_enter_and_a_done_answer_without_one_fails_refunded():
    cap = capture(VIDEO)
    page = Page([], cap, answers=[streaming(cap.answer)])
    result, _ = run(page, cap, kind="video", meta__video_submit_timeout_s=120)
    assert isinstance(result.job, Ambiguous) and page.log.count("Enter") == 1
    bare = '<div data-testid="assistant-message" data-streaming-state="DONE" data-streaming-complete="true"><p>No.</p>'
    result, _ = run(Page([], cap, answers=[bare]), cap, kind="video")
    assert result.job == Fail("meta.no_video", "meta finished its answer with no video")
    r = errors.rule("meta.no_video")
    assert (r.terminal, r.refund) == (True, True)


# ---- text to video -----------------------------------------------------------------------------------------------
def test_the_captured_text_to_video_answer_reads_as_done_with_its_mp4():
    cap = capture(T2V)
    answer = S.read_answer(cap.answer, "video")
    assert answer.done and len(answer.media) == 1 and answer.previews == 0, answer
    assert answer.media[0].startswith("https://scontent.xx.fbcdn.net/v/t39.105495-1/") and ".mp4?" in answer.media[0]
    assert cap.uploads == [], "nothing was uploaded"


@mutation(SUBMIT_FILE, '        self.text = " ".join(((VIDEO_FROM_TEXT if self.operation == "text_to_video" else "") + '
          'job.sent_text).split())', '        self.text = " ".join(job.sent_text.split())',
          expect="asks for a video")
@mutation(SUBMIT_FILE, "        image = await self._input_image() if self.operation in FROM_IMAGE else None",
          '        image = await self._input_image() if self.kind == "video" else None', expect="text alone")
def test_a_video_from_text_alone_asks_for_a_video_attaches_nothing_marks_sent_presses_enter_once_and_delivers_the_mp4():
    cap = capture(T2V)
    page = Page([], cap)
    result, _ = run(page, cap, kind="video", op="text_to_video")
    uuid = warmed_uuid(cap)
    assert isinstance(result.job, Completed) and len(result.job.outputs) == 1, "text alone: %r" % (result.job,)
    assert sent(page.log) == ["report_ref " + uuid, "mark_sent", "Enter"] and not page.chosen, sent(page.log)
    assert page.typed == {EDITABLE: "Create a short video: %s [7100]" % cap.prompt}, "asks for a video: %r" % (
        page.typed,)
    out = result.job.outputs[0]
    assert (out.mime, out.idx, out.meta) == ("video/mp4", 0, {"conversation_id": uuid}) and S.is_final_video(out.ref)
    assert result.account == (Success(),)


def test_only_a_video_from_text_alone_gets_the_video_wording():
    for folder, kind, op in (("image-1-acc1", "image", "text_to_image"), *FROM_IMAGE_RUNS):
        cap = capture(folder)
        page = Page([], cap)
        run(page, cap, kind=kind, op=op)
        assert page.typed == {EDITABLE: "%s [7100]" % cap.prompt}, op


@mutation(SUBMIT_FILE, '        if job.params.get("ratio") and (self.kind, job.params["ratio"]) not in RATIO_UNTYPED:',
          "        if False:", expect="the ratio is typed")
@mutation(CONSTANTS, 'RATIO_TAIL = ", %s"', 'RATIO_TAIL = " %s"', expect="the ratio is typed")
def test_a_ratio_is_typed_after_the_final_text_and_only_when_the_job_has_one():
    for folder, kind, op, head in (("image-1-acc1", "image", "text_to_image", ""),
                                   (T2V, "video", "text_to_video", "Create a short video: ")):
        cap = capture(folder)
        page = Page([], cap)
        result, _ = run(page, cap, kind=kind, op=op, ratio="9:16")
        assert page.typed == {EDITABLE: "%s%s [7100], 9:16" % (head, cap.prompt)}, "the ratio is typed: %r" % (
            page.typed,)
        assert isinstance(result.job, Completed) and page.log.count("Enter") == 1, op


@mutation(SUBMIT_FILE, '            self.text += RATIO_TAIL % job.params["ratio"]',
          '            self.text = (RATIO_TAIL % job.params["ratio"]).lstrip(", ") + " " + self.text',
          expect="the frame first, the ratio tail second")
def test_an_admin_frame_is_typed_first_and_the_ratio_tail_after_it():
    """The core renders the frame (the shipped default or an admin's); the engine types it, then `, <ratio>`."""
    admin = "Style: watercolor. {{client_prompt}} {{tracking_code}} Keep it calm."
    for frame in (SPEC.frames.defaults["image"], admin):
        cap = capture("image-1-acc1")
        page = Page([], cap)
        text = frames.render(frame, cap.prompt, "7100", SPEC.frames.verbatim)
        result, _ = run(page, cap, kind="image", op="text_to_image", ratio="4:3", sent=text)
        assert page.typed == {EDITABLE: text + ", 4:3"}, "the frame first, the ratio tail second: %r" % (page.typed,)
        assert isinstance(result.job, Completed) and page.log.count("Enter") == 1


@mutation(SUBMIT_FILE, '        if job.params.get("ratio") and (self.kind, job.params["ratio"]) not in RATIO_UNTYPED:',
          '        if job.params.get("ratio"):', expect="a 16:9 video goes with no tail")
@mutation(CONSTANTS, 'RATIO_UNTYPED = (("video", "16:9"),)', "RATIO_UNTYPED = ()",
          expect="a 16:9 video goes with no tail")
def test_a_16_9_video_is_sent_with_no_tail_since_meta_draws_text_to_video_at_16_9_itself():
    cap = capture(T2V)
    page = Page([], cap)
    result, _ = run(page, cap, kind="video", op="text_to_video", ratio="16:9")
    assert page.typed == {EDITABLE: "Create a short video: %s [7100]" % cap.prompt}, (
        "a 16:9 video goes with no tail: %r" % (page.typed,))
    assert isinstance(result.job, Completed) and page.log.count("Enter") == 1


def test_a_video_from_text_that_never_finishes_is_unknown_after_one_enter():
    cap = capture(T2V)
    page = Page([], cap, answers=[streaming(cap.answer)])
    result, _ = run(page, cap, kind="video", op="text_to_video", meta__video_submit_timeout_s=120)
    assert isinstance(result.job, Ambiguous) and page.log.count("Enter") == 1


# ---- image to image ----------------------------------------------------------------------------------------------
# the input image's layer in the captured slider: `aria-hidden` on its wrapper
HIDDEN_WRAPPER = '<div aria-hidden="true" style="clip-path: inset(0px 100% 0px 0px);"><img alt=""'


def edit_shapes(answer: str) -> dict[str, str]:
    """The captured edit (the input image under an `aria-hidden` wrapper), the same with `aria-hidden` on the input
    `img` itself, and the same with the input image shown (no `aria-hidden` at all)."""
    assert answer.count(HIDDEN_WRAPPER) == 1
    shown = '<div style="clip-path: inset(0px 100% 0px 0px);"><img alt=""'
    return {"hidden wrapper": answer,
            "hidden img": answer.replace(HIDDEN_WRAPPER, shown.replace("<img ", '<img aria-hidden="true" ')),
            "input shown": answer.replace(HIDDEN_WRAPPER, shown)}


def edited(cap) -> str:
    """The edited image's link in the captured slider (its `gallery/` img), read with a plain pattern."""
    return unescape(re.search(r'<img alt="gallery/[^"]*"[^>]*? src="([^"]+)"', cap.answer).group(1))


@mutation(SUBMIT_FILE, '                              slider or (a.get("role") == "slider" and a.get("aria-label") == '
          'COMPARE_LABEL)))', '                              slider))', expect="the edited image")
@mutation(SUBMIT_FILE, "            elif hidden:\n", "            elif False:\n", expect="never the input image")
@mutation(SUBMIT_FILE, '            hidden = hidden or a.get("aria-hidden") == "true"', "            hidden = hidden",
          expect="never the input image")
def test_the_captured_edit_reads_as_done_with_the_edited_image_never_the_input_image_in_either_slider_shape():
    cap = capture(I2I)
    for shape, html in list(edit_shapes(cap.answer).items())[:2]:
        answer = S.read_answer(html)
        assert answer == S.Answer(True, (edited(cap),), 0, 0), (
            "%s: the edited image, never the input image: %r" % (shape, answer))
    assert "/t39.105495-1/" in edited(cap) and edited(cap).startswith("https://scontent.xx.fbcdn.net/")
    drawing = S.read_answer(streaming(cap.answer))
    assert not drawing.done and drawing.media == () and drawing.previews == 1
    assert S.read_answer(cap.answer, "video") == S.Answer(True, (), 0, 1), "an edit holds no video"


def test_an_edit_delivers_only_the_edited_image_whichever_way_the_input_image_is_hidden():
    cap = capture(I2I)
    for shape, html in list(edit_shapes(cap.answer).items())[:2]:
        page = Page([], cap, answers=[html])
        result, _ = run(page, cap, kind="image", op="image_to_image")
        assert isinstance(result.job, Completed), (shape, result.job)
        assert [o.ref for o in result.job.outputs] == [edited(cap)], shape


@mutation(SUBMIT_FILE, '                if (a.get("alt") or "").startswith(RESULT_ALT):', "                if True:",
          expect="never both")
def test_a_slider_that_shows_the_input_image_too_is_unknown_never_both_delivered():
    cap = capture(I2I)
    html = edit_shapes(cap.answer)["input shown"]
    answer = S.read_answer(html)
    assert answer.previews == 1, "never both: %r" % (answer,)
    page = Page([], cap, answers=[html])
    result, _ = run(page, cap, kind="image", op="image_to_image")
    assert isinstance(result.job, Ambiguous), "never both: %r" % (result.job,)
    assert page.log.count("Enter") == 1


def test_an_edit_attaches_the_image_waits_for_the_upload_marks_sent_presses_enter_once_and_delivers_the_edited_image():
    cap = capture(I2I)
    page = Page([], cap)
    result, _ = run(page, cap, kind="image", op="image_to_image")
    uuid = warmed_uuid(cap)
    assert uuid == "55165934-9729-4a72-9083-64f3e7a6fc5e"
    assert sent(page.log) == ["attach", "report_ref " + uuid, "mark_sent", "Enter"], sent(page.log)
    assert page.chosen == [{"name": "upload.webp", "mimeType": "image/webp", "buffer": INPUT}]
    assert isinstance(result.job, Completed) and len(result.job.outputs) == 1, result.job
    out = result.job.outputs[0]
    assert (out.mime, out.idx, out.meta) == ("image/webp", 0, {"conversation_id": uuid}) and S.is_final_image(out.ref)
    assert out.ref == S.read_answer(cap.answer).media[0]
    assert result.account == (Success(),)


@mutation(SUBMIT_FILE, "            elif PREVIEW_MARK in urlsplit(src).path:", "            elif False:",
          expect="a preview outside")
def test_an_edit_done_with_its_preview_still_shown_is_unknown_never_delivered_nor_failed():
    cap = capture(I2I)
    done = 'data-streaming-state="DONE" data-streaming-complete="true"'
    in_slider = streaming(cap.answer).replace('data-streaming-state="STREAMING"', done)
    # the preview meta showed before the slider (an "Inline image" at 16 s in the capture), not yet in a slider
    outside = (cap.answer.split('<div tabindex="0" role="slider"')[0] + '<img alt="Inline image" src="https://'
               'scontent.xx.fbcdn.net/v/t58.90637-1/x_n.webp?stp=dst-webp"></div>')
    assert S.read_answer(outside) == S.Answer(True, (), 1), "a preview outside the slider is counted"
    for what, html in (("in the slider", in_slider), ("a preview outside the slider", outside)):
        page = Page([], cap, answers=[html])
        result, _ = run(page, cap, kind="image", op="image_to_image")
        assert isinstance(result.job, Ambiguous), "%s: %r" % (what, result.job)
        assert page.log.count("Enter") == 1


# ---- an answer of the other kind ---------------------------------------------------------------------------------
@mutation(SUBMIT_FILE, "        if answer.other:", "        if False:", expect="never a refund at once")
def test_a_done_answer_with_only_the_other_kind_is_unknown_never_a_refund_and_one_with_nothing_still_fails():
    image, video, edit = capture("image-1-acc1"), capture(T2V), capture(I2I)
    cases = (("a video from text answered with an image", T2V, "video", "text_to_video", image.answer),
             ("a video from an image answered with an edit", VIDEO, "video", "image_to_video", edit.answer),
             ("an edit answered with a video", I2I, "image", "image_to_image", video.answer),
             ("an image answered with a video", "image-1-acc1", "image", "text_to_image", video.answer))
    for what, folder, kind, op, html in cases:
        cap = capture(folder)
        assert S.read_answer(html, kind).other >= 1, what
        page = Page([], cap, answers=[html])
        result, _ = run(page, cap, kind=kind, op=op)
        assert isinstance(result.job, Ambiguous), "%s: meta took it, never a refund at once: %r" % (what, result.job)
        assert page.log.count("Enter") == 1
    bare = '<div data-testid="assistant-message" data-streaming-state="DONE" data-streaming-complete="true"><p>No.</p>'
    result, _ = run(Page([], video, answers=[bare]), video, kind="video", op="text_to_video")
    assert result.job == Fail("meta.no_video", "meta finished its answer with no video")


# ---- fetch -------------------------------------------------------------------------------------------------------
def drain(ref, site, proxy=None) -> bytes:
    async def go():
        ctx = SimpleNamespace(account=SimpleNamespace(proxy=proxy))
        out = SimpleNamespace(ref=ref)
        return b"".join([p async for p in fetch_mod.fetch(ctx, out, media_transport=site,
                                                          media_resolver=public_resolver)])
    return asyncio.run(go())


@mutation(FETCH_FILE, "proxy=proxy_url(ctx.account.proxy),", "proxy=None,",
          expect="the meta media went direct although the account has a proxy")
def test_fetch_takes_fbcdn_links_only_through_the_account_proxy_when_it_has_one():
    ref = S.read_answer(capture("image-1-acc1").answer).media[0]
    site = MediaSite(b"direct-bytes", b"webp-bytes")
    proxy = Proxy("proxy.example", 8080, "user:p@ss")
    assert drain(ref, site, proxy) == b"webp-bytes" and site.routes == ["http://user:p%40ss@proxy.example:8080"], \
        "the meta media went direct although the account has a proxy: %r" % site.routes
    failing = MediaSite(b"direct-bytes", 500)
    with pytest.raises(FetchFailed):
        drain(ref, failing, proxy)
    assert failing.routes == ["http://user:p%40ss@proxy.example:8080"], \
        "the meta media went direct although the account has a proxy: %r" % failing.routes
    plain = MediaSite(b"webp-bytes")
    assert drain(ref, plain) == b"webp-bytes" and plain.routes == [None]
    with pytest.raises(UnsafeUrl):
        drain("https://evil.example/v/t39.105495-1/x.webp", MediaSite(b"x"))
