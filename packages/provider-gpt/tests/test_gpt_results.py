"""The gpt reads after the send: poll the conversation, reconcile a send whose answer was lost, and download a result
with the bearer through the proxy. Nothing here sends, hides or deletes; reconcile never says NotSent."""

import asyncio
import copy
from datetime import UTC, datetime, timedelta

import pytest
from gptkit import ACCESS, NOW, PROXY, Ctx, FakeSession, Resp, assert_no_secret, fixture, job, settings

from relay_core.providers import Done, Failed, Found, Output, OutputGone, Pending, StillUnknown, Suspect
from relay_core.testing.mutation import mutation
from relay_gpt.constants import STATE_CONVERSATION, STATE_MESSAGE, STATE_PROMPT
from relay_gpt.engine.results import FIND_AFTER_S, FIND_BEFORE_S, fetch, poll, reconcile
from relay_gpt.engine.web import Sessions, proxy_url
from relay_gpt.errors import IMAGE_LOST, OUTPUT_MISSING, REFUSED

RESULTS_FILE = "packages/provider-gpt/src/relay_gpt/engine/results.py"
NEVER_NOT_SENT = "    return StillUnknown(answered=read_ok)\n"
NOT_SENT = "    return __import__('relay_core.providers', fromlist=['x']).NotSent(covers_send_window=True)\n"
CLAIMED = "        return False                 # claimed by another message id: never matched by its text\n"
NOT_CLAIMED = "        pass\n"
HOST_CHECK = "    if parts.scheme != \"https\" or parts.netloc != SITE_HOST or parts.path != ESTUARY_PATH:\n"
NO_HOST_CHECK = "    if False:\n"
NO_PATCH = "        yield data\n"
PATCH_CALL = ("        yield data\n        await web._call(\"PATCH\", \"/backend-api/conversation/\" + cid, "
              "data='{\"is_visible\": false}')\n")

T2I = fixture("conv_t2i")["body"]
CID = T2I["conversation_id"]
OURS = "1b4326f5-8fe9-409d-8f62-4906eb968b28"          # conv_t2i's user message id (client-made)
PROMPT = "Generate an image: a bowl of ramen on a wooden table, top-down food photography, square 1:1."
FILE_ID = "file_0000000087cc820797dbca5224902b9c"
SENT = datetime.fromtimestamp(NOW, UTC)
CONV = "/backend-api/conversation/"
LIST = "/backend-api/conversations"
ESTUARY = "/backend-api/estuary/content"
PNG = b"\x89PNG\r\n\x1a\n" + b"P" * 100


def run(coro):
    return asyncio.run(coro)


def session_body() -> dict:
    body = fixture("auth_session")["body"]
    body["accessToken"] = ACCESS
    return body


class Maker:
    """`make_session`: one FakeSession per call over the same routes and log."""

    def __init__(self, table: dict, log: list | None = None):
        self.table = {("GET", "/api/auth/session"): Resp(json_body=session_body()), **table}
        self.log = log if log is not None else []
        self.sessions: list[FakeSession] = []
        self.proxies: list = []

    def __call__(self, proxies):
        self.proxies.append(proxies)
        s = FakeSession(self.table, self.log, proxies=proxies)
        self.sessions.append(s)
        return s

    def requests(self):
        return [r for s in self.sessions for r in s.requests]


def conv(cid: str, user_id: str | None, text: str = PROMPT, *, image: bool = True, create: float = NOW + 5,
         words: str = "", done: bool = True) -> dict:
    """A conversation read: one user message (`user_id`, text), an image tool message, an assistant end."""
    body = copy.deepcopy(T2I)
    body["conversation_id"], body["create_time"] = cid, create
    mapping = {}
    for node in body["mapping"].values():
        m = node.get("message")
        if not m:
            mapping[node["id"]] = node
            continue
        role = m["author"]["role"]
        if role == "user":
            m["content"]["parts"] = [text]
            if user_id is None:
                m.pop("id")
            else:
                m["id"] = user_id
        elif role == "tool" and not image:
            continue
        elif role == "assistant" and m["content"]["content_type"] == "text":
            m["content"]["parts"], m["end_turn"] = [words], done
            m["status"] = "finished_successfully" if done else "in_progress"
        mapping[node["id"]] = node
    body["mapping"] = mapping
    return body


