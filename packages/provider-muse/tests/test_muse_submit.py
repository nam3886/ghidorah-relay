"""The submit against an in-process muse (its site over HTTP, its VM behind a real Noise responder), fed the recorded
histories of fixtures/muse/spike-261026. Money rules first: the chat id before `mark_sent`, one send, no re-send."""

import asyncio
import base64
import json
from datetime import timedelta

import pytest
from musekit import (
    IMAGE_REF,
    NOW,
    SESSION_OK,
    Ctx,
    Site,
    folders,
    history,
    job,
    settings,
    stream_bodies,
    vm_routes,
)

from relay_core.providers import (
    Accepted,
    Ambiguous,
    Completed,
    Cooldown,
    Dead,
    Fail,
    MediaRef,
    NotTaken,
    ProxyBad,
    Rest,
    Retry,
    Success,
    Suspect,
)
from relay_core.testing.mutation import mutation
from relay_muse.credential import jar_of
from relay_muse.engine import submit as submit_mod
from relay_muse.engine.web import NetError

SUBMIT_FILE = "packages/provider-muse/src/relay_muse/engine/submit.py"
WEB_FILE = "packages/provider-muse/src/relay_muse/engine/web.py"
CHAT_FILE = "packages/provider-muse/src/relay_muse/engine/chat.py"
REPORT_REF = "            await self._core(lambda: self.ctx.report_ref(self.session, state))\n"
LEASE_CALL = "        info = await lease_workspace(web)\n    vm, endpoint = workspace(info)"
TOKEN_CHECK = "    if not isinstance(token, str) or not token or not isinstance(notary, str) or not notary.strip():"


def chat_of(site: Site) -> str:
    return stream_bodies(site)[0]["session_id"]


def answered(name, after=0):
    """A history route: empty for `after` reads, then the recorded job's events for the chat it is asked about."""
    seen = {"n": 0}

    def events(req):
        seen["n"] += 1
        if seen["n"] <= after:
            return []
        return history(name, req.path.rsplit("session_id=", 1)[1])
    return events


def run(site: Site, j=None, ctx=None, **over):
    ctx = ctx or Ctx()
    deps = site.deps(read_media=_media, now=lambda: NOW)
    result = asyncio.run(submit_mod.submit(ctx, j or job(), settings(**over), deps=deps))
    return result, ctx


async def _media(ref):
    return b"RIFFwebp"


def test_an_image_job_is_sent_once_in_a_new_side_chat_and_completes_with_its_file():
    site = Site(vm_routes_=vm_routes([answered("job_j01.json", after=1)]))
    result, ctx = run(site)
    assert isinstance(result.job, Completed) and result.account == (Success(),)
    (out,) = result.job.outputs
    sid = chat_of(site)
    assert out.ref.startswith("workspace/imagine_media/media-generation-generate-one-image")
    assert out.mime == "image/webp"
    assert out.meta == {"session_id": sid, "job_id": "job-1", "last": True}
    assert "/session/delete" not in site.vms[0].paths(), "a done chat is deleted by the fetch of its last output"
    vm = site.vms[0]
    assert vm.paths()[:7] == ["/client/register-capabilities", "/chat/history?limit=1&transcript_mode=messages",
                              "/chat/subscribe", "/api/nodes/register", "/fs/list", "/fs/list", "/chat/stream"]
    assert "session_id" not in vm.requests[2].json() and vm.requests[2].json()["after_chat_event_seq"] == 41
    assert vm.requests[4].json() == {"path": "workspace/imagine_media"}, "the media folders are listed before"
    assert vm.paths().count("/chat/stream") == 1
    assert all(p in ("/chat/history?limit=40&transcript_mode=messages&session_id=" + sid, "/fs/list")
               for p in vm.paths()[7:])
    body = stream_bodies(site)[0]
    assert body["node_id"] == vm.requests[0].json()["client_id"] == vm.requests[3].json()["node_id"]
    assert body["message"].startswith("Generate exactly one separate image")
    assert "a red fox in fresh snow" in body["message"]
    assert vm.closed


@mutation(SUBMIT_FILE, REPORT_REF, "", expect="the chat id first")
def test_the_chat_id_is_kept_before_mark_sent_and_the_send_comes_after_it():
    site = Site(vm_routes_=vm_routes([answered("job_j01.json")]))
    result, ctx = run(site)
    sid = chat_of(site)
    assert ctx.calls[:2] == [("report_ref", sid, {"session_id": sid, "before": []}), ("mark_sent",)], \
        "the chat id first"


def test_a_refused_report_ref_or_mark_sent_propagates_and_nothing_is_sent():
    for gate in ("report_ref", "mark_sent"):
        site = Site(vm_routes_=vm_routes([answered("job_j01.json")]))
        with pytest.raises(RuntimeError, match="lease lost"):
            run(site, ctx=Ctx(fail=gate))
        assert stream_bodies(site) == []


