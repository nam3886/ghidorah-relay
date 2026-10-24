"""The video path and the account index over a fake gemini.google.com (no network), fed the AI Pro account's recorded
answers (fixtures/gemini/live-261024, /u/4, 24/10). Each money or verdict rule has a test and a mutation."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from geminikit import (
    I2V_ACCEPTED,
    I2V_DONE,
    I2V_ERROR,
    I2V_PENDING,
    I2V_PROMPT,
    MODE_FLASH_LITE,
    NOT_ENTITLED,
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
    inner_of,
    job,
    settings,
)

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Cooldown,
    Done,
    Fail,
    Failed,
    Output,
    Pending,
    Rest,
    Retry,
    Success,
)
from relay_core.testing.fake_media import MediaSite, public_resolver
from relay_core.testing.mutation import mutation
from relay_gemini import errors
from relay_gemini.engine import results as R
from relay_gemini.engine.http import Sessions
from relay_gemini.engine.reply import read_answer, turns
from relay_gemini.engine.submit import submit

SUBMIT_FILE = "packages/provider-gemini/src/relay_gemini/engine/submit.py"
RESULTS_FILE = "packages/provider-gemini/src/relay_gemini/engine/results.py"
REPLY_FILE = "packages/provider-gemini/src/relay_gemini/engine/reply.py"
HTTP_FILE = "packages/provider-gemini/src/relay_gemini/engine/http.py"
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
TAKEN = '    if video and answer.generating:\n        return "taken", None\n'


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
    run(g, pro_ctx(log), video_job())
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


LIMIT = "    if video and VIDEO_LIMIT_TEXT in final:\n"
LIMIT_REST = "        limited = (limit_rest(settings, now),) if code == errors.VIDEO_LIMIT else ()\n"


@mutation(SUBMIT_FILE, LIMIT, "    if False:\n", expect="the video limit")
@mutation(SUBMIT_FILE, LIMIT_REST, "        limited = ()\n", expect="the video pool did not rest")
def test_the_video_limit_answer_fails_with_its_refund_and_rests_the_accounts_video_pool():
    text = "I can't create more videos for you today, but I can still find videos from the web."
    log = []
    result = run(pro_site(log, send=answer(text)), pro_ctx(log), video_job())
    assert isinstance(result.job, Fail) and result.job.code == errors.VIDEO_LIMIT, \
        "the video limit answer became %s" % (result.job,)
    assert errors.CODES[errors.VIDEO_LIMIT].refund and errors.CODES[errors.VIDEO_LIMIT].terminal
    assert Rest("video", NOW + timedelta(seconds=5 * 3600), "gemini answered the account's video limit is reached") \
        in result.account, "the video pool did not rest: %s" % (result.account,)
    # the same words on an image job are an ordinary refusal; the free plan's answer stays not_entitled
    log = []
    assert run(pro_site(log, send=answer(text)), pro_ctx(log), job()).job.code == errors.REFUSED
    log = []
    assert run(pro_site(log, send=NOT_ENTITLED), pro_ctx(log), video_job()).job.code == errors.NOT_ENTITLED


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
    result = run(pro_site(log, send=T2V_ACCEPTED), Ctx(log, blob=blob_for(EMAIL), state={"authuser": 4}), video_job())
    assert log.count("SEND") == 0 and result.job == Retry(errors.UNRESOLVED), "sent with no mode: %s" % log


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
        waiting = poll(Gemini([], rpc={"hNvQHb": pending}), video_job(state=state))
        assert isinstance(waiting.outcome, Pending), "a chat still generating became %s" % (waiting.outcome,)
        got = poll(Gemini([], rpc={"hNvQHb": done}), video_job(state=state))
        assert isinstance(got.outcome, Done), "the video was not read: %s" % (got.outcome,)
        (out,) = got.outcome.outputs
        assert out.mime == "video/mp4" and out.ref.startswith("https://lh3.googleusercontent.com/gg/")
        assert out.meta == {"chat": chat, "width": 1280, "height": 720, "duration_s": 10.005}, out.meta
        assert turns(done)[0][0].videos[0].size == size
    # the i2v chat's input image (in what was sent) is never taken for a result
    assert turns(I2V_PENDING)[0][0].images == () and turns(I2V_PENDING)[0][0].videos == ()


POLL_LIMIT = "        limited = (limit_rest(settings, now()),) if code == errors.VIDEO_LIMIT else ()\n"


@mutation(RESULTS_FILE, POLL_LIMIT, "        limited = ()\n", expect="the video pool did not rest")
def test_a_chat_that_answered_the_video_limit_fails_refunded_and_rests_the_video_pool():
    got = poll(Gemini([], rpc={"hNvQHb": VIDEO_LIMIT}), video_job(state={"chat": "c_000000000000a000"}))
    assert isinstance(got.outcome, Failed) and got.outcome.code == errors.VIDEO_LIMIT, got.outcome
    assert [e.pool for e in got.effects if isinstance(e, Rest)] == ["video"], "the video pool did not rest"


def test_a_lost_video_send_is_found_by_its_exact_text_like_an_image():
    from geminikit import chat_read, listing
    start = video_job().sent_at.timestamp()
    g = Gemini([], rpc={"MaZiqc": listing(("c_00000000000000b2", start + 1)),
                        "hNvQHb": chat_read(T2V_PROMPT, "", image=False, cid="c_00000000000000b2")})
    found = asyncio.run(R.reconcile(pro_ctx(g.log), video_job(), settings(), Sessions(), transport=g.transport))
    assert found.ref == "c_00000000000000b2" and "SEND" not in g.log


FIRST_LINK = '        current = re.sub(r"=[A-Za-z0-9-]+$", "", link) + size + "?alr=yes"'
SIZE = "        link = await site.resolve(out.ref, VIDEO_SIZE if video else FULL_SIZE)"
LINK_AU = ('            current = text if not au or urlsplit(text).hostname == BYTES_HOST \\\n'
           '                else text + ("&" if "?" in text else "?") + au[1:]')


@mutation(RESULTS_FILE, SIZE, "        link = await site.resolve(out.ref)", expect="the video's MP4")
@mutation(HTTP_FILE, LINK_AU, "            current = text", expect="did not name the account")
@mutation(HTTP_FILE, FIRST_LINK + " + au", FIRST_LINK, expect="did not name the account")
def test_a_video_is_fetched_as_its_720p_mp4_from_googlevideo_naming_the_account_on_every_session_hop():
    log = []
    g = Gemini(log, chain={
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
    assert "cookie" not in next(r for r in g.seen if r.url.host.endswith("googlevideo.com")).headers
    assert site.routes, "the bytes were not downloaded"


def test_a_video_link_that_leaves_the_google_hosts_is_never_followed():
    log = []
    g = Gemini(log, chain={"lh3.googleusercontent.com": (200, "text/plain", "https://googlevideo.com.evil.example/x")})

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