def gpt_job(state=None, *, sent=SENT):
    return job(state=state if state is not None else {STATE_MESSAGE: OURS, STATE_PROMPT: PROMPT}, sent_at=sent)


def go_poll(maker, j, *, at=SENT, s=None):
    ctx = Ctx(maker.log)
    return run(poll(ctx, j, s or settings(), Sessions(clock=lambda: NOW), make_session=maker, now=lambda: at)), ctx


def go_reconcile(maker, j):
    ctx = Ctx(maker.log)
    return run(reconcile(ctx, j, settings(), Sessions(clock=lambda: NOW), make_session=maker)), ctx


def go_fetch(maker, out):
    ctx = Ctx(maker.log)

    async def drain():
        return [part async for part in fetch(ctx, out, settings(), Sessions(clock=lambda: NOW), make_session=maker)]
    return run(drain()), ctx


def output(cid=CID, file_id=FILE_ID):
    return Output("sediment://" + file_id, "image/png", 0, file_id, {"conversation": cid, "width": 1254,
                                                                     "height": 1254})


def download(url: str) -> Resp:
    body = fixture("attachment_download")["body"]
    body["download_url"] = url
    return Resp(json_body=body)


# ---- reconcile ---------------------------------------------------------------------------------------------------
def listing(*items) -> Resp:
    return Resp(json_body={"items": [{"id": cid, "title": "t", "create_time": t} for cid, t in items],
                           "total": len(items), "limit": 28, "offset": 0})


@mutation(RESULTS_FILE, NEVER_NOT_SENT, NOT_SENT, expect="reconcile must never claim not sent")
def test_reconcile_with_nothing_found_is_still_unknown():
    other = "c-other"
    maker = Maker({("GET", LIST): listing((other, NOW + 3)), ("GET", CONV + other): Resp(json_body=conv(other, "x"))})
    result, _ = go_reconcile(maker, gpt_job())
    assert result == StillUnknown(answered=True), "reconcile must never claim not sent"
    # an empty listing, a failed listing and an unreadable one: never NotSent
    maker = Maker({("GET", LIST): listing()})
    assert go_reconcile(maker, gpt_job())[0] == StillUnknown(answered=True), "reconcile must never claim not sent"
    for answer in (Resp(500, text="oops"), Resp(text="not json"), Resp(json_body={"items": "x"})):
        maker = Maker({("GET", LIST): answer})
        assert go_reconcile(maker, gpt_job())[0] == StillUnknown(answered=False), "reconcile must never claim not sent"
    # no message id written ahead: nothing to match by, still never NotSent
    assert go_reconcile(Maker({}), gpt_job({}))[0] == StillUnknown(), "reconcile must never claim not sent"


@mutation(RESULTS_FILE, CLAIMED, NOT_CLAIMED, expect="matched the twin prompt")
def test_reconcile_takes_the_conversation_with_its_own_message_id_not_the_twin_prompt():
    # Review Focus 2: two jobs, one prompt, one account. The older conversation is the other job's (its own message id)
    twin, mine = "c-twin", "c-mine"
    maker = Maker({("GET", LIST): listing((mine, NOW + 9), (twin, NOW + 2)),
                   ("GET", CONV + twin): Resp(json_body=conv(twin, "other-job-message")),
                   ("GET", CONV + mine): Resp(json_body=conv(mine, OURS))})
    result, ctx = go_reconcile(maker, gpt_job())
    assert result == Found(mine, {STATE_CONVERSATION: mine}, taken=True), "matched the twin prompt"
    # text decides only when the user message id cannot be read, and only the exact sent text
    maker = Maker({("GET", LIST): listing((twin, NOW + 2), (mine, NOW + 9)),
                   ("GET", CONV + twin): Resp(json_body=conv(twin, None, PROMPT + " ")),
                   ("GET", CONV + mine): Resp(json_body=conv(mine, None))})
    assert go_reconcile(maker, gpt_job())[0] == Found(mine, {STATE_CONVERSATION: mine}, taken=True)
    assert ctx.refs == [] and "mark_sent" not in ctx.log