@mutation(WEB_FILE, '                                         proxies=self.proxies, impersonate="chrome")',
          '                                         impersonate="chrome")', expect="the WebSocket's proxy")
def test_every_call_goes_through_the_accounts_proxy_and_rotated_cookies_are_kept():
    site = Site(vm_routes_=vm_routes([answered("job_j01.json")]))
    result, ctx = run(site)
    proxy = {"http": "http://user:pw@10.0.0.9:3128", "https": "http://user:pw@10.0.0.9:3128"}
    assert site.made == [proxy]
    ws = next(c for c in site.calls if c.method == "WS")
    assert ws.proxies == proxy, "the WebSocket's proxy"
    assert ws.path.startswith("wss://hatch.metaaivm.com/v1/noise?vm_id=%s&auth_token=tok-abc&notary_token="
                              "endorsement.v1.n&app_id=hatch-web&request_id=" % SESSION_OK["vm_id"])
    token = next(c for c in site.calls if c.path == "/api/hatch/token")
    assert token.headers["Sec-Fetch-Site"] == "same-origin" and token.headers["Sec-Fetch-Mode"] == "cors"
    assert json.loads(token.data) == {"vmAddress": SESSION_OK["endpoint_url"], "vmName": SESSION_OK["vm_id"]}
    assert site.cookies_seen[0] == {"hatch_sess": "sess-value-1", "hatch_vml": "vml-1"}
    assert [jar_of(b)["hatch_vml"] for b in ctx.renewed] == ["vml-2"]
    no_proxy = Site(vm_routes_=vm_routes([answered("job_j01.json")]))
    run(no_proxy, ctx=Ctx(proxy=None))
    assert no_proxy.made == [None]


CANCEL = "            await cancel_chat(tunnel, self.session, step)\n"


@mutation(SUBMIT_FILE, "                if found == \"refused\":", "                if False:", expect="Fail")
@mutation(SUBMIT_FILE, CANCEL, "", expect="cancelled before the delete")
def test_an_answer_in_words_with_no_image_is_refused_and_refunded_once_settled():
    site = Site(vm_routes_=vm_routes([answered("job_j07.json")]))
    result, _ = run(site)
    assert isinstance(result.job, Fail), result
    assert result.job.code == "muse.refused" and "Taylor Swift" in result.job.detail
    assert result.account == (Success(),)
    ends = [(r.path, r.json()) for r in site.vms[0].requests if r.path in ("/chat/cancel", "/session/delete")]
    sid = chat_of(site)
    assert ends[:2] == [("/chat/cancel", {"session_id": sid}),
                        ("/session/delete", {"method": "/api/session/delete", "session_id": sid})], \
        "cancelled before the delete: %s" % ends


NO_VIDEO_HERE = "region-301030/history_video_unavailable.json"
NO_VIDEO_BRANCH = '                    if self.kind == "video" and video_unavailable_here(reading.text):'
KEEP_STRIKES = "        self.keep_strikes = True\n"
DROP_REFUSED = "                    await self._drop_chat(tunnel, timeout)\n"
DELETE_RETRY = "            if reply.status != 409 or final:"
BEST_EFFORT = "    except Exception as e:      # noqa: BLE001 - whatever muse answers, the delete never changes the job"
NARROW = "    except ValueError as e:      # noqa: BLE001 - whatever muse answers, the delete never changes the job"
BUSY = (409, {"ok": False, "error": {"code": "conflict", "message": "session has an active message"}})


def video_job(**kw):
    return job("text_to_video", sent_text="a paper boat drifting down a rainy street", **kw)


def deleted(site: Site) -> list[dict]:
    return [r.json() for vm in site.vms for r in vm.requests if r.path == "/session/delete"]


@mutation(SUBMIT_FILE, NO_VIDEO_BRANCH, "                    if False:", expect="another account")
@mutation(SUBMIT_FILE, KEEP_STRIKES, "        self.keep_strikes = False\n", expect="no Success")
@mutation(SUBMIT_FILE, DROP_REFUSED, "", expect="the chat is deleted")
def test_muse_saying_the_account_makes_no_video_where_it_is_moves_the_job_and_rests_the_video_pool():
    site = Site(vm_routes_=vm_routes([answered(NO_VIDEO_HERE)]))
    result, ctx = run(site, video_job())
    assert result.job == NotTaken("pool.account_region_blocked", {"refused_by": ["acc-1"]}), "another account"
    assert not any(isinstance(e, Success) for e in result.account), "no Success: %s" % (result.account,)
    (rest,) = result.account
    assert isinstance(rest, Rest) and rest.pool == "video" and rest.until == NOW + timedelta(days=7)
    assert "country or region" in rest.reason
    assert any("isn't available in your country or region" in text for _, text in ctx.events)
    assert any("goes to another account" in text for _, text in ctx.events)
    assert deleted(site) == [{"method": "/api/session/delete", "session_id": chat_of(site)}], "the chat is deleted"
    assert len(stream_bodies(site)) == 1
    later = run(Site(vm_routes_=vm_routes([answered(NO_VIDEO_HERE)])), video_job(), video_region_rest_s=3600)[0]
    assert later.account[0].until == NOW + timedelta(hours=1)


