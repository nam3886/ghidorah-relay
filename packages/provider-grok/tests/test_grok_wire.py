"""grok's pure readers on the 06/11 captures: Imagine frames, edit/video NDJSON, refusal classes, assets URLs, the
short-delivery note and the evidence summary (fixtures/grok/FINDINGS.md F3, F5, F6, F8, F9)."""

import json

import pytest
from grokkit import EMAIL, SSO, USER_ID, Clock, assert_no_secret, fixture

from relay_core.testing.mutation import mutation
from relay_grok.engine.wire import (
    ImagineReader,
    NdjsonReader,
    asset_url,
    classify_refusal,
    made_note,
    redact,
    summary,
)

WIRE = "packages/provider-grok/src/relay_grok/engine/wire.py"
CONSTANTS = "packages/provider-grok/src/relay_grok/constants.py"
REQ = "img_00000000000000000000000000000001"          # the n2 capture's request id
JOB0, JOB1 = "af000000-0000-4000-8000-000000000003", "a1000000-0000-4000-8000-000000000002"


def frames_in(name: str) -> list[dict]:
    return [f["data"] for f in fixture(name)["frames"] if f["dir"] == "in"]


def read_frames(frames, request_id: str = REQ) -> ImagineReader:
    r = ImagineReader(request_id)
    for f in frames:
        r.feed(f if isinstance(f, str) else json.dumps(f))
    return r


def read_lines(name: str, kind: str, upto: int | None = None, clock=None) -> NdjsonReader:
    fx = fixture(name)
    r = NdjsonReader(kind, clock or Clock())
    for line, t_ms in list(zip(fx["lines"], fx["t_ms"], strict=True))[:upto]:
        if clock is not None:
            clock.t = clock.start + t_ms / 1000
        r.feed(line)
    return r


# ---- Imagine frames (F3) ---------------------------------------------------------------------------------------------
def test_the_n2_capture_reads_two_finals_two_completed_and_the_conversation():
    r = read_frames(frames_in("imagine_t2i_n2"))
    a = r.answer()
    assert r.settled()
    assert [f.job_id for f in a.finals] == [JOB0, JOB1] and a.jobs == (JOB0, JOB1)
    assert [f.url for f in a.finals] == ["https://imagine-public.x.ai/imagine-public/images/%s.jpg" % j
                                         for j in (JOB0, JOB1)]
    assert len(a.deliverable()) == 2 and a.completed == {JOB0, JOB1} and not a.moderated
    assert a.previews == 2 and a.conversation_id == "c0000000-0000-4000-8000-000000000008"
    assert (a.limit, a.safety, a.error_text, a.unknown_frames) == (False, False, None, 0)


@mutation(WIRE, 'jpg = isinstance(url, str) and urlsplit(url).path.endswith(".jpg")', "jpg = isinstance(url, str)",
          expect="a preview was taken for a final")
@mutation(WIRE, 'return jpg and frame.get("percentage_complete") == 100', "return jpg",
          expect="a preview was taken for a final")
def test_a_preview_is_never_a_final():
    base = {"type": "image", "job_id": JOB0, "request_id": REQ}
    odd = [dict(base, url="https://imagine-public.x.ai/imagine-public/images/x.png", percentage_complete=100.0),
           dict(base, url="https://imagine-public.x.ai/imagine-public/images/x.jpg"),
           dict(base, url="https://imagine-public.x.ai/imagine-public/images/x.jpg", percentage_complete=50.0)]
    a = read_frames(odd).answer()
    assert a.finals == () and a.previews == 3, "a preview was taken for a final"
    real = read_frames(frames_in("imagine_t2i_n2")).answer()
    assert all(f.url.endswith(".jpg") for f in real.finals), "a preview was taken for a final"


@mutation(WIRE, "if rid is not None and rid != self.request_id:", "if False:",
          expect="a stray frame counted for this run")