def test_reconcile_by_known_conversation():
    maker = Maker({("GET", CONV + CID): Resp(json_body=T2I)})
    result, _ = go_reconcile(maker, gpt_job({STATE_MESSAGE: OURS, STATE_PROMPT: PROMPT, STATE_CONVERSATION: CID}))
    assert result == Found(CID, {STATE_CONVERSATION: CID}, taken=True)
    assert not any(r.path == LIST for r in maker.requests()), "a known conversation needs no listing"
    # the known conversation holds another message id: not ours, nothing more to find
    state = {STATE_MESSAGE: "someone-else", STATE_PROMPT: PROMPT, STATE_CONVERSATION: CID}
    assert go_reconcile(maker, gpt_job(state))[0] == StillUnknown(answered=True)
    # unreadable, then signed out (a doubt, with its strike)
    maker = Maker({("GET", CONV + CID): Resp(502, text="bad gateway")})
    assert go_reconcile(maker, gpt_job({STATE_MESSAGE: OURS, STATE_CONVERSATION: CID}))[0] == StillUnknown()
    maker = Maker({("GET", "/api/auth/session"): Resp(json_body={})})
    result, _ = go_reconcile(maker, gpt_job({STATE_MESSAGE: OURS, STATE_CONVERSATION: CID}))
    assert isinstance(result, StillUnknown) and result.answered is False
    assert any(isinstance(e, Suspect) for e in result.effects)


def test_reconcile_ignores_conversations_outside_the_window():
    early, late, iso = "c-early", "c-late", "c-iso"
    in_iso = datetime.fromtimestamp(NOW + 30, UTC).isoformat().replace("+00:00", "Z")
    maker = Maker({("GET", LIST): listing((early, NOW - FIND_BEFORE_S - 1), (late, NOW + FIND_AFTER_S + 1),
                                          (iso, in_iso), ("c-none", None)),
                   ("GET", CONV + early): Resp(json_body=conv(early, OURS)),
                   ("GET", CONV + late): Resp(json_body=conv(late, OURS)),
                   ("GET", CONV + iso): Resp(json_body=conv(iso, "x"))})
    result, _ = go_reconcile(maker, gpt_job())
    assert result == StillUnknown(answered=True)
    read = [r.path for r in maker.requests() if r.path.startswith(CONV)]
    assert read == [CONV + iso], "only conversations made in the send window are read"
    listed = next(r for r in maker.requests() if r.path == LIST)
    assert "offset=0&limit=28&order=updated" in listed.url
    # on the window's edges they are read
    maker.table[("GET", LIST)] = listing((early, NOW - FIND_BEFORE_S), (late, NOW + FIND_AFTER_S))
    assert go_reconcile(maker, gpt_job())[0] == Found(early, {STATE_CONVERSATION: early}, taken=True)