def test_the_last_account_muse_refuses_or_a_pinned_job_ends_with_the_refund_and_a_prompt_refusal_moves_nothing():
    site = Site(vm_routes_=vm_routes([answered(NO_VIDEO_HERE)]))
    result, _ = run(site, video_job(state={"refused_by": ["acc-0"]}))
    assert isinstance(result.job, Fail) and result.job.code == "muse.video_unavailable_on_accounts", result.job
    assert result.job.state_patch["refused_by"] == ["acc-0", "acc-1"] and "2 account(s)" in result.job.detail
    assert [type(e) for e in result.account] == [Rest] and deleted(site)
    for j, over in ((video_job(), {"refusal_max_accounts": 1}), (video_job(pinned="job-0"), {})):
        result = run(Site(vm_routes_=vm_routes([answered(NO_VIDEO_HERE)])), j, **over)[0]
        assert isinstance(result.job, Fail) and result.job.code == "muse.video_unavailable_on_accounts"
    image = run(Site(vm_routes_=vm_routes([answered(NO_VIDEO_HERE)])))[0]     # an image job: not about videos
    assert isinstance(image.job, Fail) and image.job.code == "muse.refused" and image.account == (Success(),)
    words = run(Site(vm_routes_=vm_routes([answered("job_j07.json")])), video_job())[0]
    assert isinstance(words.job, Fail) and words.job.code == "muse.refused" and words.account == (Success(),)


@mutation(CHAT_FILE, DELETE_RETRY, "            if True:", expect="tried again")
def test_a_delete_muse_refuses_or_never_answers_changes_nothing_and_a_busy_chat_is_tried_again():
    tries = []

    def busy_then_deleted(req):
        tries.append(req.json())
        return BUSY if len(tries) == 1 else (200, {"ok": True, "result": {"deleted": True}})
    site = Site(vm_routes_=vm_routes([answered("job_j07.json")], overrides={"/session/delete": busy_then_deleted}))
    result, _ = run(site)
    assert isinstance(result.job, Fail) and result.job.code == "muse.refused"
    assert len(tries) == 2, "tried again"
    for answer in (BUSY, (500, {"ok": False}), (200, {"ok": False}), ("reset", "gone"), "silent"):
        site = Site(vm_routes_=vm_routes([answered("job_j07.json")], overrides={"/session/delete": answer}))
        result, _ = run(site)
        assert isinstance(result.job, Fail) and result.job.code == "muse.refused", (answer, result)
        assert len(deleted(site)) == (2 if answer == BUSY else 1)


@mutation(CHAT_FILE, BEST_EFFORT, NARROW, expect="malformed")
def test_a_malformed_answer_to_the_delete_changes_nothing():
    for body in (b"not json", b"[" * 100_000, b'{"ok": true, "result": [1]}'):     # the second: too deep to decode
        site = Site(vm_routes_=vm_routes([answered("job_j07.json")], overrides={"/session/delete": (200, body)}))
        result, _ = run(site)
        assert isinstance(result.job, Fail) and result.job.code == "muse.refused", "malformed: %s" % (result,)
        assert result.account == (Success(),) and len(deleted(site)) == 1


@mutation(SUBMIT_FILE, "                    seen, since = signature(reading), self.deps.clock()",
          "                    seen = signature(reading)", expect="before the second card")
def test_a_new_card_starts_the_settle_again():
    """Nothing for 0.8 s, the first of two images at 0.8 s, the second at 1.1 s: with a 0.6 s settle the first card
    must not be delivered alone (the quiet time before it does not count)."""
    import time
    start = []

    def timed(req):
        sid = req.path.rsplit("session_id=", 1)[1]
        start.append(time.monotonic())
        t = time.monotonic() - start[0]
        events = history("history_j06.json", sid)
        return [] if t < 0.8 else events[:3] if t < 1.1 else events
    site = Site(vm_routes_=vm_routes([timed]))
    result, _ = run(site, job(n=2), settle_s=0.6, history_poll_s=0.05)
    assert isinstance(result.job, Completed)
    assert len(result.job.outputs) == 2, "before the second card"


def test_fewer_images_than_asked_are_delivered_once_settled_and_charged():
    site = Site(vm_routes_=vm_routes([answered("job_j01.json")]))
    result, ctx = run(site, job(n=3))
    assert isinstance(result.job, Completed) and len(result.job.outputs) == 1
    assert any("made 1 of the 3 images" in text for _, text in ctx.events)