def test_frames_of_another_request_are_ignored():
    frames = frames_in("imagine_stray_request")
    a = read_frames(frames).answer()
    assert [f.job_id for f in a.finals] == [JOB0, JOB1], "a stray frame counted for this run"
    assert "b0000000-0000-4000-8000-000000000009" not in a.completed, "a stray frame counted for this run"
    # the stray's completed never settles this run: drop this run's own last completed
    cut = [f for f in frames if not (f.get("current_status") == "completed" and f.get("job_id") == JOB1)]
    r = read_frames(cut)
    assert not r.settled(), "a stray frame counted for this run"
    assert len(r.answer().finals) == 2 and r.answer().unknown_frames == 0, "a stray frame counted for this run"


def test_a_moderated_completed_job_has_no_deliverable_final():
    r = read_frames(frames_in("imagine_moderated"))
    a = r.answer()
    assert r.settled() and a.moderated == {JOB0} and a.finals == () and a.deliverable() == ()


def test_one_moderated_of_two_leaves_one_final():
    frames = [dict(f, moderated=True) if f.get("current_status") == "completed" and f.get("job_id") == JOB0 else f
              for f in frames_in("imagine_t2i_n2")]
    a = read_frames(frames).answer()
    assert [f.job_id for f in a.deliverable()] == [JOB1]
    assert [(f.job_id, f.moderated) for f in a.finals] == [(JOB0, True), (JOB1, False)]


def test_a_cut_capture_is_not_settled():
    frames = frames_in("imagine_t2i_n2")
    assert not read_frames(frames[:-1]).settled()
    assert not read_frames([]).settled() and not read_frames(frames[:1]).settled()


def test_a_limit_frame_bad_json_and_an_unknown_type():
    r = read_frames(frames_in("imagine_limit_frame") + ["{not json", {"type": "telepathy"}])
    a = r.answer()
    assert a.limit and not a.safety and a.jobs == () and not r.settled()
    assert a.error_text == "You have reached your usage limit. Please try again later."
    assert a.unknown_frames == 2
    s = read_frames([{"type": "error", "err_code": "x", "err_msg": "Content violates usage guidelines"}]).answer()
    assert s.safety and not s.limit


@mutation(WIRE, "            self._rate_limit = self._rate_limit or is_rate_limit(msg)\n",
          "            self._rate_limit = self._rate_limit\n", expect="the real rate limit frame is a rate limit")
def test_the_real_rate_limit_frame_is_a_rate_limit_not_a_usage_limit():
    r = read_frames(frames_in("imagine_rate_limit_frame"))
    a = r.answer()
    assert a.rate_limit, "the real rate limit frame is a rate limit"
    assert not a.limit and not a.safety and a.jobs == () and a.conversation_id is None and not r.settled()
    assert a.error_text == "Image rate limit exceeded"
    assert not read_frames(frames_in("imagine_limit_frame")).answer().rate_limit
    stray = dict(frames_in("imagine_rate_limit_frame")[-1], request_id="img_" + "f" * 32)
    assert not read_frames([stray]).answer().rate_limit, "another run's frame is never ours"


# ---- NDJSON (F5, F6) -------------------------------------------------------------------------------------------------
def test_the_edit_lines_read_one_final_with_its_ids():
    r = read_lines("ndjson_edit", "image", upto=1)
    assert r.ids_new() == {"conversation_id": "c0000000-0000-4000-8000-000000000001"}
    r = read_lines("ndjson_edit", "image")
    a = r.answer()
    assert a.final_url == ("users/d0000000-0000-4000-8000-000000000001/generated/"
                           "af000000-0000-4000-8000-000000000002/image.jpg")
    assert a.asset_id == "af000000-0000-4000-8000-000000000002" and a.moderated is False
    assert a.conversation_id == "c0000000-0000-4000-8000-000000000001"
    assert a.response_id == "e0000000-0000-4000-8000-000000000004"
    assert a.ended and a.progress == 100 and a.first_line_seen and a.rrated_present and a.rrated is False
    assert a.video_id is None and r.ids_new() is None


def test_a_moderated_edit_is_moderated():
    a = read_lines("ndjson_edit_moderated", "image").answer()
    assert a.moderated is True and a.ended


