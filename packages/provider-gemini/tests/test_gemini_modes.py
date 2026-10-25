"""The modes added 25/10: portrait video, the video and music gallery templates, and songs (the music tool), checked
against the web app's own requests captured unsent and a recorded song answer (fixtures/gemini/live-261025). Each
rule has a test and a mutation."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
from geminikit import (
    MODE_FLASH_LITE,
    MUSIC_REPLY,
    T2V_ACCEPTED,
    T2V_DONE,
    WEB_REQUESTS,
    Ctx,
    Gemini,
    answer,
    blob_for,
    inner_of,
    job,
    settings,
)

from relay_core.providers import Accepted, Balance, Completed, CreateRequest, Fail, InputError, MediaIn, Output, Success
from relay_core.testing.fake_media import MediaSite, public_resolver
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_gemini import catalog as C
from relay_gemini import errors
from relay_gemini.engine import results as R
from relay_gemini.engine.http import Sessions
from relay_gemini.engine.reply import read_answer
from relay_gemini.engine.submit import judge, request, submit
from relay_gemini.spec import SPEC

SPEC_FILE = "packages/provider-gemini/src/relay_gemini/spec.py"
SUBMIT_FILE = "packages/provider-gemini/src/relay_gemini/engine/submit.py"
RESULTS_FILE = "packages/provider-gemini/src/relay_gemini/engine/results.py"
HTTP_FILE = "packages/provider-gemini/src/relay_gemini/engine/http.py"
VIDEO, MUSIC = "gemini-omni-video", "gemini-music"
EMAIL = "owner.pro@example.com"
PRO = {"authuser": 4, "mode": MODE_FLASH_LITE}
SONG_CHAT = "c_000000000000b000"


def plan(model=MUSIC, prompt="a song about a red bicycle", **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), FakeSettings(SPEC), {})


def refusal(model=MUSIC, prompt="a song about a red bicycle", **kw):
    try:
        plan(model, prompt, **kw)
    except InputError as e:
        return e.code, e.param
    return None


def pro_ctx(log):
    return Ctx(log, blob=blob_for(EMAIL), state=dict(PRO))


def pro_site(log, **kw):
    return Gemini(log, accounts=["a@example.com", "b@example.com", "c@example.com", "d@example.com", EMAIL], **kw)


async def png(ref) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def run(g, ctx, j):
    return asyncio.run(submit(ctx, j, settings(), Sessions(), transport=g.transport, read_media=png))


def captured(name: str) -> list:
    return WEB_REQUESTS[name]["inner"]


def built(name: str, **plan_kw) -> list:
    """The inner the provider builds for the plan, with the captured request's prompt."""
    text = captured(name)[0][0]
    p = plan(prompt=text, **plan_kw)
    ratio = p.params.get("ratio")
    music = dict(p.params) if p.operation == "text_to_music" else None
    inner, _ = request(text, ratio, None, "RID", MODE_FLASH_LITE, video=p.operation == "text_to_video",
                       template=p.params.get("template"), music=music)
    return inner


# ---- portrait video ------------------------------------------------------------------------------------------------
ORIENTATION = "        message = [[None, None, None, C.VIDEO_ORIENTATION[ratio]]]"


@mutation(SUBMIT_FILE, ORIENTATION, '        message = [None, [None, ratio]]', expect="the orientation code")
@mutation(SUBMIT_FILE, "        option = [[C.VIDEO_RATIO_IDS[ratio]]]", "        option = [[16]]",
          expect="the portrait option")
def test_a_video_names_its_option_and_orientation_as_the_web_app_does_landscape_or_portrait():
    """25/10: the web app's portrait send is the option 17 and the orientation code 2 in the message; sends with the
    option and the ratio as text came back landscape (FINDINGS 7). The provider's request is the captured one."""
    for name, ratio in (("t2v_landscape", "16:9"), ("t2v_portrait", "9:16")):
        mine, web = built(name, model=VIDEO, ratio=ratio), captured(name)
        assert mine[55] == web[55], "not the portrait option: %s != %s" % (mine[55], web[55])
        assert mine[0] == web[0], "not the web app's message, the orientation code: %s != %s" % (mine[0], web[0])
        assert mine[49] == web[49] == 11
    assert plan(VIDEO, ratio="9:16").params == {"ratio": "9:16", "duration": 10}


