"""Reconcile, poll and fetch (`engine/results.py`): reads only, through the account's proxy, the cookie to
assets.grok.com alone."""

import ast
import asyncio
import copy
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from grokkit import PROXY, Ctx, FakeSession, Resp, assert_no_secret, fixture, job, settings

from relay_core.providers import Done, Found, Output, OutputGone, Pending, Rest, StillUnknown, Suspect
from relay_core.testing.mutation import mutation
from relay_grok.constants import (
    LIST_CONVERSATIONS_PATH,
    PUBLIC_HOST,
    RESPONSES_PATH,
    STATE_CONVERSATION,
    STATE_REQUEST,
    STATE_SENT_TEXT,
    VIDEO_KIND,
)
from relay_grok.engine import results
from relay_grok.engine.results import fetch, poll, reconcile
from relay_grok.engine.web import Refused, proxy_url

FILE = "packages/provider-grok/src/relay_grok/engine/results.py"
PROXIES = {"http": proxy_url(PROXY), "https": proxy_url(PROXY)}
SPIKE_USER = "d0000000-0000-4000-8000-000000000001"
EDIT, T2V, T2I, EMPTY = ("c0000000-0000-4000-8000-00000000000%d" % i for i in (1, 7, 8, 2))
EDIT_PROMPT = "make the apple green"
T2V_PROMPT = "a paper boat drifting on a calm lake at sunrise, gentle ripples"
T2I_PROMPT = "a red apple on a wooden table, soft daylight, photo"
VIDEO_REF = "https://assets.grok.com/users/%s/generated/a1000000-0000-4000-8000-000000000001/generated_video.mp4" \
    % SPIKE_USER
EDIT_REF = "https://assets.grok.com/users/%s/generated/af000000-0000-4000-8000-000000000002/image.jpg" % SPIKE_USER
T2I_REFS = ["https://imagine-public.x.ai/imagine-public/images/%s.jpg" % a
            for a in ("af000000-0000-4000-8000-000000000003", "a1000000-0000-4000-8000-000000000002")]
T2V_SENT = datetime(2026, 11, 6, 4, 30, 9, 900000, tzinfo=UTC)
EDIT_SENT = datetime(2026, 11, 6, 4, 27, 20, tzinfo=UTC)    # early, so the four empty probe conversations are read too
DECOY = StillUnknown(answered=True, effects=(Rest("video", datetime(2026, 11, 7, 5, tzinfo=UTC), "decoy"),))
DECOY_REST = "        return StillUnknown(answered=read_ok, effects=(rest,) if decoys else ())"


def body(name: str) -> dict:
    return copy.deepcopy(fixture(name)["body"])


def ok(b) -> Resp:
    return Resp(200, {"content-type": "application/json"}, json_body=b)


def responses(cid: str) -> str:
    return RESPONSES_PATH % cid


def assets(b: dict) -> list[dict]:
    return [a for t in b["responses"] for a in t["fileAttachmentAssetMetadata"] if a["isModelGenerated"]]


def lists(plain: dict | None = None, imagine: dict | None = None):
    """The list route: the no-kind listing (edits) or the IMAGINE one, by the query."""
    def answer(call):
        return ok(imagine if "kind=" + VIDEO_KIND in call.url else plain)
    return answer


def run(j, routes: dict, ctx: Ctx | None = None, **set_over):
    ctx = ctx or Ctx(settings_=settings(**set_over))
    s = FakeSession(routes, calls=ctx.calls)
    got = asyncio.run(reconcile(ctx, j, ctx.settings, make_session=s,
                                now=lambda: datetime(2026, 11, 6, 5, tzinfo=UTC)))
    return got, s, ctx


def by_id(op: str, cid: str, **kw):
    return job(op, prompt=kw.pop("prompt", "x"), n=kw.pop("n", 1),
               state={STATE_REQUEST: "r-1", STATE_CONVERSATION: cid}, **kw)