# ---- poll -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("body, at, want", [
    (T2I, 5, "done"),
    (conv(CID, OURS, image=False, words="I can't help with that."), 5, "refused"),
    (conv(CID, OURS, image=False, done=False), 599, "pending"),
    (conv(CID, OURS, image=False, done=False), 600, "lost"),
    (conv(CID, OURS, image=False, words="Working on it", done=False), 30, "pending"),
])
def test_poll_done_refused_lost_and_pending(body, at, want):
    maker = Maker({("GET", CONV + CID): Resp(json_body=body)})
    state = {STATE_MESSAGE: OURS, STATE_PROMPT: PROMPT, STATE_CONVERSATION: CID}
    result, ctx = go_poll(maker, gpt_job(state), at=SENT + timedelta(seconds=at))
    out = result.outcome
    if want == "done":
        assert out == Done((Output("sediment://" + FILE_ID, "image/png", 0, FILE_ID,
                                   {"conversation": CID, "width": 1254, "height": 1254}),))
        assert result.title == T2I["title"]
    elif want == "refused":
        assert isinstance(out, Failed) and out.code == REFUSED and out.taken is True
        assert "can't help" in out.detail
    elif want == "lost":
        assert isinstance(out, Failed) and out.code == IMAGE_LOST and out.taken is True
    else:
        assert out == Pending(out.note, settings().get("gpt.poll_interval_s"))
    assert [p for p in (r.method for r in maker.requests()) if p != "GET"] == []
    assert_no_secret(result, *ctx.events)


def test_poll_without_answer_and_a_deleted_conversation():
    state = {STATE_MESSAGE: OURS, STATE_CONVERSATION: CID}
    # signed out: polled again, a strike
    result, _ = go_poll(Maker({("GET", "/api/auth/session"): Resp(json_body={})}), gpt_job(state))
    assert isinstance(result.outcome, Pending) and result.outcome.answered is False
    assert any(isinstance(e, Suspect) for e in result.effects)
    # a failed or garbled read: polled again, not an answer
    for answer in (Resp(500, text="x"), Resp(text="not json"), ConnectionError("boom")):
        result, _ = go_poll(Maker({("GET", CONV + CID): answer}), gpt_job(state))
        assert isinstance(result.outcome, Pending) and result.outcome.answered is False
    # no conversation on the job: nothing to read
    assert go_poll(Maker({}), gpt_job({STATE_MESSAGE: OURS}))[0].outcome.answered is False
    # 404: the conversation is gone, it has no result
    result, _ = go_poll(Maker({("GET", CONV + CID): Resp(404, json_body=fixture("conversation_deleted_404")["body"])}),
                        gpt_job(state))
    assert isinstance(result.outcome, Failed) and result.outcome.code == IMAGE_LOST


# ---- fetch ------------------------------------------------------------------------------------------------------
@mutation(RESULTS_FILE, HOST_CHECK, NO_HOST_CHECK, expect="a link off chatgpt.com must end the fetch before a request")
def test_the_bearer_goes_only_to_chatgpt_estuary():
    # Review Focus 5: the relative and the absolute chatgpt.com forms are fetched with the bearer through the proxy
    for url in (ESTUARY + "?id=1&sig=s", "https://chatgpt.com" + ESTUARY + "?id=1&sig=s"):
        maker = Maker({("GET", CONV + CID + "/attachment/" + FILE_ID + "/download"): download(url),
                       ("GET", ESTUARY): Resp(200, {"content-type": "image/png"}, content=PNG)})
        parts, _ = go_fetch(maker, output())
        assert parts == [PNG]
        media = maker.sessions[-1].requests[-1]
        assert media.url == "https://chatgpt.com" + ESTUARY + "?id=1&sig=s"
        assert media.headers["Authorization"] == "Bearer " + ACCESS and media.cookies == {}
        assert set(map(str, maker.proxies)) == {str({"http": proxy_url(PROXY), "https": proxy_url(PROXY)})}
    # any other place: ended as missing, and no request carries the bearer there (nor anywhere after)
    for url in ("https://evil.com" + ESTUARY + "?id=1", "http://chatgpt.com" + ESTUARY,
                "https://chatgpt.com.evil.com" + ESTUARY, "https://chatgpt.com@evil.com" + ESTUARY,
                "https://chatgpt.com/backend-api/files/1", "//evil.com" + ESTUARY):
        maker = Maker({("GET", CONV + CID + "/attachment/" + FILE_ID + "/download"): download(url)})
        try:
            go_fetch(maker, output())
            code = None
        except OutputGone as e:
            code = e.code
        except Exception:      # noqa: BLE001 - any other end is a retry, which would try the link again
            code = None
        assert code == OUTPUT_MISSING, "a link off chatgpt.com must end the fetch before a request"
        assert len(maker.sessions) == 1, "a link off chatgpt.com must end the fetch before a request"
        assert all(r.path != ESTUARY and "evil" not in r.url for r in maker.requests())