def test_an_image_to_portrait_video_sends_the_portrait_code_beside_its_file():
    log = []
    g = pro_site(log, send=T2V_ACCEPTED)
    result = run(g, pro_ctx(log), job("The apple turns", model=VIDEO, ratio="9:16", images=1))
    inner = inner_of(g.sends()[0])
    assert inner[55] == [[17]] and inner[0][9][6] == [[None, None, None, 2]] and len(inner[0][3][0]) == 2
    assert isinstance(result.job, Accepted)


def test_portrait_video_is_sold_beside_landscape():
    assert {m.id: m for m in SPEC.catalog()}[VIDEO].limits["ratios"] == ["16:9", "9:16"]
    assert refusal(VIDEO, ratio="1:1") == ("input.bad_ratio", "ratio")


# ---- the gallery templates -----------------------------------------------------------------------------------------
TEMPLATE_GUARD = "    if found is None:\n"
VIDEO_TEMPLATE_LINE = "            message += [None, None, *gallery_entry(VIDEO_TEMPLATES, template)]\n"


@mutation(SUBMIT_FILE, VIDEO_TEMPLATE_LINE, "            pass\n",
          expect="the template was not sent")
@mutation(SPEC_FILE, TEMPLATE_GUARD, "    if False:\n", expect="was planned")
def test_a_video_template_rides_in_the_message_as_the_web_app_sends_it():
    """The captured Anime send: the template's id and gallery entry after the orientation, the gallery tab "all"; the
    card's place (19 there) is left out: a template video sent without it was made (25/10, "8-bit adventure")."""
    mine, web = built("t2v_template_anime", model=VIDEO, options={"template": "anime"}), \
        captured("t2v_template_anime")
    assert mine[0][9][6][:7] == web[0][9][6][:7], "the template was not sent: %s" % mine[0][9][6]
    assert mine[0][9][6][7] is None and mine[55] == web[55] == [[16]]
    assert plan(VIDEO, options={"template": " ANIME "}).params["template"] == "Anime"
    assert refusal(VIDEO, options={"template": "Doodle effects"}) == ("input.invalid", "options"), \
        "a template that needs a video upload was planned"
    assert refusal(VIDEO, options={"template": "no such card"}) == ("input.invalid", "options"), \
        "an unknown template was planned"
    assert "template" not in plan(VIDEO).params


def test_a_music_template_rides_in_the_message_as_the_web_app_sends_it():
    mine, web = built("music_template_ringtone", options={"template": "Ringtone"}), captured("music_template_ringtone")
    assert mine[0][9][6][:7] == web[0][9][6][:7] and mine[55] == web[55] == [] and mine[49] == 21
    assert refusal(options={"template": "Ringtone", "genre": "pop"}) == ("input.invalid", "options"), \
        "a template with a genre was planned (not measured)"


# ---- songs -------------------------------------------------------------------------------------------------------
MUSIC_BRANCH = "        if req.model == C.MUSIC_MODEL:\n            return self._music(req, settings)\n"
OPTION_GUARD = "            if not isinstance(value, str) or value.strip().lower() not in allowed:"


@mutation(SPEC_FILE, MUSIC_BRANCH, "", expect="a song planned as an image")
@mutation(SPEC_FILE, OPTION_GUARD, "            if not isinstance(value, str):", expect="an unknown length")
@mutation(SPEC_FILE, '            pool="music", submit_timeout_s=SUBMIT_TIMEOUT_S)',
          '            pool="video", submit_timeout_s=SUBMIT_TIMEOUT_S)', expect="the music pool")
