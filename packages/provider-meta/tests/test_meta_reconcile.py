"""meta's reconcile (`GET /prompt/<uuid>`, read the server-rendered chat, never send) and the poll that delivers what
it found, driven by the 05/11 prompt pages (fixtures/meta/http-261105) and the frames of the same chats."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from metakit import PROXY, Clock, Ctx, Net, Resp, frames, job, page, settings

from relay_core.providers import Done, Found, NotSent, Output, Pending, StillUnknown
from relay_core.testing.mutation import mutation
from relay_meta.engine import build_engine
from relay_meta.engine import reconcile as R
from relay_meta.engine.web import Sessions, ip_key
from relay_meta.engine.wire import Reader, link_class
from relay_meta.spec import SPEC

RECONCILE_FILE = "packages/provider-meta/src/relay_meta/engine/reconcile.py"
PROMPT = "https://www.meta.ai/prompt/"
OPS = {"t2i_n1": "text_to_image", "t2i_n2": "text_to_image", "t2i_n4": "text_to_image",
       "i2v": "image_to_video", "edit": "image_to_image", "refusal": "text_to_image"}


def cid(name: str) -> str:
    """The conversation of a recorded chat: the one its EstabStream opened."""
    est = next(f for f in frames(name, out=True) if f[0] == 0x0f)
    return json.loads(est[6:])["x-dgw-app-x-ecto-conversation-id"]


def finals(name: str, kind: str) -> tuple[str, ...]:
    reader = Reader()
    for f in frames(name):
        reader.feed(f)
    return reader.answer(kind).finals


def run(name: str | None, *, n=None, html=None, answer=None, ref="chat", state=None, sessions=None, ctx=None):
    """One reconcile of the recorded chat `name` (its page, or `html`, or the route `answer`). -> (result, net, ...)."""
    op = OPS.get(name or "", "text_to_image")
    net = Net({("GET", PROMPT): answer if answer is not None else Resp(200, html if html is not None else page(
        "prompt_page_" + name))})
    ctx = ctx or Ctx()
    sessions = sessions or Sessions(Clock())
    j = job(op, n=n, external_ref=cid(name) if ref == "chat" else ref, state=state)
    got = asyncio.run(R.reconcile(ctx, j, settings(), sessions, make_session=net.make_session))
    return got, net, ctx, sessions


def gets_only(net: Net, conversation: str | None = None) -> bool:
    return all(c.method == "GET" for c in net.calls) and (
        conversation is None or [c.url for c in net.calls] == [PROMPT + conversation])


# ---- what the chat page says --------------------------------------------------------------------------------------
@mutation(RECONCILE_FILE, "outputs = [{\"ref\": o.ref, \"mime\": o.mime} for o in media_outputs(finals, conversation, "
          "kind, n)]", "outputs = [{\"ref\": o.ref, \"mime\": o.mime} for o in media_outputs(finals, conversation, "
          "kind)]", expect="the unpaid")
@mutation(RECONCILE_FILE, "    sessions.keep(account.id, account.credential_gen, page)", "    pass",
          expect="the page's token is kept")
def test_a_done_chat_page_is_found_with_its_final_links_and_nothing_is_sent():
    for name, n in (("t2i_n1", 1), ("t2i_n2", 2), ("t2i_n4", 4)):
        conversation = cid(name)
        got, net, ctx, sessions = run(name, n=n)
        assert gets_only(net, conversation) and not [c for c in ctx.calls if c[0] in ("report_ref", "mark_sent")]
        want = finals(name, "image")
        assert len(want) == n
        assert isinstance(got, Found) and got.ref == conversation, (name, got)
        assert set(got.provider_state) == {"conversation_id", "outputs"}
        assert got.provider_state["conversation_id"] == conversation
        refs = [o["ref"] for o in got.provider_state["outputs"]]
        assert sorted(refs) == sorted(want) and all(link_class(u) == "image" for u in refs), name
        assert all(o["mime"] == "image/webp" for o in got.provider_state["outputs"]), name
        assert got.taken
        assert sessions.token(ctx.account.id, ctx.account.credential_gen) is not None, "the page's token is kept"
    got, *_ = run("t2i_n4", n=2)       # a job that paid 2 of the 4 drawn: the first 2, in page order
    every = R.parse_prompt_page(page("prompt_page_t2i_n4"), cid("t2i_n4"), "image")[1]
    assert [o["ref"] for o in got.provider_state["outputs"]] == list(every[:2]), "the unpaid ones are never delivered"
    got, *_ = run("t2i_n4")            # a job from before n: every image, as before
    assert len(got.provider_state["outputs"]) == 4


@mutation(RECONCILE_FILE, "        if cls == kind:", "        if cls in (kind, \"input\"):", expect="never the input")
@mutation(RECONCILE_FILE, '_NOT_RESULTS = ("/t39.30808-6/", ', "_NOT_RESULTS = (", expect="nor the avatar")
def test_an_i2v_page_finds_only_the_video_never_the_input_image():
    conversation = cid("i2v")
    html = page("prompt_page_i2v")
    assert "/t0.71334-6/" in html and "/t39.30808-6/" in html, "the page shows the input image and the avatar"
    got, net, *_ = run("i2v")
    assert gets_only(net, conversation)
    want = [{"ref": finals("i2v", "video")[0], "mime": "video/mp4"}]
    assert got == Found(conversation, {"conversation_id": conversation, "outputs": want}), \
        "only the video, never the input image nor the avatar: %r" % (got,)


@mutation(RECONCILE_FILE, "    if n and len(finals) >= n:", "    if False:", expect="the edit's own preview")
@mutation(RECONCILE_FILE, "    left = len(previews) if n else max(0, len(previews) - len(finals))",
          "    left = len(previews) if n else len(previews)", expect="(n=None)")
def test_an_edit_page_finds_only_the_edited_image_never_its_preview_or_input():
    conversation = cid("edit")
    want = [{"ref": finals("edit", "image")[0], "mime": "image/webp"}]
    assert "/t58.90637-1/" in page("prompt_page_edit"), "the edit page holds its draft preview as an artifact"
    for n in (1, None):
        got, net, *_ = run("edit", n=n)
        assert gets_only(net, conversation)
        assert got == Found(conversation, {"conversation_id": conversation, "outputs": want}), \
            "the edit's own preview (n=%r) is never a result nor a block: %r" % (n, got)


@mutation(RECONCILE_FILE, '    left = len(previews) if n else max(0, len(previews) - len(finals))',
          '    left = 0 if n else max(0, len(previews) - len(finals))', expect="a part delivery")
@mutation(RECONCILE_FILE, "    return done, tuple(finals.values()), left + len(unknown)",
          "    return done, tuple(finals.values()), left", expect="an unknown link")
@mutation(RECONCILE_FILE, '    done = bool(states) and all(s == "DONE" for s in states) and conversation in text',
          "    done = True", expect="still streaming")
def test_a_page_short_of_its_results_still_streaming_or_with_an_unknown_link_is_still_unknown():
    html = page("prompt_page_t2i_n2")
    second = finals("t2i_n2", "image")[1]
    path = second.split("?")[0].split("/v/")[1]
    drawing = html.replace("/v/" + path, "/v/" + path.replace("t39.105495-1", "t58.90637-1"))
    streaming = html.replace('\\"streamingState\\":\\"DONE\\"', '\\"streamingState\\":\\"STREAMING\\"', 1)
    odd = html + '<img src="https://scontent.xx.fbcdn.net/v/t45.1234-1/9_9_9_n.webp">'
    cases = (("a part delivery: 1 of 2 paid with a preview left", drawing, 2),
             ("still streaming", streaming, 2),
             ("an unknown link on a job with no n", odd, None))
    for what, h, n in cases:
        got, net, *_ = run("t2i_n2", n=n, html=h)
        assert got == StillUnknown(answered=True), "%s: %r" % (what, got)
        assert gets_only(net)


@mutation(RECONCILE_FILE, "    return StillUnknown(answered=conversation in page.html)",
          "    from relay_core.providers import NotSent\n    return NotSent(True)", expect="never not sent")
def test_the_refusal_page_is_still_unknown_never_not_sent():
    got, net, *_ = run("refusal", n=1)
    assert got == StillUnknown(answered=True) and not isinstance(got, NotSent), "never not sent: %r" % (got,)
    assert gets_only(net, cid("refusal"))
    got, *_ = run("refusal", n=1, ref="11111111-2222-3333-4444-555555555555")     # a page of another chat
    assert got == StillUnknown(answered=False), "another chat's page proves nothing: %r" % (got,)


@mutation(RECONCILE_FILE, "    if page.state != \"in\":", "    if False:", expect="proves nothing")
def test_a_closed_gate_or_a_signed_out_page_proves_nothing():
    sessions = Sessions(Clock())
    sessions.gate.take(ip_key(PROXY), 300)            # a page was loaded on this IP just now
    got, net, *_ = run("t2i_n1", n=1, sessions=sessions)
    assert got == StillUnknown(answered=False) and net.calls == [], "a closed gate sends no request"
    chat_out = page("prompt_page_t2i_n1").replace('\\"isLoggedIn\\":true', '\\"isLoggedIn\\":false')
    for what, answer in (("signed out", Resp(200, page("page_signed_out"))),
                         ("the chat's links on a signed-out page", Resp(200, chat_out)),
                         ("vercel", Resp(429, page("page_vercel"))),
                         ("unreadable", Resp(500, "oops")),
                         ("no answer", ConnectionError("reset"))):
        got, net, *_ = run("t2i_n1", n=1, answer=answer)
        assert got == StillUnknown(answered=False), "%s proves nothing: %r" % (what, got)
        assert gets_only(net)


@mutation(RECONCILE_FILE, "        if note is not None:\n", "        if False:\n", expect="noted")
def test_a_done_page_with_fewer_finals_than_paid_is_found_charged_in_full_and_noted(caplog):
    html = page("prompt_page_t2i_n2")
    second = finals("t2i_n2", "image")[1]
    path = second.split("?")[0].split("/v/")[1]
    short = html.replace("/v/" + path, "/v/" + path.replace("t39.105495-1", "t0.71334-1"))     # an input, ignored
    done, found, left = R.parse_prompt_page(short, cid("t2i_n2"), "image", 2)
    assert (done, len(found), left) == (True, 1, 0), "the page holds 1 final and no preview"
    got, net, ctx, _ = run("t2i_n2", n=2, html=short)
    assert isinstance(got, Found) and len(got.provider_state["outputs"]) == 1, got
    note = "meta made 1 of the 2 images asked; all are charged (any output is a full charge)"
    assert (note, "reconcile", "engine") in ctx.events, "noted: %r" % (ctx.events,)
    assert any("job-1" in r.getMessage() and note in r.getMessage() for r in caplog.records), "noted in the log"
    _, _, ctx, _ = run("t2i_n2", n=2)
    assert ctx.events == [], "as paid: no note"


@mutation(RECONCILE_FILE, "            page = await web.page(CHAT_PATH + conversation, solve=False)",
          "            page = await web.page(CHAT_PATH + conversation)", expect="no POST")
def test_a_challenged_chat_page_is_still_unknown_and_its_challenge_is_never_posted():
    rd = Resp(403, page("page_rd_403"))
    got, net, *_ = run("t2i_n1", n=1, answer=rd)
    assert got == StillUnknown(answered=False), got
    assert net.methods() == ["GET"], "no POST of the rd challenge: %r" % (net.methods(),)


# ---- never a send -------------------------------------------------------------------------------------------------
@mutation(RECONCILE_FILE, "from relay_meta.engine.wire import is_final, link_class, made_note, media_outputs",
          "from relay_meta.engine.wire import is_final, link_class, made_note, media_outputs, send_frame",
          expect="reconcile never sends")
def test_reconcile_never_imports_the_socket_or_the_upload():
    tree = ast.parse((Path(__file__).resolve().parents[3] / RECONCILE_FILE).read_text())
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom | ast.Import)
              for a in n.names}
    banned = {"socket", "Socket", "upload", "send_frame", "build_request", "estab", "submit", "ws_connect"}
    assert not names & banned, "reconcile never sends: it names %s" % sorted(names & banned)
    for name in OPS:
        _, net, *_ = run(name, n=1)
        assert net.calls and gets_only(net), name


# ---- jobs from the browser engine -----------------------------------------------------------------------------------
@mutation(RECONCILE_FILE, "not isinstance(conversation, str) or not _UUID.fullmatch(conversation):",
          "not isinstance(conversation, str):", expect="no request for a bad ref")
def test_jobs_left_unknown_by_the_browser_engine_reconcile_with_the_same_ref():
    conversation = cid("t2i_n2")
    got, net, *_ = run("t2i_n2", n=2, state={})      # external_ref only, no provider_state: the browser era's job
    assert isinstance(got, Found) and got.ref == conversation and [c.url for c in net.calls] == [PROMPT + conversation]
    got, net, *_ = run("t2i_n2", n=2, ref=None, state={"conversation_id": conversation})
    assert isinstance(got, Found), "the conversation id in provider_state serves too"
    for ref in (None, "not-a-uuid", "../prompt/" + conversation, conversation + "/x"):
        got, net, *_ = run("t2i_n2", n=2, ref=ref, state={})
        assert got == StillUnknown() and net.calls == [], "no request for a bad ref: %r" % (ref,)


# ---- the poll after Found ----------------------------------------------------------------------------------------
def poll(kind, state, ref=None):
    j = SimpleNamespace(params={"kind": kind}, provider_state=state, external_ref=ref)
    return asyncio.run(build_engine(SPEC, settings()).poll(SimpleNamespace(), j))


@mutation(RECONCILE_FILE, 'is_final(str(o.get("ref") or ""), kind) for o in outs)', "True for o in outs)",
          expect="a link that is no result")
def test_the_poll_hands_the_found_links_to_the_fetcher_and_answers_nothing_without_them():
    found, *_ = run("i2v")
    done = poll("video", dict(found.provider_state), found.ref)
    assert done.outcome == Done((Output(found.provider_state["outputs"][0]["ref"], "video/mp4", 0,
                                        meta={"conversation_id": found.ref}),)), done
    browser = {"conversation_id": "c", "outputs": [      # a job the browser engine found before the swap
        {"ref": "https://scontent.xx.fbcdn.net/v/t39.105495-1/1_2_3_n.webp?oh=x", "mime": "image/webp"}]}
    assert isinstance(poll("image", browser).outcome, Done), "a browser-era Found still delivers"
    image = found.provider_state["outputs"][0]
    for kind, state in (("video", {}), ("video", {"outputs": []}),
                        ("video", {"outputs": [{"ref": "https://evil.example/x.mp4", "mime": "video/mp4"}]}),
                        ("video", {"outputs": [image, {"ref": "blob:https://www.meta.ai/1"}]}),
                        ("video", {"outputs": [{"ref": image["ref"].replace("t39.105495-1", "t58.90637-1")}]}),
                        ("image", {"outputs": [image]})):
        got = poll(kind, state)
        assert isinstance(got.outcome, Pending) and got.outcome.answered is False, "a link that is no result: %r" % (
            state,)