def test_the_t2v_lines_give_the_video_ids_in_the_first_second():
    fx = fixture("ndjson_t2v")
    first = next(i for i, line in enumerate(fx["lines"]) if "streamingVideoGenerationResponse" in line) + 1
    assert fx["t_ms"][first - 1] < 1000
    r = read_lines("ndjson_t2v", "video", upto=first)
    a = r.answer()
    assert (a.video_id, a.video_post_id) == ("90000000-0000-4000-8000-000000000001",
                                             "80000000-0000-4000-8000-000000000001")
    assert r.ids_new() == {"video_id": a.video_id, "video_post_id": a.video_post_id}
    assert a.conversation_id == "c0000000-0000-4000-8000-000000000007" and a.final_url is None and not a.ended


@mutation(WIRE, 'f["rrated_present"], f["rrated"] = True, g["rRated"] is True',
          'f["rrated_present"], f["rrated"] = bool(g["rRated"]), g["rRated"] is True',
          expect="rRated false must count as present")
def test_rrated_false_is_present_and_a_missing_key_is_a_decoy():
    real = read_lines("ndjson_t2v", "video").answer()
    assert real.rrated_present is True and real.rrated is False, "rRated false must count as present"
    assert real.final_url.endswith("/generated_video.mp4") and real.thumbnail_url.endswith("/preview_image.jpg")
    assert (real.width, real.height, real.moderated, real.ended) == (1280, 720, False, True)
    decoy = read_lines("ndjson_t2v_decoy", "video").answer()
    assert decoy.rrated_present is False and decoy.rrated is None
    assert decoy.progress == 100 and decoy.final_url is not None


def test_progress_times_come_from_the_clock():
    a = read_lines("ndjson_t2v", "video", clock=Clock()).answer()
    assert a.progress_at[1] == pytest.approx(0.97) and a.progress_at[100] == pytest.approx(34.5)
    d = read_lines("ndjson_t2v_decoy", "video", clock=Clock()).answer()
    assert d.progress_at[100] == pytest.approx(5.0)


def test_an_error_line_after_progress_is_read_as_a_limit():
    r = NdjsonReader("video", Clock())
    r.feed("")
    r.feed("{oops")
    assert not r.answer().first_line_seen
    r.feed(fixture("ndjson_t2v")["lines"][4])
    r.feed(json.dumps({"error": {"code": 8, "message": "You have reached your usage limit (users/%s/x)" % USER_ID}}))
    a = r.answer()
    assert a.limit and a.progress == 1 and not a.safety and a.first_line_seen
    assert "usage limit" in a.error_text
    assert_no_secret(a.error_text)


# ---- refusal classes (F8, F9) ----------------------------------------------------------------------------------------
def _fx(name: str, status: int | None = None):
    if name.endswith(".html"):
        return (status or 403, "text/html; charset=UTF-8", fixture(name), "cloudflare")
    fx = fixture(name)
    body = fx["lines"][0] if "lines" in fx else json.dumps(fx["body"])
    return (status or fx["status"], fx["headers"].get("content-type", ""), body, fx["headers"].get("server", ""))


_BLOCKED_7 = json.dumps({"error": {"code": 7, "message": "blocked-user: user is blocked", "details": []}})


@mutation(CONSTANTS, 'CF_MARKS = ("just a moment", "__cf_chl", "cf-chl-")',
          'CF_MARKS = ("just a moment", "__cf_chl", "cf-chl-", "challenge-platform")',
          expect="a normal page read as a challenge")
@mutation(CONSTANTS, "ANTIBOT_CODE = 7", "ANTIBOT_CODE = 8", expect="code 7 is the anti-bot answer")
@mutation(WIRE, "    if status == 429 or (400 <= status < 500 and is_limit(body)):",
          "    if status == 429 or is_limit(body):", expect="a 5xx with limit words is no proven refusal")