def test_a_song_is_held_on_the_music_pool_with_the_options_asked_for():
    try:
        p = plan(options={"length": "Standard", "vocals": "on", "genre": "Jazz & Blues"})
    except InputError as e:
        raise AssertionError("a song planned as an image: %s" % e.code) from None
    assert (p.operation, p.media_type, p.model) == ("text_to_music", "video", MUSIC), "a song planned as an image"
    assert dict(p.params) == {"length": "standard", "vocals": "on", "genre": "jazz & blues"}
    assert (p.completion.name, p.pool, p.price_key) == ("HOLD", "music", MUSIC), "not on the music pool: %s" % p.pool
    assert dict(plan().params) == {}
    for bad in ({"length": "long"}, {"vocals": "yes"}, {"genre": "polka"}, {"length": 30}):
        assert refusal(options=bad) == ("input.invalid", "options"), "an unknown length was planned: %s" % bad
    assert refusal(ratio="1:1") == ("input.invalid", "ratio")
    assert refusal(duration=30) == ("input.invalid", "duration")
    assert refusal(images=(MediaIn("0" * 64, "image/png", 10),)) == ("input.too_many_images", "images")


CODES_TRIM = "    while codes and codes[-1] is None:\n        codes.pop()\n"
GENRE_ONLY = "    return ([], [ids]) if ids else (None, [])"
GENRE_DROPPED = "    return (None, [ids]) if ids else (None, [])"


@mutation(SUBMIT_FILE, CODES_TRIM, "", expect="not the web app's song request")
@mutation(SUBMIT_FILE, GENRE_ONLY, GENRE_DROPPED,
          expect="not the web app's song request")
@mutation(SUBMIT_FILE, '    tool = VIDEO_TOOL if video else MUSIC_TOOL if music is not None else IMAGE_TOOL',
          '    tool = VIDEO_TOOL if video else IMAGE_TOOL', expect="the music tool")
def test_a_song_is_the_web_apps_music_request_for_every_option_captured():
    cases = {"music_standard_vocals_pop": {"length": "standard", "vocals": "on", "genre": "pop"},
             "music_short_instrumental_ambient": {"length": "short", "vocals": "instrumental", "genre": "ambient"},
             "music_short_vocals": {"length": "short", "vocals": "on"}, "music_standard": {"length": "standard"},
             "music_instrumental": {"vocals": "instrumental"}, "music_genre_jazz": {"genre": "jazz & blues"},
             "music_none": {}}
    for name, options in cases.items():
        mine, web = built(name, options=options), captured(name)
        assert mine[49] == web[49] == 21, "the music tool was not chosen: %s" % mine[49]
        assert (mine[0], mine[55]) == (web[0], web[55]), "not the web app's song request for %s: %s %s != %s %s" % (
            name, mine[0], mine[55], web[0], web[55])


def test_a_song_answer_is_completed_with_its_mp4_never_its_mp3():
    """The recorded song: an MP3 record (audio/mpeg), an MP4 (the cover art over the song, 172.7 s) and the lyrics
    (text/vtt). The relay sells the MP4, a job of the core's video kind."""
    log = []
    g = pro_site(log, send=MUSIC_REPLY)
    result = run(g, pro_ctx(log), job("A cheerful song about a red bicycle", model=MUSIC,
                                      options={"length": "standard"}))
    inner = inner_of(g.sends()[0])
    assert inner[49] == 21 and inner[55] == [[43]] and inner[0][9][6] == [None, None, [5]]
    assert isinstance(result.job, Completed), result.job
    (out,) = result.job.outputs
    assert (out.mime, out.idx) == ("video/mp4", 0) and "/gg-dl/" in out.ref
    assert out.meta == {"chat": SONG_CHAT, "width": 1024, "height": 1024, "duration_s": 172.667}
    assert Success() in result.account