def by_text(op: str, prompt: str, sent_at: datetime, sent_text: str | None = None, n: int = 1):
    return job(op, prompt=prompt, n=n, sent_at=sent_at,
               state={STATE_REQUEST: "r-1", STATE_SENT_TEXT: sent_text if sent_text is not None else prompt})


# ---- reconcile by the conversation id --------------------------------------------------------------------------------
def test_a_read_back_of_a_finished_video_is_found_with_its_url():
    got, s, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(body("readback_t2v"))})
    assert got == Found(T2V, {STATE_CONVERSATION: T2V, "outputs": [
        {"ref": VIDEO_REF, "mime": "video/mp4", "upstream_id": "a1000000-0000-4000-8000-000000000001"}]}, taken=True)
    assert [(c.method, c.path) for c in s.requests] == [("GET", responses(T2V))]
    assert all(c.proxies == PROXIES and c.allow_redirects is False for c in s.requests)

    got, _, _ = run(by_id("edit", EDIT), {("GET", responses(EDIT)): ok(body("readback_edit"))})
    assert [o["ref"] for o in got.provider_state["outputs"]] == [EDIT_REF], "the edit's input image is no output"

    got, _, _ = run(by_id("t2i", T2I, n=2), {("GET", responses(T2I)): ok(body("readback_t2i"))})
    assert [o["ref"] for o in got.provider_state["outputs"]] == T2I_REFS
    assert {o["mime"] for o in got.provider_state["outputs"]} == {"image/jpeg"}


@mutation(FILE, '            if kind == "video" and "rRated" not in asset:',
          '            if kind == "video" and not asset.get("rRated"):', expect="rRated false must count as present")
@mutation(FILE, '            if kind == "video" and "rRated" not in asset:', '            if False:',
          expect="a video without rRated was found")
def test_a_read_back_without_rrated_is_not_found():
    real = body("readback_t2v")
    assert assets(real)[0]["rRated"] is False
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(real)})
    assert isinstance(got, Found), "rRated false must count as present"

    decoy = body("readback_t2v")
    del assets(decoy)[0]["rRated"]
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(decoy)})
    assert got == DECOY, "a video without rRated was found"


@mutation(FILE, "            if kind == \"video\" and decoy_min_s and (took is None or took < decoy_min_s):",
          "            if False:", expect="a decoy too fast was found")
def test_a_video_done_faster_than_decoy_min_s_is_not_found_and_zero_turns_the_time_rule_off():
    route = {("GET", responses(T2V)): ok(body("readback_t2v"))}       # 33.6 s from the ask to the answer
    got, _, _ = run(by_id("t2v", T2V), route, decoy_min_s=60)
    assert got == DECOY, "a decoy too fast was found"
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(body("readback_t2v"))}, decoy_min_s=30)
    assert isinstance(got, Found)
    decoy = body("readback_t2v")
    del assets(decoy)[0]["rRated"]
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(decoy)}, decoy_min_s=0)
    assert got == DECOY, "decoy_min_s 0 still checks rRated"


@mutation(FILE, DECOY_REST, "        return StillUnknown(answered=read_ok)",
          expect="a decoy read back must rest the video pool")
def test_a_decoy_read_back_rests_the_video_pool_like_the_stream():
    decoy = body("readback_t2v")
    del assets(decoy)[0]["rRated"]
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(decoy)}, decoy_rest_s=3600)
    assert got == StillUnknown(answered=True, effects=(
        Rest("video", datetime(2026, 11, 6, 6, tzinfo=UTC), "decoy"),)), "a decoy read back must rest the video pool"
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(body("readback_empty"))})
    assert got == StillUnknown(answered=True), "no video at all is no decoy: nothing rests"


@mutation(FILE, '            if (asset.get("auxKeys") or {}).get("moderated") == "true":',
          '            if (asset.get("auxKeys") or {}).get("moderated") is True:',
          expect="a moderated read-back was delivered")