@mutation(SUBMIT_FILE, "            if left <= 0:\n                return self._accepted()",
          "            if left <= 0:\n                return _result(Ambiguous('late'))", expect="taken")
def test_past_the_hold_a_taken_order_is_accepted_for_the_poll():
    site = Site(vm_routes_=vm_routes([[]]))
    result, _ = run(site, image_submit_timeout_s=submit_mod.HOLD_MARGIN_S + 0.3)
    assert isinstance(result.job, Accepted), "taken: %s" % (result.job,)
    sid = chat_of(site)
    assert result.job.ref == sid and result.job.taken
    state = dict(result.job.provider_state)
    assert state["session_id"] == sid and state["seen"] == [0, 0, False, False, 0] and "empty_at" not in state


HANDOFF = '            state["empty_at"] = self._wall(self.empty)\n'
SEEN_HANDOFF = '            state["seen"], state["seen_at"] = self.settling[0], self._wall(self.settling[1])\n'


@mutation(SUBMIT_FILE, HANDOFF, "            pass\n", expect="the grace goes on in the poll")
@mutation(SUBMIT_FILE, SEEN_HANDOFF, "            pass\n", expect="the grace goes on in the poll")
def test_past_the_hold_the_grace_goes_on_in_the_poll_from_its_first_empty_read():
    """A settled chat with no new file at the end of the hold: the poll refuses at the grace's original end."""
    import time
    from datetime import datetime

    from relay_core.providers import Failed, Pending
    from relay_muse.engine import results
    start = time.monotonic()

    def now():
        return NOW + timedelta(seconds=time.monotonic() - start)
    same = folders("2099-01-01T00:00:00.000Z")
    site = Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=same))
    over = {"image_submit_timeout_s": submit_mod.HOLD_MARGIN_S + 0.6, "files_grace_s": 30}
    result = asyncio.run(submit_mod.submit(Ctx(), edit_job(), settings(**over),
                                           deps=site.deps(read_media=_media, now=now)))
    assert isinstance(result.job, Accepted), result.job
    handed = dict(result.job.provider_state)
    empty_at = datetime.fromisoformat(handed.get("empty_at", "2000-01-01T00:00:00+00:00"))
    assert NOW <= empty_at <= now(), "the grace goes on in the poll: %s" % (handed,)
    state = {**handed, "before": names(same[0])}

    def poll(at, st):
        polled = Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=same))
        taken = job("image_to_image", external_ref=result.job.ref, state=st)
        return asyncio.run(results.poll(Ctx(), taken, settings(files_grace_s=30),
                                        deps=polled.deps(now=lambda: at))).outcome
    within = poll(empty_at + timedelta(seconds=10), state)
    assert isinstance(within, Pending), "the grace goes on in the poll: %s" % (within,)
    state = {**state, **within.state_patch}
    at_end = poll(empty_at + timedelta(seconds=31), state)
    assert isinstance(at_end, Failed) and at_end.code == "muse.refused", \
        "the grace goes on in the poll: refused at its original end, %s" % (at_end,)


def test_a_tunnel_that_breaks_after_the_acknowledgement_leaves_the_order_taken():
    def broken(req):
        raise_after = site.vms[0]
        raise_after.broken()
        return "silent"
    site = Site(vm_routes_=vm_routes([broken]))
    result, _ = run(site)
    assert isinstance(result.job, Accepted) and result.job.taken
    assert len(stream_bodies(site)) == 1


@mutation(SUBMIT_FILE, "            except TunnelError as e:\n                return await self._unknown(",
          "            except TunnelError as e:\n                return _result(Retry(errors.HTTP_ERROR)) or (",
          expect="Ambiguous")
def test_no_answer_to_the_send_is_unknown_and_never_sent_again():
    site = Site(vm_routes_=vm_routes(stream="silent"))
    result, ctx = run(site)
    assert isinstance(result.job, Ambiguous), result
    assert len(stream_bodies(site)) == 1 and ("mark_sent",) in ctx.calls


@mutation(SUBMIT_FILE, "            if 400 <= reply.status < 500:", "            if False:", expect="NotTaken")
def test_a_4xx_to_the_send_is_not_taken_and_a_5xx_is_unknown():
    site = Site(vm_routes_=vm_routes(stream=(400, {"ok": False, "error": {"code": "invalid_params"}})))
    result, ctx = run(site)
    assert isinstance(result.job, NotTaken) and result.job.code == "muse.http_error", result
    assert any("invalid_params" in text for _, text in ctx.events)
    site = Site(vm_routes_=vm_routes(stream=(502, {"error": "bad gateway"})))
    assert isinstance(run(site)[0].job, Ambiguous)
    other_chat = Site(vm_routes_=vm_routes(stream=(200, {"session_id": "99999999-0000-4000-8000-000000000000"})))
    assert isinstance(run(other_chat)[0].job, Ambiguous)