@pytest.mark.parametrize("answer, kind, why", [
    (_fx("rest_401_no_credentials"), "signed_out", "no credentials is signed out"),
    (_fx("rest_401_bad_credentials"), "signed_out", "bad credentials is signed out"),
    ((401, "application/json", '{"code":7,"message":"x"}', ""), "other", "a 401 without code 16 is not signed out"),
    (_fx("rest_403_code7"), "antibot", "code 7 is the anti-bot answer"),
    (_fx("rest_403_cloudflare.html"), "cloudflare", "a challenge page is cloudflare"),
    ((403, "text/html", "", "cloudflare"), "cloudflare", "an empty cloudflare 403 is cloudflare"),
    ((403, "text/html", "", ""), "other", "an empty 403 not from cloudflare"),
    ((200, "text/html", fixture("page_home.html"), "cloudflare"), "other", "a normal page read as a challenge"),
    ((403, "text/html", fixture("page_home.html"), "cloudflare"), "other", "a normal page read as a challenge"),
    (_fx("rest_403_blocked"), "blocked", "a blocked user is blocked"),
    ((403, "application/json", _BLOCKED_7, ""), "blocked", "blocked phrases come before code 7"),
    (_fx("rest_429"), "limit", "a 429 is a limit"),
    (_fx("send_out_of_quota"), "limit", "the out-of-quota send is a limit"),
    ((400, "application/json", '{"error":{"message":"usage limit reached"}}', ""), "limit", "a limit phrase"),
    ((500, "application/json", '{"error":{"message":"usage limit during response processing"}}', ""), "other",
     "a 5xx with limit words is no proven refusal"),
    ((502, "text/plain", "usage quota", ""), "other", "a 5xx with limit words is no proven refusal"),
    ((200, "application/json", '{"error":{"message":"usage limit reached"}}', ""), "other",
     "only a 4xx refusal is a limit"),
    (_fx("rest_400_code3"), "bad_body", "code 3 is our own bad body"),
    ((500, "text/plain", "upstream error", ""), "other", "anything else is other"),
])
def test_classify_refusal(answer, kind, why):
    status, ctype, body, server = answer
    assert classify_refusal(status, ctype, body, server) == kind, why


# ---- assets URL, note, summary ---------------------------------------------------------------------------------------
def test_asset_url_refuses_absolute_other_host_and_dotdot():
    rel = "users/d0000000-0000-4000-8000-000000000001/generated/a1/generated_video.mp4"
    assert asset_url(rel) == "https://assets.grok.com/" + rel
    for bad in ("https://evil.example/users/x.mp4", "http://assets.grok.com/users/x.mp4", "//evil.example/x",
                "/users/x.mp4", "users/../../x", "users//x", "users\\x", "javascript:alert(1)", "", None, 7):
        assert asset_url(bad) is None, "the cookie could leave assets.grok.com: %r" % (bad,)


def test_made_note_fewer_and_more():
    assert made_note(3, 3, "image") is None
    assert made_note(2, 3, "image") == ("grok made 2 of the 3 images asked; all are charged (any output is a full "
                                        "charge)")
    assert made_note(5, 4, "image") == ("grok made 5 images, 4 were paid: the first 4 are delivered, the rest are "
                                        "not")


def test_the_summary_holds_no_id_blob_or_user_path():
    frame = {"type": "image", "blob": "PLANTED-blob-" + SSO, "job_id": USER_ID, "request_id": USER_ID,
             "url": "https://imagine-public.x.ai/imagine-public/images/%s.jpg" % USER_ID, "percentage_complete": 100.0,
             "prompt": "for %s" % EMAIL}
    video = {"result": {"response": {"streamingVideoGenerationResponse": {
        "videoId": USER_ID, "progress": 100, "videoUrl": "users/%s/generated/x/generated_video.mp4" % USER_ID},
        "responseId": USER_ID}}}
    error = {"error": {"code": 8, "message": "limit for users/%s/x at https://grok.com/u/%s" % (USER_ID, USER_ID)}}
    out = summary([json.dumps(frame), json.dumps(video).encode(), json.dumps(error), "{cut"])
    assert_no_secret(out)
    rows = json.loads(out)
    assert rows[0]["type"] == "image" and rows[0]["progress"] == 100.0 and rows[0]["hosts"] == ["imagine-public.x.ai"]
    assert "blob" in rows[0]["keys"] and "url" in rows[0]["keys"]
    assert rows[1]["type"] == "response.streamingVideoGenerationResponse" and rows[1]["progress"] == 100
    assert rows[2]["type"] == "error" and rows[2]["error"].startswith("limit for <path>")
    assert rows[3] == {"bad": 4}
    real = json.loads(summary(fixture("ndjson_t2v")["lines"]))
    assert len(real) == 20 and real[0]["type"] == "conversation"
    assert "d0000000" not in json.dumps(real) and "90000000" not in json.dumps(real)