def test_a_moderated_read_back_is_not_found():
    for name, op, cid in (("readback_edit", "edit", EDIT), ("readback_t2v", "t2v", T2V)):
        b = body(name)
        assets(b)[0]["auxKeys"]["moderated"] = "true"
        got, _, _ = run(by_id(op, cid), {("GET", responses(cid)): ok(b)})
        assert got == StillUnknown(answered=True), "a moderated read-back was delivered"

    b = body("readback_t2i")                # one of two moderated: the other is delivered, with a note
    assets(b)[0]["auxKeys"]["moderated"] = "true"
    got, _, ctx = run(by_id("t2i", T2I, n=2), {("GET", responses(T2I)): ok(b)})
    assert [o["ref"] for o in got.provider_state["outputs"]] == T2I_REFS[1:], "a moderated read-back was delivered"
    assert [e[1:] for e in ctx.events] == [("reconcile", "engine")] and "1 of the 2" in ctx.events[0][0]


def test_more_images_than_n_are_cut_to_the_first_n():
    got, _, ctx = run(by_id("t2i", T2I, n=1), {("GET", responses(T2I)): ok(body("readback_t2i"))})
    assert [o["ref"] for o in got.provider_state["outputs"]] == T2I_REFS[:1]
    assert "the first 1 are delivered" in ctx.events[0][0]


def test_an_empty_running_or_unreadable_conversation_is_still_unknown():
    got, _, _ = run(by_id("edit", EMPTY), {("GET", responses(EMPTY)): ok(body("readback_empty"))})
    assert got == StillUnknown(answered=True), "a refused send's empty conversation is no result"
    running = body("readback_t2v")
    running["inflightResponses"] = [{"responseId": "e-9"}]
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ok(running)})
    assert got == StillUnknown(answered=True)
    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): Resp(500, text="oops")})
    assert got == StillUnknown(answered=False)


# ---- reconcile by the sent text (cut before the first line) ----------------------------------------------------------
@mutation(FILE, '                if job.operation != "image_to_image":', "                if True:",
          expect="an edit was looked for among the imagine conversations")
def test_a_run_cut_before_the_first_line_is_matched_by_its_exact_text_in_the_window():
    reads = {("GET", responses(T2V)): ok(body("readback_t2v")), ("GET", responses(T2I)): ok(body("readback_t2i"))}
    got, s, _ = run(by_text("t2v", T2V_PROMPT, T2V_SENT),
                    {("GET", LIST_CONVERSATIONS_PATH): lists(imagine=body("list_conversations_imagine")), **reads})
    assert got == Found(T2V, {STATE_CONVERSATION: T2V, "outputs": [
        {"ref": VIDEO_REF, "mime": "video/mp4", "upstream_id": "a1000000-0000-4000-8000-000000000001"}]}, taken=True)
    assert "kind=" + VIDEO_KIND in s.requests[0].url
    assert all(c.method == "GET" and c.proxies == PROXIES for c in s.requests)

    # an edit is listed with no kind; the 400-code-3 conversations beside it have no human turn and never match
    reads = {("GET", responses("c0000000-0000-4000-8000-00000000000%d" % i)): ok(body("readback_empty"))
             for i in range(2, 7)}
    reads[("GET", responses(EDIT))] = ok(body("readback_edit"))
    got, s, _ = run(by_text("edit", EDIT_PROMPT, EDIT_SENT),
                    {("GET", LIST_CONVERSATIONS_PATH): lists(plain=body("list_conversations"),
                                                             imagine={"conversations": []}), **reads})
    assert "kind=" not in s.requests[0].url, "an edit was looked for among the imagine conversations"
    assert isinstance(got, Found) and got.ref == EDIT, "an edit was looked for among the imagine conversations"

    got, _, _ = run(by_text("t2i", T2I_PROMPT, datetime(2026, 11, 6, 4, 28, 34, tzinfo=UTC), n=2),
                    {("GET", LIST_CONVERSATIONS_PATH): lists(imagine=body("list_conversations_imagine")),
                     ("GET", responses(T2V)): ok(body("readback_t2v")),
                     ("GET", responses(T2I)): ok(body("readback_t2i"))})
    assert isinstance(got, Found) and got.ref == T2I