@mutation(SUBMIT_FILE, "            return _result(Retry(POOL_LOGGED_OUT), *signed_out_effects(now))",
          "            return _result(Retry(errors.HTTP_ERROR))", expect="pool.account_logged_out")
def test_a_signed_out_session_sends_nothing_strikes_once_and_asks_for_a_recheck():
    site = Site({"GET /api/session": (401, {"error": "Unauthorized"}, {"hatch_sess": "deleted"})})
    result, ctx = run(site)
    assert result.job == Retry("pool.account_logged_out")
    strike, rest = result.account
    assert isinstance(strike, Suspect) and strike.min_gap_s == 540
    assert isinstance(rest, Cooldown) and rest.recheck and (rest.until - NOW).total_seconds() == 600
    assert site.vms == [] and ctx.calls == [] and ctx.renewed == [], "nothing sent, no cookie kept"


@mutation(WEB_FILE, '    if info.get("vm_state") != RUNNING:\n        await wake(',
          "    if False:\n        await wake(", expect="POST /api/hatch/vm/wake")
def test_a_sleeping_workspace_is_woken_and_the_job_goes_on():
    asleep = {**SESSION_OK, "vm_state": "SLEEPING"}
    site = Site({"GET /api/session": [(200, asleep, {}), (200, asleep, {}), (200, SESSION_OK, {})]},
                vm_routes_=vm_routes([answered("job_j01.json")]))
    result, _ = run(site)
    assert isinstance(result.job, Completed)
    assert site.paths()[:4] == ["GET /api/session", "POST /api/hatch/vm/wake", "GET /api/session",
                                "GET /api/session"]
    wake = site.calls[1]
    assert json.loads(wake.data)["vm_id"] == SESSION_OK["vm_id"] and wake.headers["Sec-Fetch-Mode"] == "cors"


def test_a_workspace_that_does_not_wake_or_none_assigned_goes_back_to_the_queue_unsent():
    asleep = {**SESSION_OK, "vm_state": "SLEEPING"}
    for answer in ((200, asleep, {}), (200, {"status": "unassigned"}, {})):
        site = Site({"GET /api/session": answer})
        result, ctx = run(site)
        assert result.job == Retry("muse.vm_not_ready")
        assert isinstance(result.account[-1], Cooldown) and ctx.calls == [] and site.vms == []


@mutation(WEB_FILE, TOKEN_CHECK, "    if False:", expect="403 to the tunnel token call")
def test_a_failed_step_before_the_send_is_retried_unsent():
    forbidden = Site({"POST /api/hatch/token": (403, {"error": "Forbidden"}, {})})
    result, ctx = run(forbidden)
    assert result.job == Retry("muse.http_error") and ctx.calls == [] and forbidden.vms == []
    assert any("muse answered 403 to the tunnel token call" in text for _, text in ctx.events), ctx.events
    empty = Site({"POST /api/hatch/token": (200, {"token": "t"}, {})})
    assert run(empty)[0].job == Retry("muse.http_error")
    node = Site(vm_routes_=vm_routes(overrides={"/api/nodes/register": (400, {"ok": False})}))
    result, ctx = run(node)
    assert result.job == Retry("muse.http_error") and ctx.calls == [] and stream_bodies(node) == []


def test_a_proxy_that_fails_rests_the_account_and_marks_the_proxy():
    class ProxyError(Exception):
        pass
    site = Site({"GET /api/session": ProxyError("connect to http://user:pw@10.0.0.9:3128 failed")})
    result, ctx = run(site)
    assert result.job == Retry("pool.proxy_failed")
    proxy = {"http": "http://user:pw@10.0.0.9:3128", "https": "http://user:pw@10.0.0.9:3128"}
    assert site.made == [proxy] and site.paths() == ["GET /api/session"], "never a direct try after the proxy failed"
    assert [type(e) for e in result.account] == [ProxyBad, Cooldown]
    assert not any("pw" in text for _, text in ctx.events)
    plain = Site({"GET /api/session": OSError("reset")})
    assert run(plain)[0].job == Retry("muse.http_error")
    assert str(NetError("ProxyError", True)) == "muse.ai could not be reached (ProxyError)"


def test_an_image_to_video_carries_its_image_inline():
    site = Site(vm_routes_=vm_routes([answered("job_j10.json")]))
    j = job("image_to_video", sent_text="the fox turns its head", inputs=(IMAGE_REF,))
    result, _ = run(site, j)
    assert isinstance(result.job, Completed) and result.job.outputs[0].mime == "video/mp4"
    body = stream_bodies(site)[0]
    assert body["items"][0]["data_base64"] == "UklGRndlYnA=" and body["items"][0]["mime_type"] == "image/webp"
    assert body["items"][1]["text"].startswith("Animate the attached image into exactly one 5-second video")
    assert body["items"][0]["filename"] == "image1.webp"


