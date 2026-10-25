"""The video path and the account index over a fake gemini.google.com (no network), fed the AI Pro account's recorded
answers (fixtures/gemini/live-261024, /u/4, 24/10). Each money or verdict rule has a test and a mutation."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from geminikit import (
    I2V_ACCEPTED,
    I2V_DONE,
    I2V_ERROR,
    I2V_PENDING,
    I2V_PROMPT,
    MODE_FLASH_LITE,
    NOT_ENTITLED,
    PRO_USAGE,
    T2V_ACCEPTED,
    T2V_DONE,
    T2V_PENDING,
    T2V_PROMPT,
    UPLOADED,
    VIDEO_LIMIT,
    Ctx,
    Gemini,
    answer,
    blob_for,
    frames,
    inner_of,
    job,
    settings,
)

from relay_core.net import FetchFailed
from relay_core.providers import (
    Accepted,
    Ambiguous,
    Balance,
    Completed,
    Cooldown,
    Done,
    Fail,
    Failed,
    Output,
    Pending,
    Retry,
    Success,
)
from relay_core.testing.fake_media import Answer as MediaAnswer
from relay_core.testing.fake_media import MediaSite, public_resolver
from relay_core.testing.mutation import mutation
from relay_gemini import errors
from relay_gemini.engine import results as R
from relay_gemini.engine.http import Sessions
from relay_gemini.engine.reply import Turn, read_answer, turns
from relay_gemini.engine.submit import judge, submit

SUBMIT_FILE = "packages/provider-gemini/src/relay_gemini/engine/submit.py"
RESULTS_FILE = "packages/provider-gemini/src/relay_gemini/engine/results.py"
REPLY_FILE = "packages/provider-gemini/src/relay_gemini/engine/reply.py"
HTTP_FILE = "packages/provider-gemini/src/relay_gemini/engine/http.py"
NET_FILE = "packages/relay-core/src/relay_core/net.py"
NOW = datetime(2026, 10, 24, 1, 8, 0, tzinfo=UTC)
WALL = 1_792_804_000.0
VIDEO = "gemini-omni-video"
PRO = {"authuser": 4, "mode": MODE_FLASH_LITE}      # what the check keeps for the AI Pro account
EMAIL = "owner.pro@example.com"
T2V_CHAT = "c_000000000000a000"                     # the redacted ids of the recorded chats
I2V_CHAT = "c_000000000000a002"


async def media(ref) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def video_job(prompt=T2V_PROMPT, **kw):
    return job(prompt, model=VIDEO, **kw)


def pro_ctx(log, **kw):
    return Ctx(log, blob=blob_for(EMAIL), state=dict(PRO), **kw)


def pro_site(log, **kw):
    return Gemini(log, accounts=["a@example.com", "b@example.com", "c@example.com", "d@example.com", EMAIL], **kw)


def run(g, ctx, j):
    return asyncio.run(submit(ctx, j, settings(), Sessions(), transport=g.transport, read_media=media,
                              now=lambda: NOW, wall=lambda: WALL))


def cid_of(text: str) -> str:
    return read_answer(text).cid


# ---- the send -----------------------------------------------------------------------------------------------------
TAKEN_IF = "    if video and answer.generating:"
TAKEN = TAKEN_IF + "     # taken, whatever other text comes with it: never a refusal while it renders\n"


@mutation(SUBMIT_FILE, TAKEN, "", expect="a taken video")
@mutation(REPLY_FILE, "    return any(n == GENERATING_MARK for n in _walk(node))", "    return False",
          expect="a taken video")
def test_a_text_to_video_is_on_record_and_marked_sent_before_its_one_send_and_a_generating_answer_is_accepted():
    log = []
    g, ctx = pro_site(log, send=T2V_ACCEPTED), pro_ctx(log)
    result = run(g, ctx, video_job())
    assert log.index("report_ref") < log.index("mark_sent") == log.index("SEND") - 1
    assert log.count("SEND") == 1
    chat = cid_of(T2V_ACCEPTED)
    rid = ctx.refs[0][0]
    assert result.job == Accepted(chat, {"request": rid, "chat": chat}), "a taken video became %s" % (result.job,)
    assert Success() in result.account and ctx.refs[-1] == (chat, {"request": rid, "chat": chat})


@mutation(SUBMIT_FILE, "49: VIDEO_TOOL if video else IMAGE_TOOL,", "49: IMAGE_TOOL,", expect="the video tool")
@mutation(HTTP_FILE, '        return BASE_URL + ("/u/%d" % self.authuser if self.authuser else "") + path',
          "        return BASE_URL + path", expect="went to another account")
def test_the_video_send_is_the_web_apps_request_on_the_accounts_index_with_its_mode():
    log = []
    g = pro_site(log, send=T2V_ACCEPTED)
    result = run(g, pro_ctx(log), video_job())
    assert g.sends(), "nothing sent: the page went to another account (%s)" % (result.job,)
    send = g.sends()[0]
    gemini_calls = [r.url.path for r in g.seen if r.url.host == "gemini.google.com"]
    assert all(p.startswith("/u/4/") for p in gemini_calls), "a call went to another account: %s" % gemini_calls
    inner = inner_of(send)
    assert inner[49] == 11, "the video tool was not chosen: %s" % inner[49]
    assert inner[55] == [[16]] and inner[0][0] == T2V_PROMPT and inner[0][3] is None
    assert inner[0][9][6] == [None, [None, "16:9"]]
    assert json.loads(send.headers["x-goog-ext-525001261-jspb"])[4] == MODE_FLASH_LITE


I2V_ENTRY = ("        content[3] = [[entry, name]] if video else "
             "[[entry, name, None, None, None, None, None, None, [0]]]")
UPLOAD_AU = '        url = UPLOAD_URL + ("?authuser=%d" % self.authuser if self.authuser else "")'


@mutation(SUBMIT_FILE, I2V_ENTRY, "        content[3] = [[entry, name, None, None, None, None, None, None, [0]]]",
          expect="the video tool's file entry")
@mutation(SUBMIT_FILE, "                                 [[None, None, None, 1]] if image is not None else",
          "                                 ", expect="the image to video marker")
@mutation(HTTP_FILE, UPLOAD_AU, "        url = UPLOAD_URL", expect="the upload did not name the account")
@mutation(SUBMIT_FILE, '            if job.operation in ("image_to_image", "image_to_video"):',
          '            if job.operation == "image_to_image":', expect="sent without its image")
def test_an_image_to_video_uploads_for_the_account_then_names_the_file_as_the_video_tool_does():
    log = []
    g = pro_site(log, send=I2V_ACCEPTED)
    result = run(g, pro_ctx(log), video_job(I2V_PROMPT, images=1))
    uploads = [r for r in g.seen if r.url.host == "push.clients6.google.com"]
    assert uploads, "sent without its image"
    assert uploads[0].url.params.get("authuser") == "4", "the upload did not name the account: %s" % uploads[0].url
    assert log.index("upload:upload, finalize") < log.index("mark_sent")
    content = inner_of(g.sends()[0])[0]
    assert len(content[3][0]) == 2 and content[3][0][0][0] == UPLOADED and content[3][0][1] == "input.png", \
        "not the video tool's file entry: %s" % content[3]
    assert content[9][6] == [[None, None, None, 1]], "the image to video marker is missing: %s" % content[9]
    assert inner_of(g.sends()[0])[55] == [[16]]
    assert isinstance(result.job, Accepted) and result.job.ref == cid_of(I2V_ACCEPTED)


def test_the_error_answer_of_a_refused_file_is_unknown_and_never_sent_again():
    """Measured twice (24/10): BardErrorInfo 1100 in 1.3 s, no chat, the quota not moved. Not proof that nothing was
    taken, so it is reconciled (and refunded at the deadline), never sent again."""
    log = []
    result = run(pro_site(log, send=I2V_ERROR), pro_ctx(log), video_job(I2V_PROMPT, images=1))
    assert isinstance(result.job, Ambiguous) and log.count("SEND") == 1


NO_CHAT = "        if not answer.cid:\n"


@mutation(SUBMIT_FILE, NO_CHAT, "        if False:\n", expect="no chat")
def test_a_generating_answer_that_names_no_chat_is_unknown():
    text = T2V_ACCEPTED.replace(cid_of(T2V_ACCEPTED), "x_none")
    log = []
    result = run(pro_site(log, send=text), pro_ctx(log), video_job())
    assert isinstance(result.job, Ambiguous), "a generating answer with no chat became %s" % (result.job,)


LIMIT = "    if video and is_video_limit(final):\n"
LIMIT_REST = ("        return (spent,) if spent is not None and spent.refill_at is not None else "
              "(limit_balance(settings, now),)")


@mutation(SUBMIT_FILE, LIMIT, "    if False:\n", expect="the video limit")
@mutation(SUBMIT_FILE, "    if NOT_ENTITLED_LINK in final:\n", "    if False:\n", expect="the upgrade answer")
@mutation(SUBMIT_FILE, LIMIT_REST, "        return ()", expect="the video pool did not rest")
def test_the_video_limit_answer_fails_with_its_refund_and_rests_the_accounts_video_pool():
    text = "I can't create more videos for you today, but I can still find videos from the web."
    log = []
    result = run(pro_site(log, send=answer(text)), pro_ctx(log), video_job())
    assert isinstance(result.job, Fail) and result.job.code == errors.VIDEO_LIMIT, \
        "the video limit answer became %s" % (result.job,)
    assert errors.CODES[errors.VIDEO_LIMIT].refund and errors.CODES[errors.VIDEO_LIMIT].terminal
    assert Balance("video", 0, NOW + timedelta(seconds=5 * 3600)) in result.account, \
        "the video pool did not rest: %s" % (result.account,)
    # the same words on an image job are an ordinary refusal; the free plan's answer stays not_entitled
    log = []
    assert run(pro_site(log, send=answer(text)), pro_ctx(log), job()).job.code == errors.REFUSED
    log = []
    upgrade = run(pro_site(log, send=NOT_ENTITLED), pro_ctx(log), video_job()).job
    assert isinstance(upgrade, Fail) and upgrade.code == errors.NOT_ENTITLED, \
        "the upgrade answer became %s" % (upgrade,)


# ---- before the send: the right Google account -------------------------------------------------------------------
GUARD = '            if mode is None or (site.email and (page.email or "").lower() != site.email):'


@mutation(SUBMIT_FILE, GUARD, "            if mode is None:", expect="sent to another account")
@mutation(SUBMIT_FILE, GUARD, '            if site.email and (page.email or "").lower() != site.email:',
          expect="sent with no mode")
def test_a_page_of_another_account_or_an_unchecked_account_is_a_retry_with_nothing_sent():
    log = []
    moved = Gemini(log, accounts=["a@example.com"] * 4 + ["someone.else@example.com"], send=T2V_ACCEPTED)
    result = run(moved, pro_ctx(log), video_job())
    assert log.count("SEND") == 0 and "mark_sent" not in log, "sent to another account: %s" % log
    assert result.job == Retry(errors.UNRESOLVED)
    (rest,) = result.account
    assert isinstance(rest, Cooldown) and rest.recheck
    log = []
    try:
        got = run(pro_site(log, send=T2V_ACCEPTED), Ctx(log, blob=blob_for(EMAIL), state={"authuser": 4}),
                  video_job()).job
    except TypeError as e:          # no mode to send: the request cannot be built
        got = e
    assert log.count("SEND") == 0 and got == Retry(errors.UNRESOLVED), "sent with no mode: %s %r" % (log, got)


# ---- poll, reconcile and fetch -----------------------------------------------------------------------------------
def poll(g, j):
    return asyncio.run(R.poll(pro_ctx(g.log), j, settings(), Sessions(), transport=g.transport, rand=lambda: 0.0,
                              now=lambda: NOW))


VIDEO_MEDIA = "    media = answer.videos if video else answer.images\n"


@mutation(SUBMIT_FILE, VIDEO_MEDIA, "    media = answer.images\n", expect="the video was not read")
@mutation(REPLY_FILE, "                        videos_in(row[3]), generating(row[3])))",
          "                        (), generating(row[3])))", expect="the video was not read")
@mutation(RESULTS_FILE, "    verdict, what = judge(200, turn.answer(chat), video=is_video(job))",
          "    verdict, what = judge(200, turn.answer(chat))", expect="the video was not read")
def test_poll_reads_the_chat_until_the_video_is_there():
    for pending, done, chat, size in ((T2V_PENDING, T2V_DONE, T2V_CHAT, 3748734), (I2V_PENDING, I2V_DONE, I2V_CHAT,
                                                                                  2132282)):
        state = {"chat": chat}
        waiting = poll(pro_site([], rpc={"hNvQHb": pending}), video_job(state=state))
        assert isinstance(waiting.outcome, Pending), "a chat still generating became %s" % (waiting.outcome,)
        got = poll(pro_site([], rpc={"hNvQHb": done}), video_job(state=state))
        assert isinstance(got.outcome, Done), "the video was not read: %s" % (got.outcome,)
        (out,) = got.outcome.outputs
        assert out.mime == "video/mp4" and out.ref.startswith("https://lh3.googleusercontent.com/gg/")
        assert out.meta == {"chat": chat, "width": 1280, "height": 720, "duration_s": 10.005}, out.meta
        assert turns(done)[0][0].videos[0].size == size
    # the i2v chat's input image (in what was sent) is never taken for a result
    assert turns(I2V_PENDING)[0][0].images == () and turns(I2V_PENDING)[0][0].videos == ()


POLL_LIMIT = "                          effects=limit_effects(settings, now(), code, text, is_video(job), spent))"


@mutation(RESULTS_FILE, POLL_LIMIT, "                          effects=())", expect="the video pool did not rest")
def test_a_chat_that_answered_the_video_limit_fails_refunded_and_rests_the_video_pool():
    got = poll(pro_site([], rpc={"hNvQHb": VIDEO_LIMIT}), video_job(state={"chat": "c_000000000000a000"}))
    assert isinstance(got.outcome, Failed) and got.outcome.code == errors.VIDEO_LIMIT, got.outcome
    assert [e.pool for e in got.effects if isinstance(e, Balance) and e.left_n == 0] == ["video"], \
        "the video pool did not rest"


def test_a_lost_video_send_is_found_by_its_exact_text_like_an_image():
    from geminikit import chat_read, listing
    start = video_job().sent_at.timestamp()
    g = pro_site([], rpc={"MaZiqc": listing(("c_00000000000000b2", start + 1)),
                        "hNvQHb": chat_read(T2V_PROMPT, "", image=False, cid="c_00000000000000b2")})
    found = asyncio.run(R.reconcile(pro_ctx(g.log), video_job(), settings(), Sessions(), transport=g.transport))
    assert found.ref == "c_00000000000000b2" and "SEND" not in g.log


FIRST_LINK = '        current = re.sub(r"=[A-Za-z0-9-]+$", "", link) + size + "?alr=yes"'
SIZE = "        link = await site.resolve(out.ref, VIDEO_SIZE if video else FULL_SIZE)"
LINK_AU = ('            current = text + ("&" if "?" in text else "?") + au[1:] \\\n'
           '                if au and urlsplit(text).hostname in SESSION_LINK_HOSTS else text')
CDN_AU = "                if au and urlsplit(text).hostname in SESSION_LINK_HOSTS else text"


@mutation(RESULTS_FILE, SIZE, "        link = await site.resolve(out.ref)", expect="the video's MP4")
@mutation(HTTP_FILE, LINK_AU, "            current = text", expect="did not name the account")
@mutation(HTTP_FILE, CDN_AU, "                if au and urlsplit(text).hostname != BYTES_HOST else text",
          expect="the CDN request carried")
@mutation(HTTP_FILE, FIRST_LINK + " + au", FIRST_LINK, expect="did not name the account")
def test_a_video_is_fetched_as_its_720p_mp4_from_googlevideo_naming_the_account_on_every_session_hop():
    log = []
    g = pro_site(log, chain={
        "lh3.googleusercontent.com": (200, "text/plain", "https://lh3.google.com/rd/abc?x=1"),
        "lh3.google.com": (200, "text/plain", "https://rr1---sn-abc.googlevideo.com/videoplayback?id=1")})
    mp4 = b"\0\0\0\x18ftypmp42" + b"\0" * 32
    site = MediaSite(mp4)
    link = "https://lh3.googleusercontent.com/gg/VIDEO1"

    async def go():
        return b"".join([p async for p in R.fetch(pro_ctx(log), Output(link, "video/mp4", 0), settings(), Sessions(),
                                                  transport=g.transport, media_transport=site,
                                                  media_resolver=public_resolver)])
    assert asyncio.run(go()) == mp4
    first = next(r for r in g.seen if r.url.host == "lh3.googleusercontent.com")
    assert str(first.url) == link + "=m22?alr=yes&authuser=4", \
        "not the video's MP4 link, or it did not name the account: %s" % first.url
    hop = next(r for r in g.seen if r.url.host == "lh3.google.com")
    assert hop.url.params.get("authuser") == "4" and hop.url.params.get("x") == "1", \
        "the lh3.google.com hop did not name the account: %s" % hop.url
    cdn = [r for r in g.seen if r.url.host.endswith("googlevideo.com")] + [r for _, r in site.requests]
    assert len(cdn) == 2, "the bytes were not downloaded: %s" % cdn
    for r in cdn:
        assert "authuser" not in r.url.params and "cookie" not in r.headers, \
            "the CDN request carried the account or a cookie: %s" % r.url


GOOGLEVIDEO = json.loads((Path(__file__).resolve().parents[3] / "fixtures" / "gemini" / "live-261025"
                          / "googlevideo_user_agent.json").read_text(encoding="utf-8"))
UA_ARG = "            user_agent=USER_AGENT)) as parts:"
UA_PAD = "                                                        "
UA_HEADER = UA_PAD + '**({"User-Agent": user_agent} if user_agent else {})})'


@mutation(RESULTS_FILE, UA_ARG, "            )) as parts:", expect="googlevideo refused the download")
@mutation(NET_FILE, UA_HEADER, UA_PAD + "})",
          expect="googlevideo refused the download")
def test_a_video_is_downloaded_with_the_user_agent_its_googlevideo_link_was_signed_for():
    """25/10 on production: every video failed `render.download_failed`. The googlevideo link carries `eaua` (the
    resolving client's User-Agent) and `rqh=1`; the same link answered the resolving client 200 video/mp4 and any
    other User-Agent (httpx's, curl's, a bare Mozilla/5.0) 403 text/plain, through the same proxy (live-261025)."""
    assert GOOGLEVIDEO["link"]["eaua_present"] and GOOGLEVIDEO["link"]["rqh"] == ["1"]
    refused = next(t for t in GOOGLEVIDEO["tries"] if t["status"] == 403)
    served = next(t for t in GOOGLEVIDEO["tries"] if t["status"] == 200)
    log = []
    g = pro_site(log, chain={
        "lh3.googleusercontent.com": (200, "text/plain", "https://lh3.google.com/rd/abc"),
        "lh3.google.com": (200, "text/plain", "https://rr1---sn-abc.googlevideo.com/videoplayback?eaua=x&rqh=1")})
    mp4 = b"\0\0\0\x18ftypmp42" + b"\0" * 32

    def googlevideo(request):
        signed_for = next(r for r in g.seen if r.url.host.endswith("googlevideo.com")).headers["user-agent"]
        if request.headers.get("user-agent") != signed_for:
            return MediaAnswer(403, headers={k: v for k, v in refused["headers"].items() if k != "content-length"})
        return MediaAnswer(body=mp4, headers={"content-type": served["headers"]["content-type"]})
    site = MediaSite(googlevideo, googlevideo)

    async def go():
        return b"".join([p async for p in R.fetch(pro_ctx(log), Output("https://lh3.googleusercontent.com/gg/V",
                                                                        "video/mp4", 0), settings(), Sessions(),
                                                  transport=g.transport, media_transport=site,
                                                  media_resolver=public_resolver)])
    try:
        got = asyncio.run(go())
    except FetchFailed as e:
        raise AssertionError("googlevideo refused the download: %s" % e.reason) from None
    assert got == mp4


def test_a_video_link_that_leaves_the_google_hosts_is_never_followed():
    log = []
    evil = "https://googlevideo.com.evil.example/x"
    g = pro_site(log, chain={"lh3.googleusercontent.com": (200, "text/plain", evil)})

    async def go():
        return [p async for p in R.fetch(pro_ctx(log), Output("https://lh3.googleusercontent.com/gg/V", "video/mp4", 0),
                                         settings(), Sessions(), transport=g.transport,
                                         media_transport=MediaSite(b""), media_resolver=public_resolver)]
    try:
        asyncio.run(go())
    except RuntimeError as e:
        assert "did not resolve" in str(e)
    else:
        raise AssertionError("a link off the Google hosts resolved")
    assert not any("evil" in str(r.url) for r in g.seen)


def test_the_recorded_video_answers_read_as_measured():
    a = read_answer(T2V_ACCEPTED)
    assert (a.clean, a.ended, a.generating, a.videos, a.texts) == (True, True, True, (), ())
    assert read_answer(I2V_ACCEPTED).generating and read_answer(I2V_ERROR).errors
    (vid,) = turns(T2V_DONE)[0][0].videos
    assert (vid.width, vid.height, vid.duration_s, vid.mime) == (1280, 720, 10.005, "video/mp4")
    limit = turns(VIDEO_LIMIT)[0][0]
    assert not limit.generating and "create more videos" in limit.texts[-1]


# ---- every account-scoped read checks the Google account first ---------------------------------------------------
VERIFY = ('        if self.email and (page.email or "").lower() != self.email:\n'
          '            if await self.find_account(self.email) is None:\n'
          '                return None\n'
          '            page = self.page\n'
          '        return page\n')
VERIFY_NONE = ("            if await self.find_account(self.email) is None:\n                return None\n"
               "            page")
MOVED = ["a@example.com", "b@example.com", EMAIL, "c@example.com", "someone.else@example.com"]   # /u/4 is not ours
GONE = ["a@example.com", "b@example.com", "c@example.com", "d@example.com", "e@example.com"]   # none is ours


def chat_reads(g):
    return [r.url.path for r in g.seen if "hNvQHb" in str(r.url) or "MaZiqc" in str(r.url)]


@mutation(HTTP_FILE, VERIFY, "        return page\n", expect="read another account")
@mutation(HTTP_FILE, VERIFY_NONE, "            await self.find_account(self.email)\n            page",
          expect="read with no verified account")
@mutation(RESULTS_FILE, "            if await site.open_account() is None:\n                return PollResult(",
          "            if await site.open_account() is None and False:\n                return PollResult(",
          expect="read with no verified account")
def test_poll_reads_the_chat_only_on_the_verified_account_re_resolving_a_moved_index():
    g = Gemini([], accounts=MOVED, rpc={"hNvQHb": T2V_DONE})
    got = poll(g, video_job(state={"chat": T2V_CHAT}))
    assert chat_reads(g) and all(p.startswith("/u/2/") for p in chat_reads(g)), \
        "the poll read another account: %s" % chat_reads(g)
    assert isinstance(got.outcome, Done)
    g = Gemini([], accounts=GONE, rpc={"hNvQHb": T2V_DONE})
    got = poll(g, video_job(state={"chat": T2V_CHAT}))
    assert chat_reads(g) == [], "the poll read with no verified account: %s" % chat_reads(g)
    assert isinstance(got.outcome, Pending) and got.outcome.answered is False


@mutation(RESULTS_FILE, "            if await site.open_account() is None:      # never a chat",
          "            if await site.open_account() is None and False:      # never a chat",
          expect="reconcile read with no verified account")
def test_reconcile_never_matches_a_prompt_in_another_account():
    from geminikit import chat_read, listing
    start = video_job().sent_at.timestamp()
    rpc = {"MaZiqc": listing(("c_00000000000000b2", start + 1)),
           "hNvQHb": chat_read(T2V_PROMPT, "", image=False, cid="c_00000000000000b2")}
    g = Gemini([], accounts=GONE, rpc=rpc)       # the same prompt sits in an account that is not ours
    found = asyncio.run(R.reconcile(pro_ctx(g.log), video_job(), settings(), Sessions(), transport=g.transport))
    assert found == R.StillUnknown() and chat_reads(g) == [], \
        "reconcile read with no verified account: %s %s" % (found, chat_reads(g))
    g = Gemini([], accounts=MOVED, rpc=rpc)
    found = asyncio.run(R.reconcile(pro_ctx(g.log), video_job(), settings(), Sessions(), transport=g.transport))
    assert found.ref == "c_00000000000000b2" and all(p.startswith("/u/2/") for p in chat_reads(g))


@mutation(RESULTS_FILE, "        if await site.open_account() is None:\n            raise RuntimeError(",
          "        if await site.open_account() is None and False:\n            raise RuntimeError(",
          expect="fetched with no verified account")
def test_fetch_resolves_a_link_only_on_the_verified_account():
    log = []
    g = Gemini(log, accounts=GONE)
    out = Output("https://lh3.googleusercontent.com/gg/V", "video/mp4", 0)

    async def go():
        return [p async for p in R.fetch(pro_ctx(log), out, settings(), Sessions(), transport=g.transport,
                                         media_transport=MediaSite(b""), media_resolver=public_resolver)]
    raised = None
    try:
        asyncio.run(go())
    except Exception as e:      # noqa: BLE001 - which exception is asserted below
        raised = e
    assert all(r.url.host in ("gemini.google.com", "accounts.google.com") for r in g.seen), \
        "fetched with no verified account: %s" % [str(r.url.host) for r in g.seen]
    assert isinstance(raised, RuntimeError) and "could not be verified" in str(raised), \
        "fetched with no verified account: %r" % (raised,)


# ---- the generating item wins over any text but the proven refusals ----------------------------------------------
def generating_answer(text: str, cid: str = "c_00000000000000f1") -> str:
    """A send's answer that carries the "generating your video" item, the chat, and a candidate with `text`."""
    item = {"7": ["I'm generating your video.", ["video_generation", [None, None, "Video Generation"], 1]]}
    cand = ["rc_00000000000000f3", [text], None, None, None, None, None, None, [1], "en"]
    return frames([None, [cid, "r_00000000000000f2"], item], [None, [cid, "r_00000000000000f2"], None, None, [cand]])


@mutation(SUBMIT_FILE, TAKEN, "", expect="a generating answer with text")
@mutation(SUBMIT_FILE, LIMIT, "    if video and is_video_limit(final) and not answer.generating:\n",
          expect="the limit beside the generating item")
def test_a_generating_answer_with_any_other_text_is_accepted_never_refunded_and_the_proven_refusals_still_win():
    for text in ("Sure! Here is a calm video of a paper boat.", "Something unexpected happened, I'll keep going."):
        log = []
        result = run(pro_site(log, send=generating_answer(text)), pro_ctx(log), video_job())
        assert isinstance(result.job, Accepted) and result.job.ref == "c_00000000000000f1", \
            "a generating answer with text became %s" % (result.job,)
    log = []
    result = run(pro_site(log, send=generating_answer("I can't create more videos for you today.")), pro_ctx(log),
                 video_job())
    assert isinstance(result.job, Fail) and result.job.code == errors.VIDEO_LIMIT, \
        "the limit beside the generating item became %s" % (result.job,)
    # a chat read with text beside the item stays pending
    turn = turns(T2V_PENDING)[0][0]
    pending = Turn(turn.prompt, ("Working on it",), (), (), (), True)
    assert judge(200, pending.answer(T2V_CHAT), video=True) == ("taken", None), "a generating answer with text"


# ---- the limit answer, worded otherwise --------------------------------------------------------------------------
LOWER = '    t = text.lower().replace("\\u2019", "\'")'
CANNOT = '    t = t.replace("cannot", "can\'t").replace("can not", "can\'t")'
PUNCT = "    return \" \".join(re.sub(r\"[^\\w']+\", \" \", t).split())"
QUOTA = ("    return code == errors.VIDEO_LIMIT or (code == errors.REFUSED and any(w in plain(text) "
         "for w in QUOTA_WORDS))")


@mutation(SUBMIT_FILE, LOWER, '    t = text.replace("\\u2019", "\'")', expect="a variant of the limit")
@mutation(SUBMIT_FILE, CANNOT, "    t = t", expect="a variant of the limit")
@mutation(SUBMIT_FILE, PUNCT, "    return t", expect="a variant of the limit")
@mutation(SUBMIT_FILE, QUOTA, "    return code == errors.VIDEO_LIMIT", expect="a quota-like answer")
def test_the_limit_answer_is_known_in_its_variants_and_a_quota_like_refusal_in_any_language_rests_the_video_pool():
    for text in ("I CAN'T CREATE MORE VIDEOS FOR YOU TODAY.", "I can’t create more videos for you today",
                 "Sorry, I cannot create more videos right now.", "You've reached your daily video limit!",
                 "I can't   create\nmore videos, sorry."):
        log = []
        result = run(pro_site(log, send=answer(text)), pro_ctx(log), video_job())
        assert result.job.code == errors.VIDEO_LIMIT, "a variant of the limit became %s: %r" % (result.job, text)
    for text in ("Hôm nay tôi không thể tạo thêm video cho bạn.", "Has alcanzado el límite de videos.",
                 "I can't make any more of these until tomorrow."):
        log = []
        result = run(pro_site(log, send=answer(text)), pro_ctx(log), video_job())
        assert isinstance(result.job, Fail) and result.job.code == errors.REFUSED
        assert [e.pool for e in result.account if isinstance(e, Balance) and e.left_n == 0] \
            == ["video"], "a quota-like answer did not empty the video pool: %r" % text
    log = []
    result = run(pro_site(log, send=answer("I can't make videos of real people.")), pro_ctx(log), video_job())
    assert result.job.code == errors.REFUSED
    assert not any(isinstance(e, Balance) for e in result.account), "a content refusal emptied the video pool"


def test_the_limit_resets_answer_is_the_limit_and_a_hard_time_answer_with_no_task_is_a_plain_refusal():
    """"I can create more videos as soon as your limit resets" is the quota answer (zexadev/gemini-web2api-go, 24/10):
    refunded, the video pool rested. "I'm having a hard time fulfilling your request" with no video task is a content
    refusal (rephrasing helps): refunded, nothing rested; with the generating item it is a taken video."""
    log = []
    result = run(pro_site(log, send=answer("I can create more videos as soon as your limit resets.")), pro_ctx(log),
                 video_job())
    assert isinstance(result.job, Fail) and result.job.code == errors.VIDEO_LIMIT, \
        "the limit resets answer became %s" % (result.job,)
    assert [e.pool for e in result.account if isinstance(e, Balance) and e.left_n == 0] == ["video"]
    hard = "I'm having a hard time fulfilling your request. Can I help you with something else instead?"
    log = []
    result = run(pro_site(log, send=answer(hard)), pro_ctx(log), video_job())
    assert isinstance(result.job, Fail) and result.job.code == errors.REFUSED, result.job
    assert not any(isinstance(e, Balance) for e in result.account), "a content refusal emptied the video pool"
    taken = read_answer(answer(hard))
    taken = type(taken)(taken.clean, taken.ended, taken.payloads, taken.cid, taken.texts, taken.images, taken.errors,
                        taken.videos, True)
    assert judge(200, taken, video=True) == ("taken", None)


# ---- the pro-tier image model (Nano Banana 2.1): the account's "Pro" mode and its kind ---------------------------
NB21 = "gemini-nano-banana-2.1"
PRO_MODE = ["e6fa609c3fa255c0", 3]                   # what the check keeps from the AI Pro account's mode list


def nb21_ctx(log, state=None):
    return Ctx(log, blob=blob_for(EMAIL), state={**PRO, "pro_mode": PRO_MODE} if state is None else state)


@mutation(SUBMIT_FILE, "None, 1, None, None, kind, 1, rid]),", "None, 1, None, None, 6, 1, rid]),",
          expect="the mode's kind")
@mutation(SUBMIT_FILE, "    if model == C.PRO_IMAGE_MODEL:\n", "    if False:\n", expect="the Pro mode")
@mutation(SUBMIT_FILE, "request(sent_text(job), ratio, image, rid, mode[0], video=video, kind=mode[1])",
          "request(sent_text(job), ratio, image, rid, mode[0], video=video)",
          expect="the mode's kind")
def test_the_pro_tier_image_is_sent_with_the_accounts_pro_mode_and_its_kind_and_nano_banana_2_with_the_default():
    """25/10 live: the kind, not the mode id alone, picks the image model ("Pro" id with kind 6: "Nano Banana 2 Lite",
    with kind 3: "Nano Banana 2.1")."""
    log = []
    g = pro_site(log, send=answer("", image=True))
    result = run(g, nb21_ctx(log), job(model=NB21, ratio="9:16"))
    assert g.sends(), "nothing sent: %s" % (result.job,)
    head = json.loads(g.sends()[0].headers["x-goog-ext-525001261-jspb"])
    assert head[4] == PRO_MODE[0], "not sent with the Pro mode: %s" % head[4]
    assert head[14] == 3, "the mode's kind was not sent: %s" % head[14]
    inner = inner_of(g.sends()[0])
    assert inner[49] == 14 and inner[55] == [[59]] and inner[0][9][6] == [None, [None, "9:16"]]
    assert isinstance(result.job, Completed)
    log = []
    g = pro_site(log, send=answer("", image=True))
    run(g, nb21_ctx(log), job())
    head = json.loads(g.sends()[0].headers["x-goog-ext-525001261-jspb"])
    assert (head[4], head[14]) == (MODE_FLASH_LITE, 6), "Nano Banana 2 was not sent with the default mode: %s" % head


@mutation(SUBMIT_FILE, '        pro = state.get("pro_mode")\n', '        pro = [state.get("mode"), 6]\n',
          expect="sent without the Pro mode")
def test_a_pro_tier_image_on_an_account_with_no_pro_mode_is_a_retry_with_nothing_sent():
    for state in (dict(PRO), {**PRO, "pro_mode": None}, {**PRO, "pro_mode": "e6fa609c3fa255c0"},
                  {**PRO, "pro_mode": ["e6fa609c3fa255c0", "3"]}):
        log = []
        result = run(pro_site(log, send=answer("", image=True)), nb21_ctx(log, state), job(model=NB21))
        assert log.count("SEND") == 0 and "mark_sent" not in log, "sent without the Pro mode: %s" % (state,)
        assert result.job == Retry(errors.UNRESOLVED)



# ---- limits become a zero balance until the reset ----------------------------------------------------------------
RESET = datetime.fromtimestamp(1792814865, UTC)              # the 5-hour window's reset in PRO_USAGE


def usage(left: int) -> str:
    """PRO_USAGE with `left` units in its 5-hour window (window code 1)."""
    return PRO_USAGE.replace("[2400,0,1,", "[%d,0,1," % left)


USAGE_SPENT = "            spent = await usage_spent(site, pool) if pool else None\n"
IMAGE_READ = '    return pool if code == errors.REFUSED else None'


@mutation(SUBMIT_FILE, USAGE_SPENT, "            spent = None\n", expect="the usage read's reset")
@mutation(SUBMIT_FILE, IMAGE_READ, "    return None", expect="a used-up window")
def test_a_limit_empties_the_pool_until_the_reset_the_usage_read_names_and_an_image_only_on_a_used_up_window():
    # a video limit on an account whose 5-hour window is used up: the read's reset
    log = []
    text = "I can't create more videos for you today, but I can still find videos from the web."
    result = run(pro_site(log, send=answer(text), rpc={"jSf9Qc": [usage(2400), usage(0)]}), pro_ctx(log),
                 video_job())
    assert result.job.code == errors.VIDEO_LIMIT
    assert Balance("video", 0, RESET, "5h") in result.account, "not the usage read's reset: %s" % (result.account,)
    # an image refusal, however it is worded, empties nothing while the usage read shows units left
    for words in ("I can't create more images for you today.", "That is over the limit of what I can draw.",
                  "I can't help with that."):
        log = []
        result = run(pro_site(log, send=answer(words), rpc={"jSf9Qc": usage(2400)}), pro_ctx(log), job())
        assert isinstance(result.job, Fail) and result.job.code == errors.REFUSED, "the job outcome stays a refusal"
        assert not any(isinstance(e, Balance) for e in result.account), \
            "an image refusal emptied the image pool on its words: %r" % words
    # ... and empties the image pool until the reset when the read shows a used-up window
    log = []
    result = run(pro_site(log, send=answer("I can't help with that."), rpc={"jSf9Qc": usage(0)}), pro_ctx(log),
                 job())
    assert result.job.code == errors.REFUSED
    assert Balance("image", 0, RESET, "5h") in result.account, "a used-up window did not empty the image pool: %s" % (
        result.account,)


SHORT = "                if video_room_short(settings, room):\n"
ROOM = '    return room is not None and room[0] < int(settings.get("gemini.video_usage_units"))'


@mutation(SUBMIT_FILE, SHORT, "                if False:\n", expect="sent into a used-up window")
@mutation(SUBMIT_FILE, ROOM, '    return room is not None and room[0] <= 0', expect="sent into a used-up window")
def test_a_video_is_not_sent_into_a_five_hour_window_that_cannot_hold_one():
    """Measured 25/10: about 470 units a video, 2400 per 5 h; the 6th video was taken and never rendered."""
    log = []
    result = run(pro_site(log, send=T2V_ACCEPTED, rpc={"jSf9Qc": usage(469)}), pro_ctx(log), video_job())
    assert log.count("SEND") == 0 and "mark_sent" not in log, "sent into a used-up window: %s" % log
    assert result.job == Retry("pool.account_out_of_points")
    assert result.account == (Balance("video", 0, RESET, "5h"),)
    log = []
    result = run(pro_site(log, send=T2V_ACCEPTED, rpc={"jSf9Qc": usage(470)}), pro_ctx(log), video_job())
    assert isinstance(result.job, Accepted) and log.count("SEND") == 1, "one video's room left: it is sent"
    log = []                                                  # an unreadable usage read never blocks the send
    result = run(pro_site(log, send=T2V_ACCEPTED, rpc={"jSf9Qc": httpx.Response(500)}), pro_ctx(log), video_job())
    assert isinstance(result.job, Accepted)


SILENT = "    if video_room_short(settings, room):\n"
DUE = '    return job.sent_at is not None and (now - job.sent_at).total_seconds() >= int(\n'


@mutation(RESULTS_FILE, SILENT, "    if False:\n", expect="a silent render on a used-up window")
@mutation(RESULTS_FILE, DUE, '    return job.sent_at is not None and (now - job.sent_at).total_seconds() >= 0 * int(\n',
          expect="given up too soon")
def test_a_video_still_generating_on_a_used_up_window_fails_as_the_limit_after_the_grace():
    chat = {"chat": "c_000000000000a001"}
    old = video_job(state=chat)                               # sent hours before NOW
    got = poll(pro_site([], rpc={"hNvQHb": T2V_PENDING, "jSf9Qc": usage(50)}), old)
    assert isinstance(got.outcome, Failed) and got.outcome.code == errors.VIDEO_LIMIT, \
        "a silent render on a used-up window: %s" % (got.outcome,)
    assert got.effects == (Balance("video", 0, RESET, "5h"),)
    young = video_job(state=chat, sent_at=NOW - timedelta(seconds=120))
    got = poll(pro_site([], rpc={"hNvQHb": T2V_PENDING, "jSf9Qc": usage(50)}), young)
    assert isinstance(got.outcome, Pending), "given up too soon: %s" % (got.outcome,)
    got = poll(pro_site([], rpc={"hNvQHb": T2V_PENDING, "jSf9Qc": usage(2400)}), old)
    assert isinstance(got.outcome, Pending), "a window with room: the render is waited for"