def test_a_deleted_conversation_is_output_missing():
    gone = Resp(404, json_body=fixture("conversation_deleted_404")["body"])
    attach = ("GET", CONV + CID + "/attachment/" + FILE_ID + "/download")
    for table in ({attach: gone},
                  {attach: Resp(200, json_body=fixture("conversation_deleted_404")["body"])},
                  {attach: download(ESTUARY + "?id=1"), ("GET", ESTUARY): Resp(404, text="")}):
        with pytest.raises(OutputGone) as e:
            go_fetch(Maker(table), output())
        assert e.value.code == OUTPUT_MISSING
    # a result naming no conversation or no file cannot be fetched
    with pytest.raises(OutputGone):
        go_fetch(Maker({}), Output("sediment://x", "image/png", 0, None, {}))


def test_the_media_cap_and_content_type():
    attach = ("GET", CONV + CID + "/attachment/" + FILE_ID + "/download")
    for media in (Resp(200, {"content-type": "text/html"}, content=b"<html>"), Resp(403, text="no"),
                  Resp(200, {"content-type": "image/png"}, content=b"P" * (64 * 2**20 + 1))):
        with pytest.raises(Exception) as e:
            go_fetch(Maker({attach: download(ESTUARY + "?id=1"), ("GET", ESTUARY): media}), output())
        assert not isinstance(e.value, OutputGone), "a passing failure is retried by the core"
    with pytest.raises(RuntimeError):
        go_fetch(Maker({attach: Resp(500, text="x")}), output())


@mutation(RESULTS_FILE, NO_PATCH, PATCH_CALL, expect="a conversation was hidden")
def test_nothing_hides_or_deletes_a_conversation():
    state = {STATE_MESSAGE: OURS, STATE_PROMPT: PROMPT, STATE_CONVERSATION: CID}
    maker = Maker({("GET", CONV + CID): Resp(json_body=T2I), ("GET", LIST): listing((CID, NOW + 5)),
                   ("GET", CONV + CID + "/attachment/" + FILE_ID + "/download"): download(ESTUARY + "?id=1"),
                   ("GET", ESTUARY): Resp(200, {"content-type": "image/png"}, content=PNG)})
    go_poll(maker, gpt_job(state))
    go_reconcile(maker, gpt_job(state))
    go_reconcile(maker, gpt_job())
    go_fetch(maker, output())
    methods = {m for m, _, _ in maker.log}
    assert methods == {"GET"}, "a conversation was hidden"


def test_planted_secrets_never_leave(caplog):
    state = {STATE_MESSAGE: OURS, STATE_PROMPT: PROMPT, STATE_CONVERSATION: CID}
    leak = ConnectionError("CONNECT via http://%s@10.0.0.9:3128 failed" % PROXY.auth)
    texts = []
    for table in ({("GET", CONV + CID): Resp(json_body=T2I)}, {("GET", CONV + CID): leak},
                  {("GET", "/api/auth/session"): leak}):
        result, ctx = go_poll(Maker(table), gpt_job(state))
        texts += [result, ctx.events, ctx.refs]
        result, ctx = go_reconcile(Maker(table), gpt_job(state))
        texts += [result, ctx.events, ctx.refs]
    attach = ("GET", CONV + CID + "/attachment/" + FILE_ID + "/download")
    for table in ({attach: leak}, {attach: download(ESTUARY + "?id=1"), ("GET", ESTUARY): leak}):
        with pytest.raises(Exception) as e:
            go_fetch(Maker(table), output())
        texts += [repr(e.value), str(e.value)]
    assert_no_secret(*texts, caplog.text)