EDIT_INPUTS = '_COUNT = {"image_to_image": MAX_REFS, "image_to_video": 1}'


@mutation(SUBMIT_FILE, EDIT_INPUTS, '_COUNT = {"image_to_video": 1}', expect="sent inline, in order")
def test_an_edit_sends_its_references_in_order_before_the_text_and_delivers_one_image():
    """The spike's A2 (30/10): two references, the object of image 1 put into the scene of image 2, one WebP."""
    site = Site(vm_routes_=vm_routes([answered("ref-spike-301030/history_edit_two_refs.json", after=1)]))
    refs = (MediaRef(0, "jobs/job-1/input/0.jpg", "image/jpeg", 4), MediaRef(1, "jobs/job-1/input/1.webp",
                                                                            "image/webp", 4))
    read = {r.object_key: data for r, data in zip(refs, (b"fox!", b"tower"), strict=True)}

    async def media(ref):
        return read[ref.object_key]

    j = job("image_to_image", sent_text="Put the object from image 1 into the scene of image 2.", inputs=refs)
    result = asyncio.run(submit_mod.submit(Ctx(), j, settings(), deps=site.deps(read_media=media, now=lambda: NOW)))
    bodies = stream_bodies(site)
    assert len(bodies) == 1 and [i["type"] for i in bodies[0].get("items", ())] == ["image", "image", "text"], \
        "sent inline, in order"
    body = bodies[0]
    assert [(base64.b64decode(i["data_base64"]), i["mime_type"], i["filename"]) for i in body["items"][:2]] == [
        (b"fox!", "image/jpeg", "image1.jpg"), (b"tower", "image/webp", "image2.webp")]
    assert body["items"][2]["text"].startswith("Using the two attached images (image 1 to image 2, in the order "
                                               "attached), generate exactly one image")
    assert isinstance(result.job, Completed) and result.account == (Success(),)
    (out,) = result.job.outputs
    assert out.ref.startswith("workspace/imagine_media/media-generation-put-the-object-from-image-1-into-0-")
    assert out.mime == "image/webp" and out.meta == {"session_id": chat_of(site), "job_id": "job-1", "last": True}
    assert len(stream_bodies(site)) == 1


def test_an_edit_whose_references_are_missing_or_not_images_sends_nothing():
    for inputs in ((), (IMAGE_REF,) * 5, (MediaRef(0, "jobs/job-1/input/0.gif", "image/gif", 4),)):
        site = Site(vm_routes_=vm_routes([answered("ref-spike-301030/history_edit_two_refs.json")]))
        with pytest.raises(ValueError, match="takes 1 to 4"):
            run(site, job("image_to_image", inputs=inputs))
        assert stream_bodies(site) == []


@mutation("packages/provider-muse/src/relay_muse/engine/chat.py", '            "approval_id": aid, "decision": "deny",',
          '            "approval_id": aid, "decision": "approve",', expect="deny")
def test_an_approval_the_agent_asks_is_denied():
    def with_approval(req):
        vm = site.vms[0]
        if not any(r.path.startswith("/approvals/") for r in vm.requests):
            vm.push({"event": "approvals.snapshot", "payload": {"approvals": [
                {"approval_id": "ap-7", "status": "pending", "message_revision": 2}]}})
            return []
        return history("job_j01.json", req.path.rsplit("session_id=", 1)[1])
    site = Site(vm_routes_=vm_routes([with_approval]))
    result, ctx = run(site)
    assert isinstance(result.job, Completed)
    (deny,) = [r for r in site.vms[0].requests if r.path.startswith("/approvals/")]
    assert (deny.service, deny.path) == (1, "/approvals/ap-7/decide")
    assert deny.json() == {"approval_id": "ap-7", "decision": "deny", "expected_message_revision": 2}, "deny"


NO_VM = {"status": "none"}
UNAVAILABLE = {"status": "unavailable", "vm_resolution_issue": {"kind": "retryable"}}
LEASED = {"status": "assigned", "gatewayUrl": SESSION_OK["endpoint_url"], "vmName": SESSION_OK["vm_id"]}
SUBMIT_LEASE = "            return _result(Retry(errors.VM_NOT_READY), lease_effect(exc, self.settings, now))"