PLACEHOLDERS = "    if not final or any(p in final for p in (IMAGE_PLACEHOLDER, VIDEO_PLACEHOLDER, MUSIC_PLACEHOLDER)):"
NO_SONG_MARK = "    if not final or any(p in final for p in (IMAGE_PLACEHOLDER, VIDEO_PLACEHOLDER)):"


@mutation(SUBMIT_FILE, PLACEHOLDERS, NO_SONG_MARK, expect="a promised song")
@mutation(SUBMIT_FILE, "    media = answer.videos if video or music else answer.images",
          "    media = answer.videos if video else answer.images", expect="the song's MP4")
def test_a_song_promised_without_its_record_is_unknown_and_a_plain_text_answer_is_refused():
    assert judge(200, read_answer(MUSIC_REPLY), music=True)[0] == "done", "the song's MP4 was not read"
    promised = answer("Here it is!\n\nhttp://googleusercontent.com/generated_music_content/0")
    assert judge(200, read_answer(promised), music=True)[0] == "unknown", "a promised song was refused (refunded)"
    verdict, (code, _) = judge(200, read_answer(answer("I can't make songs about that.")), music=True)
    assert (verdict, code) == ("refused", errors.REFUSED)
    log = []
    result = run(pro_site(log, send=answer("I can't make songs about that.")), pro_ctx(log), job("x", model=MUSIC))
    assert isinstance(result.job, Fail) and not any(isinstance(e, Balance) and e.pool == "video"
                                                    for e in result.account), "a song's refusal emptied the video pool"


SONG_LINK = '        if video and "/gg-dl/" in ref:'


@mutation(RESULTS_FILE, SONG_LINK, "        if False:", expect="the poster")
def test_a_songs_mp4_is_fetched_by_its_chats_link_since_the_answers_serves_only_the_poster():
    """25/10: the `/gg-dl/` link of a song's answer resolves (=m22) to a 404 and only its poster JPEG answers; the
    chat's `/gg/` link of the same MP4 resolves to googlevideo.com."""
    log = []
    g = pro_site(log, rpc={"hNvQHb": T2V_DONE}, chain={
        "lh3.googleusercontent.com": (200, "text/plain", "https://lh3.google.com/rd/abc"),
        "lh3.google.com": (200, "text/plain", "https://rr1---sn-abc.googlevideo.com/videoplayback?id=1")})
    mp4 = b"\0\0\0\x18ftypmp42" + b"\0" * 32
    out = Output("https://lh3.googleusercontent.com/gg-dl/SONG4", "video/mp4", 0, None, {"chat": SONG_CHAT})

    async def go():
        return b"".join([p async for p in R.fetch(pro_ctx(log), out, settings(), Sessions(), transport=g.transport,
                                                  media_transport=MediaSite(mp4), media_resolver=public_resolver)])
    assert asyncio.run(go()) == mp4
    first = next(r for r in g.seen if r.url.host == "lh3.googleusercontent.com")
    assert "/gg/" in first.url.path and "gg-dl" not in first.url.path, "the poster's link was resolved: %s" % first.url
    assert "hNvQHb" in log


REDIRECT = '                if resp.is_redirect and host.endswith("." + VIDEO_BYTES_HOST):'


@mutation(HTTP_FILE, REDIRECT, "                if False:", expect="a redirecting googlevideo")
def test_a_googlevideo_link_that_redirects_is_handed_to_the_fetcher_which_follows_it():
    """25/10: the portrait video's googlevideo.com link answered 302 to another googlevideo host."""
    log = []
    g = pro_site(log, chain={
        "lh3.googleusercontent.com": (200, "text/plain", "https://lh3.google.com/rd/abc"),
        "lh3.google.com": (200, "text/plain", "https://rr3---sn-abc.googlevideo.com/videoplayback?id=1")})
    inner = g.handle

    def handle(request):
        if request.url.host.endswith(".googlevideo.com"):
            g.seen.append(request)
            return httpx.Response(302, headers={"location": "https://rr5---sn-def.googlevideo.com/videoplayback?id=1"})
        return inner(request)
    g.transport = httpx.MockTransport(handle)
    mp4 = b"\0\0\0\x18ftypmp42" + b"\0" * 32

    async def go():
        return b"".join([p async for p in R.fetch(pro_ctx(log), Output("https://lh3.googleusercontent.com/gg/V",
                                                                        "video/mp4", 0), settings(), Sessions(),
                                                  transport=g.transport, media_transport=MediaSite(mp4),
                                                  media_resolver=public_resolver)])
    try:
        got = asyncio.run(go())
    except RuntimeError as e:
        raise AssertionError("a redirecting googlevideo link did not resolve: %s" % e) from None
    assert got == mp4