@mutation(FILE, "    if len(found) != 1:", "    if not found:", expect="a twin prompt was matched")
@mutation(FILE, "                low = sent_at - timedelta(seconds=FIND_BEFORE_S)",
          "                low = sent_at - timedelta(days=1)", expect="a conversation outside the window matched")
def test_a_twin_prompt_outside_the_window_or_already_claimed_never_matches():
    routes = {("GET", LIST_CONVERSATIONS_PATH): lists(imagine=body("list_conversations_imagine")),
              ("GET", responses(T2V)): ok(body("readback_t2v")), ("GET", responses(T2I)): ok(body("readback_t2i"))}
    later = T2V_SENT + timedelta(minutes=10)          # the same words sent again 10 min later: the old one is not it
    got, s, _ = run(by_text("t2v", T2V_PROMPT, later), routes)
    assert got == StillUnknown(answered=True), "a conversation outside the window matched"
    assert [c.path for c in s.requests] == [LIST_CONVERSATIONS_PATH], "a conversation outside the window was read"

    twin = body("list_conversations_imagine")         # two conversations in the window hold the same words
    twin["conversations"][1]["createTime"] = twin["conversations"][0]["createTime"]
    got, _, _ = run(by_text("t2v", T2V_PROMPT, T2V_SENT), {
        **routes, ("GET", LIST_CONVERSATIONS_PATH): lists(imagine=twin),
        ("GET", responses(T2I)): ok(body("readback_t2v"))})
    assert got == StillUnknown(answered=True), "a twin prompt was matched"

    got, _, _ = run(by_text("t2v", "another prompt", T2V_SENT), routes)
    assert got == StillUnknown(answered=True), "another text matched"


@mutation(FILE, "    if not read_ok:", "    if False:", expect="a match was taken beside an unread candidate")
def test_a_match_beside_an_unread_candidate_is_still_unknown():
    twin = body("list_conversations_imagine")         # two conversations in the window: one matches, one is unread
    twin["conversations"][1]["createTime"] = twin["conversations"][0]["createTime"]
    got, _, _ = run(by_text("t2v", T2V_PROMPT, T2V_SENT), {
        ("GET", LIST_CONVERSATIONS_PATH): lists(imagine=twin),
        ("GET", responses(T2V)): ok(body("readback_t2v")), ("GET", responses(T2I)): Resp(500, text="oops")})
    assert got == StillUnknown(answered=False), "a match was taken beside an unread candidate"


@mutation(FILE, "    if len(found) != 1:         # none, or a twin prompt: another job's conversation is never taken "
                "for this one\n        return StillUnknown(answered=read_ok)",
          "    if len(found) != 1:\n        return __import__('relay_core.providers').providers.NotSent(True)",
          expect="reconcile must never claim not sent")
def test_nothing_found_is_still_unknown_never_not_sent():
    empty = {"conversations": []}
    got, _, _ = run(by_text("t2v", T2V_PROMPT, T2V_SENT), {("GET", LIST_CONVERSATIONS_PATH): lists(imagine=empty)})
    assert got == StillUnknown(answered=True), "reconcile must never claim not sent"
    got, _, _ = run(by_text("t2v", T2V_PROMPT, T2V_SENT), {("GET", LIST_CONVERSATIONS_PATH): Resp(500, text="x")})
    assert got == StillUnknown(answered=False)
    got, _, _ = run(job("t2v", state={STATE_REQUEST: "r-1"}), {})
    assert got == StillUnknown(), "no id and no send time: nothing to look for"

    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): Resp(401, json_body=fixture(
        "rest_401_bad_credentials")["body"])})
    assert isinstance(got, StillUnknown) and not got.answered
    assert any(isinstance(e, Suspect) for e in got.effects), "a signed-out read is a spaced strike"

    got, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ConnectionError("broken")})
    assert got == StillUnknown()