@mutation(WEB_FILE, LEASE_CALL, "        pass\n    vm, endpoint = workspace(info)", expect="Completed")
def test_a_job_on_a_new_account_leases_its_workspace_wakes_it_and_goes_on():
    asleep = {**SESSION_OK, "vm_state": "SUSPENDED"}
    site = Site({"GET /api/session": [(200, NO_VM, {}), (200, asleep, {}), (200, SESSION_OK, {})],
                 "POST /api/hatch/lease-vm": (200, LEASED, {})}, vm_routes_=vm_routes([answered("job_j01.json")]))
    result, _ = run(site)
    assert isinstance(result.job, Completed), result
    assert site.paths()[:4] == ["GET /api/session", "POST /api/hatch/lease-vm", "GET /api/session",
                                "POST /api/hatch/vm/wake"], "lease-vm then wake: %s" % site.paths()


@mutation(SUBMIT_FILE, SUBMIT_LEASE, "            return _result(Retry(errors.VM_NOT_READY))", expect="rest or end")
def test_a_job_whose_lease_fails_sends_nothing_and_rests_or_ends_the_account():
    for status, effect in ((503, Cooldown), (404, Dead)):
        site = Site({"GET /api/session": (200, NO_VM, {}),
                     "POST /api/hatch/lease-vm": (status, {"code": "lease_rejected"}, {})})
        result, ctx = run(site)
        assert result.job == Retry("muse.vm_not_ready") and ctx.calls == [] and site.vms == []
        assert [type(e) for e in result.account] == [effect], "rest or end: %s" % (result.account,)
        assert result.account[0].reason == "muse answered %d (lease_rejected) to the workspace lease" % status


UNAVAILABLE_RESULT = "unavailable_effect(exc, self.settings, now))"


@mutation(SUBMIT_FILE, UNAVAILABLE_RESULT, "Cooldown('x', now))", expect="unavailable from here")
def test_a_job_whose_workspace_is_unavailable_from_here_rests_the_account_and_leases_nothing():
    site = Site({"GET /api/session": (200, UNAVAILABLE, {})})
    result, ctx = run(site)
    assert result.job == Retry("muse.vm_not_ready") and ctx.calls == [] and site.vms == []
    assert site.paths() == ["GET /api/session"]
    assert [type(e) for e in result.account] == [Cooldown] and "unavailable from here" in result.account[0].reason


# ---- only files whose names were not there before the send (live 30/10: jobs aae305b4 and 1c7ddda2) -----------------
UNLINKED = "new-files-301030/history_unlinked_image.json"
LINKED_VIDEO = "new-files-301030/history_linked_video.json"
EDITED = "workspace/your_files/media-generation-fox-snow-watercolor-9x16-0-d74ae617-0900-46fb-aef2-46d9edd0add4.webp"
KEPT = "            self.before = frozenset(f.ref for f in listed)\n"
NOT_SENT = "            if listed is None:\n                raise Unlisted("
LIST_FAILED = "            if not isinstance(entries, list):\n                return None\n"
TOO_MANY = "    if len(files) > most:\n"
GRACE = "                elif self.empty is None or self.deps.clock() - self.empty < grace:\n"
MEDIA_EXT = (".webp", ".png", ".jpg", ".jpeg", ".mp4")


def names(listing) -> list[str]:
    """The names a listing keeps: its image and video files."""
    return sorted(f + "/" + e["name"] for f, entries in listing.items() for e in entries
                  if e["type"] == "file" and e["name"].lower().endswith(MEDIA_EXT))


def edit_job():
    return job("image_to_image", inputs=(IMAGE_REF,), ratio="9:16")


@mutation(SUBMIT_FILE, KEPT, "            self.before = frozenset()\n", expect="the names before the send are kept")
def test_an_edit_muse_saved_without_showing_it_is_delivered_and_the_names_before_the_send_kept():
    """Job aae305b4: "Done — one 9:16 watercolor version, kept private in your workspace.", no card, the WebP in
    your_files."""
    before, after = folders()
    site = Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=(before, after)))
    result, ctx = run(site, edit_job())
    assert isinstance(result.job, Completed), "the unshown image is the job's: %s" % (result.job,)
    (out,) = result.job.outputs
    assert (out.ref, out.mime, out.meta.get("last")) == (EDITED, "image/webp", True)
    sid = chat_of(site)
    assert ctx.calls[0] == ("report_ref", sid, {"session_id": sid, "before": names(before)}), \
        "the names before the send are kept"
    assert len(names(before)) == 9 and EDITED not in names(before)
    assert result.account == (Success(),)
    lists = [r.json()["path"] for r in site.vms[0].requests if r.path == "/fs/list"]
    assert lists[:2] == ["workspace/imagine_media", "workspace/your_files"], "listed before the send"
    assert len(lists) >= 4, "listed again at the verdict"


@mutation(SUBMIT_FILE, NOT_SENT, "            if False:\n                raise Unlisted(", expect="not sent")
@mutation(CHAT_FILE, LIST_FAILED, "            if not isinstance(entries, list):\n                continue\n",
          expect="not sent")