# ---- catalog, pools and prices -------------------------------------------------------------------------------------
FREE_MUSIC = '        {"name": "day", "kind": "day", "tz": "UTC", "budget": {"free": 0, "pro": None, "ultra": None}},'


@mutation(SPEC_FILE, FREE_MUSIC, '        {"name": "day", "kind": "day", "tz": "UTC", "budget": {"pro": None}},',
          expect="a free account")
def test_the_music_model_is_listed_priced_and_kept_off_free_accounts():
    from relay_core.pool import left_of, pool_configs
    model = {m.id: m for m in SPEC.catalog()}[MUSIC]
    assert (model.media_type, model.operations, model.display_name) == ("video", ("text_to_music",),
                                                                        "Gemini Music (Lyria 3.5)")
    assert model.limits["lengths"] == ["short", "standard"] and model.limits["vocals"] == ["on", "instrumental"]
    assert len(model.limits["genres"]) == 17 and "Ringtone" in model.limits["templates"]
    assert "Doodle effects" not in {m.id: m for m in SPEC.catalog()}[VIDEO].limits["templates"]
    s = SPEC.settings()
    assert s["gemini.prices"].default[MUSIC] == 2 and s["gemini.costs"].default[MUSIC] == 1
    cfg = pool_configs("gemini", SPEC.caps, FakeSettings(SPEC).get)["music"]
    now = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    assert left_of(cfg, "free", [], now) == 0, "a free account could take a song"
    assert left_of(cfg, "pro", [], now) == 39
    assert C.template_of(C.MUSIC_TEMPLATES, "brand JINGLE") == "Brand jingle" and C.template_of({}, 3) is None


def test_every_template_entry_is_a_gallery_card_the_send_can_name():
    for table in (C.VIDEO_TEMPLATES, C.MUSIC_TEMPLATES):
        for name, (tid, assets) in table.items():
            assert len(tid) == 15 and tid.isalnum(), name
            assert assets and all(isinstance(p, str) and k in (1, 2, 3) for p, k in assets), name
    inner, _ = request("x", "16:9", None, "RID", MODE_FLASH_LITE, video=True, template="Noir")
    entry = inner[0][9][6][4]
    assert entry[0] == C.VIDEO_TEMPLATES["Noir"][0]
    assert all(a[0].startswith("https://www.gstatic.com/") for a in entry[2])
    assert json.dumps(inner)        # the request is plain JSON


UNKNOWN_OPTIONS = "    if unknown:\n"


@mutation(SPEC_FILE, UNKNOWN_OPTIONS, "    if False:\n", expect="an option gemini never gets")
def test_an_option_the_model_does_not_read_is_refused_rather_than_charged_and_dropped():
    for model, options in ((VIDEO, {"genre": "rock"}), (VIDEO, {"template": "Noir", "length": "short"}),
                           (MUSIC, {"ratio": "16:9"}), (MUSIC, {"style": "anime"})):
        assert refusal(model, options=options) == ("input.invalid", "options"), \
            "an option gemini never gets was planned: %s %s" % (model, options)
    assert plan(VIDEO, options={"template": "Noir"}).params["template"] == "Noir"
    assert dict(plan(options={"length": "short", "vocals": "on", "genre": "pop"}).params)["genre"] == "pop"