FAKE_SSO = "FAKEssoValue"
FAKE_COOKIE = "FAKEcookieValue"
FAKE_BEARER = "FAKEbearerValue"
FAKE_JWT = "eyJFAKEhdr.eyJFAKEbody.FAKEsig"
FAKE_OPAQUE = "FAKEopaque0123456789abcdefABCDEF+/xyz=="
HEADER_LINE = '    text = _HEADER.sub(r"\\1\\2<secret>", text)\n'
PAIR_LINE = '    text = _PAIR.sub(r"\\1\\2<secret>", text)\n'
QUOTED_LINE = '    text = _QUOTED_HEADER.sub(r"\\1\\2<secret>", text)'


@mutation(WIRE, HEADER_LINE, "", expect="a Cookie or Authorization value leaked")
@mutation(WIRE, PAIR_LINE, "", expect="an sso= value leaked")
@mutation(WIRE, '    text = _JWT.sub("<secret>", text)\n', "", expect="a JWT leaked")
@mutation(WIRE, "    text = _TOKEN.sub(_token, text)", "    pass", expect="an opaque token leaked")
@mutation(WIRE, QUOTED_LINE, "    pass", expect="a JSON-quoted header value")
def test_redact_drops_cookies_jwts_and_opaque_tokens():
    echo = ("bad request: sso=%s; sso-rw=%s, header Cookie: a=%s\nAuthorization: Bearer %s\n"
            'json {"sso":"%s"} jwt %s token %s' % (FAKE_SSO, FAKE_SSO, FAKE_COOKIE, FAKE_BEARER, FAKE_SSO, FAKE_JWT,
                                                FAKE_OPAQUE))
    out = redact(echo)
    assert FAKE_COOKIE not in out and FAKE_BEARER not in out, "a Cookie or Authorization value leaked"
    assert FAKE_SSO not in out, "an sso= value leaked"
    assert "eyJFAKE" not in out and "FAKEsig" not in out, "a JWT leaked"
    assert "FAKEopaque" not in out, "an opaque token leaked"
    assert out.startswith("bad request: sso=<secret>; sso-rw=<secret>")
    assert redact("You have reached your usage limit") == "You have reached your usage limit"

    out = redact('Cookie: other="FAKEcookieValue"; session=FAKEsessionValue\nnext line')
    assert out == "Cookie: <secret>\nnext line", "a Cookie or Authorization value leaked"
    out = redact("Set-Cookie: a='FAKEcookieValue'; b=FAKEcookieValue")
    assert "FAKE" not in out, "a Cookie or Authorization value leaked"
    out = redact('{"cookie":"a=FAKEcookieValue; b=\\"FAKEcookieValue\\"","code":3}')
    assert out == '{"cookie":"<secret>","code":3}', "a JSON-quoted header value must end at its own quote"
    out = redact("token FAKEopaque" + "a" * 24 + "XYZ123456789 end")
    assert out == "token <secret> end", "an opaque token leaked"
    assert redact("id d0000000-0000-4000-8000-000000000001 hex " + "ab12" * 8) == "id <id> hex <id>"
    assert redact("key streamingVideoGenerationResponseMetadataX") == "key streamingVideoGenerationResponseMetadataX"

    rows = json.loads(summary([json.dumps({"error": {"code": 3, "message": echo}})]))
    assert "FAKE" not in rows[0]["error"], "an sso= value leaked"
    r = NdjsonReader("video", Clock())
    r.feed(json.dumps({"error": {"code": 8, "message": echo}}))
    assert "FAKE" not in r.answer().error_text, "an sso= value leaked"