@mutation(CHAT_FILE, TOO_MANY, "    if False:\n", expect="more names than are kept: not sent")
def test_no_listing_before_the_send_sends_nothing_and_the_job_goes_back_unpunished():
    _, after = folders()
    for over, why in (({}, "a folder answers 404"), ({"media_max_files": 3}, "more names than are kept")):
        site = Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=({} if not over else folders()[0], after)))
        result, ctx = run(site, edit_job(), **over)
        assert result.job == Retry("muse.workspace_unlisted"), "%s: not sent: %s" % (why, result.job)
        assert stream_bodies(site) == [] and ctx.calls == [], "%s: not sent" % why
        assert [type(e) for e in result.account] == [Success, Cooldown], "no strike: %s" % (result.account,)
    from relay_core.errors import rule
    unlisted = rule("muse.workspace_unlisted")
    assert not (unlisted.counts_attempt or unlisted.terminal or unlisted.refund or unlisted.free_retries)


def test_a_file_that_was_there_before_the_send_is_never_taken_shown_rewritten_or_not():
    same = folders("2099-01-01T00:00:00.000Z")          # nothing new since the send
    result, _ = run(Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=same)), edit_job())
    assert isinstance(result.job, Fail) and result.job.code == "muse.refused", result.job
    before, after = folders()
    old_card = {"name": "ocean_waves_beach_5s.mp4", "type": "file", "size": 3708901,
                "modifiedAt": "2026-10-30T05:29:45.756Z"}
    before["workspace/your_files"].append(old_card)
    after["workspace/your_files"].append(dict(old_card, modifiedAt="2026-10-30T11:59:00.000Z"))
    shown_old = Site(vm_routes_=vm_routes([answered("ref-spike-301030/history_your_files.json")],
                                          folders=(before, after)))
    result, _ = run(shown_old, video_job())
    assert isinstance(result.job, Fail) and result.job.code == "muse.refused", "an old card: %s" % (result.job,)
    before, after = folders()
    rewritten = next(e for e in after["workspace/your_files"] if "watercolor" in e["name"] and e["name"].endswith("p"))
    before["workspace/your_files"].append(dict(rewritten, modifiedAt="2026-10-30T05:00:00.000Z"))
    result, _ = run(Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=(before, after))), edit_job())
    assert isinstance(result.job, Fail), "a file rewritten since the send: %s" % (result.job,)


@mutation(SUBMIT_FILE, GRACE, "                elif False:\n", expect="lands within the grace")
def test_a_file_that_lands_within_the_grace_is_delivered_and_none_at_all_is_refused_after_it():
    before, after = folders()
    calls = []

    def listing(req):
        calls.append(req)
        path = req.json()["path"]
        return 200, {"path": path, "entries": (before if len(calls) <= 6 else after).get(path, [])}
    site = Site(vm_routes_=vm_routes([answered(UNLINKED)], overrides={"/fs/list": listing}))
    result, _ = run(site, edit_job(), files_grace_s=5)
    assert isinstance(result.job, Completed), "a file that lands within the grace: %s" % (result.job,)
    assert [o.ref for o in result.job.outputs] == [EDITED]
    late = Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=(before, before)))
    result, _ = run(late, edit_job(), files_grace_s=0.3)
    assert isinstance(result.job, Fail) and result.job.code == "muse.refused", result.job


def test_a_video_only_named_in_the_answer_is_delivered_the_cut_one_not_the_raw():
    """Job 1c7ddda2: muse made a 10 s raw video, cut it to 5 s, answered with a sandbox:// link and no card."""
    raw = ("workspace/your_files/media-generation-a-paper-boat-0-1a2b3c4d.mp4", "2026-10-30T11:51:40.000Z")
    cut = ("workspace/your_files/paper_boat_rainy_gutter_5s.mp4", "2026-10-30T11:52:05.000Z")
    site = Site(vm_routes_=vm_routes([answered(LINKED_VIDEO)], folders=folders("2026-10-30T11:00:00.000Z", raw, cut)))
    result, _ = run(site, video_job())
    assert isinstance(result.job, Completed), result.job
    assert [(o.ref, o.mime) for o in result.job.outputs] == [(cut[0], "video/mp4")], "never more than paid"


def test_new_unshown_images_beyond_n_are_never_delivered():
    added = [("workspace/imagine_media/a-fox-%d.webp" % i, "2026-10-30T11:5%d:00.000Z" % i) for i in range(3)]
    site = Site(vm_routes_=vm_routes([answered(UNLINKED)], folders=folders("2026-10-30T11:00:00.000Z", *added)))
    result, ctx = run(site, edit_job())
    assert isinstance(result.job, Completed) and len(result.job.outputs) == 1
    assert result.job.outputs[0].ref == "workspace/imagine_media/a-fox-2.webp"
    assert any("muse made 4 images, 1 were paid" in text for _, text in ctx.events)
