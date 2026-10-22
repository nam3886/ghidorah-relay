"""meta's reconcile (reopen the saved chat, read the thread, never send) and the poll that delivers what it found,
driven by the redacted captures (fixtures/meta/spike-261023) and the scripted page of test_meta_submit."""

import asyncio
import contextlib
from types import SimpleNamespace

from test_meta_submit import IMAGE_RUNS, VIDEO, Clock, Cookies, Ctx, Page, Recorder, capture, streaming, warmed_uuid

from relay_core.providers import Done, Found, NotSent, Output, Pending, StillUnknown
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_meta.engine import build_engine
from relay_meta.engine import reconcile as R
from relay_meta.engine import submit as S
from relay_meta.spec import SPEC

RECONCILE_FILE = "packages/provider-meta/src/relay_meta/engine/reconcile.py"
OTHER = "11111111-2222-3333-4444-555555555555"


def reconcile(page, cap, *, kind="image", ref="warmed", state=None, opened=None):
    """One reconcile of the job whose chat is `ref` (the warmed-up uuid of the capture by default)."""
    ctx = Ctx(page.log)

    @contextlib.asynccontextmanager
    async def fake_open(site, account, **kw):
        assert kw["purpose"] == "reconcile"
        if opened is not None:
            opened.append(kw["purpose"])
        yield SimpleNamespace(page=page, ctx=Cookies(), recorder=Recorder(page.events))

    clock = Clock()
    deps = S.Deps(open_session=fake_open, sleep=clock.sleep, clock=clock, signal_wait_s=0)
    job = SimpleNamespace(params={"kind": kind}, external_ref=warmed_uuid(cap) if ref == "warmed" else ref,
                          provider_state=state or {})
    return asyncio.run(R.reconcile(ctx, job, FakeSettings(SPEC), deps=deps, read_wait_s=5))


def sent_nothing(page) -> bool:
    """Only page loads: no click, no typing, no key, no file."""
    return all(x.startswith("goto ") for x in page.log) and not page.typed and not page.chosen


# ---- what the thread says ----------------------------------------------------------------------------------------
@mutation(RECONCILE_FILE, '        await page.goto(SITE_URL + "prompt/" + conversation, wait_until="domcontentloaded",',
          '        await page.keyboard.press("Enter")\n'
          '        await page.goto(SITE_URL + "prompt/" + conversation, wait_until="domcontentloaded",',
          expect="reconcile never sends")
def test_a_done_answer_in_the_reopened_chat_is_found_with_its_final_links_and_nothing_is_sent():
    for folder, kind in (*((f, "image") for f, _ in IMAGE_RUNS), (VIDEO, "video")):
        cap = capture(folder)
        page = Page([], cap, answers=[cap.answer])
        uuid = warmed_uuid(cap)
        found = reconcile(page, cap, kind=kind)
        assert sent_nothing(page), "reconcile never sends: %s" % page.log
        assert page.log == ["goto https://www.meta.ai/prompt/" + uuid], page.log
        media = S.read_answer(cap.answer, kind).media
        assert found == Found(uuid, {"conversation_id": uuid, "outputs": [
            {"ref": u, "mime": "video/mp4" if kind == "video" else "image/webp"} for u in media]}), folder
        assert found.taken


@mutation(RECONCILE_FILE, "            if answer.done and answer.media and not answer.previews:",
          "            if answer.media:", expect="unknown while")
def test_an_answer_still_streaming_or_with_a_preview_left_or_without_a_result_is_still_unknown_never_not_sent():
    cap = capture(VIDEO)
    image = capture("image-2x169-acc1")
    head, _, tail = image.answer.rpartition("/t39.105495-1/")
    bare = '<div data-testid="assistant-message" data-streaming-state="DONE" data-streaming-complete="true"><p>No.</p>'
    cases = {
        "a video still streaming": (cap, "video", streaming(cap.answer)),
        "an image still drawing": (image, "image", streaming(image.answer)),
        "a done answer with a preview left": (image, "image", head + "/t58.90637-1/" + tail),
        "a done answer with no video": (cap, "video", bare),
    }
    for what, (c, kind, html) in cases.items():
        page = Page([], c, answers=[html])
        got = reconcile(page, c, kind=kind)
        assert got == StillUnknown(answered=True), "unknown while %s: %r" % (what, got)
        assert sent_nothing(page), what


def test_no_assistant_answer_is_still_unknown_answered_only_when_meta_returned_the_thread():
    cap = capture(VIDEO)
    uuid = warmed_uuid(cap)
    assert [R.thread_read(e, uuid) for e in cap.events].count(True) == 1, "the conversation(conversationId, count) call"
    assert not any(R.thread_read(e, OTHER) for e in cap.events), "another chat's thread is not this one's"
    page = Page([], cap, answers=[""])
    assert reconcile(page, cap, kind="video") == StillUnknown(answered=True) and sent_nothing(page)
    page = Page([], cap, answers=[""])
    page.events = [e for e in page.events if not R.thread_read(e, uuid)]
    got = reconcile(page, cap, kind="video")
    assert got == StillUnknown(answered=False) and not isinstance(got, NotSent) and sent_nothing(page)


def test_a_page_that_is_not_the_signed_in_thread_proves_nothing():
    cap = capture(VIDEO)
    out = Page([], cap, answers=[cap.answer])
    out.signals = {"menu": False, "out": True, "name": "", "title": "Meta AI", "failed": False}
    assert reconcile(out, cap, kind="video") == StillUnknown() and sent_nothing(out)
    home = Page([], cap, answers=[cap.answer])
    home.lands = "https://www.meta.ai/"            # meta did not open the chat (another account's, or gone)
    assert reconcile(home, cap, kind="video") == StillUnknown() and sent_nothing(home)


def test_a_job_without_a_chat_id_opens_no_browser():
    cap = capture(VIDEO)
    for ref, state in ((None, {}), ("not-a-uuid", {}), ("../prompt/" + OTHER, {})):
        opened = []
        assert reconcile(Page([], cap), cap, kind="video", ref=ref, state=state, opened=opened) == StillUnknown()
        assert opened == [], ref
    page = Page([], cap, answers=[cap.answer])
    found = reconcile(page, cap, kind="video", ref=None, state={"conversation_id": warmed_uuid(cap)})
    assert isinstance(found, Found), "the conversation id in provider_state serves too"


# ---- the poll after Found ----------------------------------------------------------------------------------------
def poll(kind, state, ref=None):
    job = SimpleNamespace(params={"kind": kind}, provider_state=state, external_ref=ref)
    return asyncio.run(build_engine(SPEC, FakeSettings(SPEC)).poll(SimpleNamespace(), job))


def test_the_poll_hands_the_found_links_to_the_fetcher_and_answers_nothing_without_them():
    cap = capture(VIDEO)
    page = Page([], cap, answers=[cap.answer])
    found = reconcile(page, cap, kind="video")
    done = poll("video", dict(found.provider_state), found.ref)
    assert done.outcome == Done((Output(found.provider_state["outputs"][0]["ref"], "video/mp4", 0,
                                        meta={"conversation_id": found.ref}),)), done
    for state in ({}, {"outputs": []}, {"outputs": [{"ref": "https://evil.example/x.mp4", "mime": "video/mp4"}]},
                  {"outputs": [found.provider_state["outputs"][0], {"ref": "blob:https://www.meta.ai/1"}]}):
        got = poll("video", state)
        assert isinstance(got.outcome, Pending) and got.outcome.answered is False, state