# ---- poll ------------------------------------------------------------------------------------------------------------
def test_the_poll_hands_the_found_outputs_to_the_fetcher_and_answers_nothing_without_them():
    found, _, _ = run(by_id("t2i", T2I, n=2), {("GET", responses(T2I)): ok(body("readback_t2i"))})
    ctx = Ctx()
    done = asyncio.run(poll(ctx, job("t2i", n=2, state=dict(found.provider_state)), ctx.settings)).outcome
    assert done == Done((Output(T2I_REFS[0], "image/jpeg", 0, "af000000-0000-4000-8000-000000000003"),
                         Output(T2I_REFS[1], "image/jpeg", 1, "a1000000-0000-4000-8000-000000000002")))
    for state in ({}, {"outputs": []}, {"outputs": [{"ref": "https://evil.example/a.jpg", "mime": "image/jpeg"}]}):
        got = asyncio.run(poll(ctx, job("t2i", state=state), ctx.settings)).outcome
        assert isinstance(got, Pending) and got.answered is False


# ---- fetch -----------------------------------------------------------------------------------------------------------
class Download:
    """The `stream_download` seam: records its call, yields `data` (or raises `error`)."""

    def __init__(self, data: bytes = b"jpeg", error: BaseException | None = None):
        self.data, self.error, self.calls = data, error, []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))

        async def gen():
            if self.error is not None:
                raise self.error
            yield self.data
        return gen()


def fetched(ref: str, routes: dict | None = None, *, ctx: Ctx | None = None, download: Download | None = None):
    ctx = ctx or Ctx()
    s = FakeSession(routes or {}, calls=ctx.calls)
    download = download or Download()

    async def go():
        got = []
        try:
            async for part in fetch(ctx, Output(ref, "image/jpeg", 0), ctx.settings, make_session=s,
                                    download=download):
                got.append(part)
        except Exception as e:      # noqa: BLE001 - the test reads what was raised
            return got, e
        return got, None
    got, err = asyncio.run(go())
    return got, err, s, download


@mutation(FILE, '    if p.hostname == ASSETS_HOST and cookie_host(url):', "    if True:",
          expect="the cookie left assets.grok.com")
def test_the_cookie_goes_only_to_assets_grok_com():
    got, err, s, d = fetched(T2I_REFS[0])
    assert err is None and got == [b"jpeg"] and not s.requests, "imagine-public got a request with the cookie"
    assert d.calls == [(T2I_REFS[0], {"hosts": (PUBLIC_HOST,), "proxy": proxy_url(PROXY),
                                      "max_bytes": 64 * 2**20, "deadline_s": 120})]

    got, err, s, d = fetched(VIDEO_REF, {("GET", "assets.grok.com/"): Resp(200, content=b"mp4")})
    assert err is None and got == [b"mp4"] and not d.calls
    assert [(c.host, c.has_cookie) for c in s.requests] == [("assets.grok.com", True)]

    for ref in ("https://evil.example/users/u/generated/a/image.jpg", "http://assets.grok.com/users/u/a/image.jpg",
                "https://assets.grok.com:8443/users/u/a/image.jpg", "https://assets.grok.com.evil.example/a.jpg",
                "https://u:p@assets.grok.com/a.jpg", "https://assets.grok.com/users/../x/a.jpg",
                "http://imagine-public.x.ai/imagine-public/images/a.jpg"):
        got, err, s, d = fetched(ref, {("GET", ""): Resp(200, content=b"stolen")})
        assert isinstance(err, OutputGone) and not s.requests and not d.calls, "the cookie left assets.grok.com"
        assert err.code == "render.download_failed"


def test_every_fetch_goes_through_the_account_proxy():
    _, _, s, _ = fetched(VIDEO_REF, {("GET", "assets.grok.com/"): Resp(200, content=b"mp4")})
    assert s.made == [PROXIES] and s.requests[0].proxies == PROXIES and s.requests[0].allow_redirects is False
    _, _, s, _ = fetched(VIDEO_REF, {("GET", "assets.grok.com/"): Resp(200, content=b"mp4")}, ctx=Ctx(proxy=None))
    assert s.made == [None] and s.requests[0].proxies is None
    _, _, _, d = fetched(T2I_REFS[0], ctx=Ctx(proxy=None))
    assert d.calls[0][1]["proxy"] is None


@mutation(FILE, '        if e.kind == "gone":', "        if False:", expect="a gone file must be OutputGone")
def test_a_gone_file_is_output_gone_and_a_500_raises():
    for status in (403, 404):
        _, err, _, _ = fetched(VIDEO_REF, {("GET", "assets.grok.com/"): Resp(status, text="")})
        assert isinstance(err, OutputGone) and err.code == "render.download_failed", \
            "a gone file must be OutputGone"
    _, err, _, _ = fetched(VIDEO_REF, {("GET", "assets.grok.com/"): Resp(500, text="")})
    assert isinstance(err, Refused) and not isinstance(err, OutputGone), "a 500 is retried by the core"
    _, err, _, _ = fetched(T2I_REFS[0], download=Download(error=RuntimeError("status")))
    assert isinstance(err, RuntimeError), "a failed public download raises: the core downloads again"


# ---- guards ----------------------------------------------------------------------------------------------------------
@mutation(FILE, '                status, listing = await web.read_json("GET", path)',
          '                status, listing = await web.read_json("GET", path); web.send_new',
          expect="reconcile must not send")
def test_results_never_sends():
    tree = ast.parse(Path(results.__file__).read_text(encoding="utf-8"))
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    used |= {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
             for a in n.names}
    banned = {"send_new", "socket", "upload", "NEW_CONVERSATION_PATH", "item_create", "send_json", "IMAGINE_WS"}
    assert not used & banned, "reconcile must not send"


def test_no_secret_leaves(caplog):
    caplog.set_level(logging.DEBUG)
    ctx = Ctx()
    got, s, _ = run(by_id("t2i", T2I, n=1), {("GET", responses(T2I)): ok(body("readback_t2i"))}, ctx=ctx)
    sent = s.requests[0]
    assert sent.cookies and all(v not in sent.url for v in sent.cookies.values())

    class ProxyError(Exception):
        pass
    failed, _, _ = run(by_id("t2v", T2V), {("GET", responses(T2V)): ProxyError("CONNECT %s" % proxy_url(PROXY))},
                       ctx=ctx)
    _, err, _, _ = fetched(VIDEO_REF, {("GET", "assets.grok.com/"): ProxyError("CONNECT %s" % proxy_url(PROXY))},
                           ctx=ctx)
    _, gone, _, _ = fetched(VIDEO_REF, {("GET", "assets.grok.com/"): Resp(404, text="")}, ctx=ctx)
    assert failed == StillUnknown()
    assert_no_secret(got, failed, err, gone, gone.detail, caplog.text, ctx.events, ctx.calls, ctx.refs)


@pytest.mark.parametrize("op", ["t2i", "edit", "t2v"])
def test_reconcile_reads_nothing_but_the_two_read_back_paths(op):
    cid = {"t2i": T2I, "edit": EDIT, "t2v": T2V}[op]
    _, s, ctx = run(by_id(op, cid, n=2 if op == "t2i" else 1),
                    {("GET", responses(cid)): ok(body({"t2i": "readback_t2i", "edit": "readback_edit",
                                                       "t2v": "readback_t2v"}[op]))})
    assert [c.method for c in s.requests] == ["GET"] and not ctx.refs and "mark_sent" not in str(ctx.calls)
