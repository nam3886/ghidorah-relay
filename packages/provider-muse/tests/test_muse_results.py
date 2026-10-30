"""The poll of a taken job, the reconcile of a lost send and the fetch, against the in-process muse."""

import asyncio
from datetime import timedelta

import pytest
from musekit import NOW, Ctx, Site, folders, history, job, settings, vm_routes

from relay_core.providers import Done, Failed, Found, Output, OutputGone, Pending, Rest, StillUnknown
from relay_core.testing.mutation import mutation
from relay_muse.engine import results

RESULTS_FILE = "packages/provider-muse/src/relay_muse/engine/results.py"
SID = "11111111-2222-4333-8444-555555555555"


def _run(coro_fn, site, j, now=NOW, **over):
    return asyncio.run(coro_fn(Ctx(), j, settings(**over), deps=site.deps(now=lambda: now)))


def poll(site, j, now=NOW, **over):
    return _run(results.poll, site, j, now, **over)


def taken(n=1, state=None, **kw):
    return job(external_ref=SID, state={"session_id": SID, "before": [], **(state or {})}, n=n, **kw)


def refused_after_grace(routes, make_job, state, at):
    """Two polls of a settled chat with no new file: the first records the empty read (`empty_at`), the second, past
    `muse.files_grace_s`, refuses. (the result, the last poll's site)"""
    first = poll(Site(vm_routes_=routes()), make_job(state), at)
    assert isinstance(first.outcome, Pending), "no refusal on the first empty read: %s" % (first.outcome,)
    assert first.outcome.state_patch["empty_at"] == at.isoformat()
    site = Site(vm_routes_=routes())
    return poll(site, make_job({**state, **first.outcome.state_patch}), at + timedelta(seconds=1)), site


def test_a_poll_delivers_the_results_with_no_send():
    site = Site(vm_routes_=vm_routes([history("job_j11.json", SID)]))
    result = poll(site, taken(n=4))
    assert isinstance(result.outcome, Done) and len(result.outcome.outputs) == 4
    vm = site.vms[0]
    assert vm.paths() == ["/client/register-capabilities",
                          "/chat/history?limit=40&transcript_mode=messages&session_id=" + SID,
                          "/fs/list", "/fs/list"]


SETTLED = '    since = _at(state.get("seen_at")) if state.get("seen") == seen else None\n'


@mutation(RESULTS_FILE, SETTLED, '    since = _at(state.get("seen_at"))\n', expect="settled across two polls")
def test_a_refusal_is_failed_only_when_two_polls_settle_apart():
    words = history("job_j07.json", SID)
    first = poll(Site(vm_routes_=vm_routes([words])), taken())
    assert isinstance(first.outcome, Pending) and first.outcome.answered
    patch = dict(first.outcome.state_patch)
    assert patch == {"seen": [0, 0, True, False, len(words)], "seen_at": NOW.isoformat(), "empty_at": None}
    too_soon = poll(Site(vm_routes_=vm_routes([words])), taken(state=patch), NOW + timedelta(seconds=0.01))
    assert isinstance(too_soon.outcome, Pending) and too_soon.outcome.state_patch["seen_at"] == NOW.isoformat()
    later, _ = refused_after_grace(lambda: vm_routes([words]), lambda st: taken(state=st), patch,
                                   NOW + timedelta(seconds=1))
    assert isinstance(later.outcome, Failed) and later.outcome.code == "muse.refused"
    changed = {"seen": [0, 0, True, False, 1], "seen_at": NOW.isoformat()}
    again = poll(Site(vm_routes_=vm_routes([words])), taken(state=changed), NOW + timedelta(seconds=1))
    assert isinstance(again.outcome, Pending) and again.outcome.state_patch["empty_at"] is None, \
        "settled across two polls"


NO_VIDEO_HERE = "region-301030/history_video_unavailable.json"
POLL_NO_VIDEO = '        if kind == "video" and video_unavailable_here(reading.text):'
POLL_DROP = "        await _drop_chat(ctx, settings, deps, session)\n"


def deleted(site: Site) -> list[dict]:
    return [r.json() for vm in site.vms for r in vm.requests if r.path == "/session/delete"]


@mutation(RESULTS_FILE, POLL_NO_VIDEO, "        if False:", expect="the account's code")
@mutation(RESULTS_FILE, POLL_DROP, "", expect="deleted after a verdict with no output")
def test_a_poll_that_reads_muse_refusing_the_account_ends_the_job_rests_the_video_pool_and_deletes_the_chat():
    words = history(NO_VIDEO_HERE, SID)
    video = job("text_to_video", external_ref=SID, state={"session_id": SID, "before": []})
    first = poll(Site(vm_routes_=vm_routes([words])), video)
    assert isinstance(first.outcome, Pending)
    state = {"session_id": SID, "before": [], **first.outcome.state_patch}

    def video_job(st):
        return job("text_to_video", external_ref=SID, state=st)
    later, site = refused_after_grace(lambda: vm_routes([words]), video_job, state, NOW + timedelta(seconds=1))
    assert isinstance(later.outcome, Failed) and later.outcome.code == "muse.video_unavailable_on_accounts", \
        "the account's code: %s" % (later.outcome,)
    assert later.outcome.state_patch == {"refused_by": ["acc-1"]}
    (rest,) = later.effects
    assert isinstance(rest, Rest) and rest.pool == "video" and rest.until == NOW + timedelta(seconds=2, days=7)
    assert deleted(site) == [{"method": "/api/session/delete", "session_id": SID}], \
        "deleted after a verdict with no output"
    j07 = history("job_j07.json", SID)
    seen = poll(Site(vm_routes_=vm_routes([j07])), taken()).outcome.state_patch
    prompt, refused = refused_after_grace(lambda: vm_routes([j07]), lambda st: taken(state=st), dict(seen),
                                          NOW + timedelta(seconds=1))
    assert isinstance(prompt.outcome, Failed) and prompt.outcome.code == "muse.refused" and prompt.effects == ()
    assert len(deleted(refused)) == 1
    broken, _ = refused_after_grace(
        lambda: vm_routes([words], overrides={"/session/delete": ("reset", "gone")}), video_job, state,
        NOW + timedelta(seconds=1))
    assert broken.outcome.code == "muse.video_unavailable_on_accounts"


def test_a_poll_that_cannot_read_is_not_an_answer():
    site = Site({"GET /api/session": OSError("down")})
    result = poll(site, taken())
    assert isinstance(result.outcome, Pending) and not result.outcome.answered
    assert result.outcome.next_in_s == 30
    nothing = poll(Site(), job())
    assert isinstance(nothing.outcome, Pending) and not nothing.outcome.answered
    garbled = Site(vm_routes_=vm_routes(overrides={"/chat/history?limit=40": (200, {"no": "history"})}))
    assert not poll(garbled, taken()).outcome.answered


@mutation(RESULTS_FILE, "    if events:\n        return Found(", "    if events is not None:\n        return Found(",
          expect="an empty chat proves nothing")
def test_reconcile_finds_a_chat_with_our_message_and_never_says_not_sent():
    found = _run(results.reconcile, Site(vm_routes_=vm_routes([history("job_j01.json", SID)])), taken())
    assert found == Found(SID, {"session_id": SID})
    empty = _run(results.reconcile, Site(vm_routes_=vm_routes([[]])), taken())
    assert empty == StillUnknown(answered=True), "an empty chat proves nothing"
    unknown = Site(vm_routes_=vm_routes(overrides={"/chat/history?limit=40": (404, {"error": {"code": "not_found"}})}))
    assert _run(results.reconcile, unknown, taken()) == StillUnknown(answered=True)
    broken = Site({"POST /api/hatch/token": (500, {}, {})})
    assert _run(results.reconcile, broken, taken()) == StillUnknown(answered=False)
    assert _run(results.reconcile, Site(), job(external_ref="not-a-chat")) == StillUnknown()


REF = "workspace/imagine_media/media-generation-a-fox-0-1a2b.webp"


def fetch(site, ref=REF, mime="image/webp"):
    async def go():
        out = Output(ref, mime, 0)
        return [chunk async for chunk in results.fetch(Ctx(), out, settings(), deps=site.deps())]
    return asyncio.run(go())


@mutation(RESULTS_FILE, "        if reply.status == 404:\n            raise OutputGone(",
          "        if False:\n            raise OutputGone(", expect="answered 404")
def test_the_fetch_reads_the_file_in_the_tunnel_and_a_missing_one_is_gone():
    data = b"RIFF" + b"w" * 300_000
    site = Site(vm_routes_=vm_routes(files={REF: data}))
    assert b"".join(fetch(site)) == data
    assert site.vms[0].paths()[-1] == "/fs/raw/" + REF
    video = "workspace/your_files/ocean_waves_beach_5s.mp4"       # muse's videos since 30/10
    site = Site(vm_routes_=vm_routes(files={video: data}))
    assert b"".join(fetch(site, video, "video/mp4")) == data and site.vms[0].paths()[-1] == "/fs/raw/" + video
    with pytest.raises(OutputGone) as gone:
        fetch(Site())
    assert gone.value.code == "muse.output_missing"
    with pytest.raises(RuntimeError, match="500"):
        fetch(Site(vm_routes_=vm_routes(overrides={"/fs/raw/": (500, b"")})))


FETCH_DELETE = "        if out.meta.get(LAST) and is_session(session):"
CHAT_FILE = "packages/provider-muse/src/relay_muse/engine/chat.py"
BEST_EFFORT = "    except Exception as e:      # noqa: BLE001 - whatever muse answers, the delete never changes the job"
NARROW = "    except ValueError as e:      # noqa: BLE001 - whatever muse answers, the delete never changes the job"


def fetch_out(site, out, **over):
    async def go():
        return [chunk async for chunk in results.fetch(Ctx(), out, settings(**over), deps=site.deps())]
    return asyncio.run(go())


@mutation(CHAT_FILE, BEST_EFFORT, NARROW, expect="malformed delete answer")
def test_a_malformed_answer_to_the_delete_after_the_last_file_changes_nothing():
    data = b"RIFF" + b"w" * 1000
    last = Output(REF, "image/webp", 0, meta={"session_id": SID, "last": True})
    for body in (b"not json", b"[" * 100_000):      # a malformed 200 (the second too deep to decode)
        site = Site(vm_routes_=vm_routes(files={REF: data}, overrides={"/session/delete": (200, body)}))
        try:
            got = fetch_out(site, last)
        except Exception as e:      # noqa: BLE001 - the failure is the finding
            got = e
        assert got == [data], "malformed delete answer: %r" % (got,)
        assert len(deleted(site)) == 1


@mutation(RESULTS_FILE, FETCH_DELETE, "        if False:", expect="deleted once its last file is read")
def test_the_side_chat_is_deleted_once_the_jobs_last_file_is_read_and_a_failed_delete_changes_nothing():
    data = b"RIFF" + b"w" * 1000
    first, last = Output(REF, "image/webp", 0, meta={"session_id": SID}), \
        Output(REF, "image/webp", 1, meta={"session_id": SID, "last": True})
    site = Site(vm_routes_=vm_routes(files={REF: data}))
    assert fetch_out(site, first) == [data] and deleted(site) == [], "not before the last file"
    site = Site(vm_routes_=vm_routes(files={REF: data}))
    assert fetch_out(site, last) == [data]
    assert site.vms[0].paths()[-2:] == ["/fs/raw/" + REF, "/session/delete"], "deleted once its last file is read"
    assert deleted(site) == [{"method": "/api/session/delete", "session_id": SID}]
    busy = (409, {"ok": False, "error": {"code": "conflict", "message": "session has an active message"}})
    for answer, tries in ((busy, 2), ((500, {}), 1), (("reset", "gone"), 1), ("silent", 1)):
        site = Site(vm_routes_=vm_routes(files={REF: data}, overrides={"/session/delete": answer}))
        assert fetch_out(site, last, call_timeout_s=0.3) == [data], answer
        assert len(deleted(site)) == tries, answer
    missing = Site()
    with pytest.raises(OutputGone):
        fetch_out(missing, last)
    assert deleted(missing) == [], "a file not read deletes nothing"
    odd = Site(vm_routes_=vm_routes(files={REF: data}))
    assert fetch_out(odd, Output(REF, "image/webp", 0, meta={"session_id": "../x", "last": True})) == [data]
    assert deleted(odd) == []


REF_CHECK = '    return folder + "/" in dirs and bool(_NAME.fullmatch(name))'
DOTS = '    if not isinstance(ref, str) or ".." in ref or mime.split("/", 1)[0] not in ("image", "video"):'


@mutation("packages/provider-muse/src/relay_muse/engine/chat.py", REF_CHECK, "    return True",
          expect="never leaves the media folder")
@mutation("packages/provider-muse/src/relay_muse/engine/chat.py", DOTS,
          '    if not isinstance(ref, str) or mime.split("/", 1)[0] not in ("image", "video"):',
          expect="never leaves the media folder")
def test_a_ref_outside_the_media_folder_is_never_read():
    for ref, mime in (("workspace/imagine_media/../secrets.txt", "image/webp"), ("/etc/passwd", "image/webp"),
                      ("workspace/other/a.webp", "image/webp"), ("workspace/your_files/../secrets.txt", "video/mp4"),
                      ("workspace/your_files/a/b.mp4", "video/mp4"), ("workspace/your_files/a.txt", "text/plain"),
                      ("workspace/your_files/a..mp4", "video/mp4")):
        site = Site()
        with pytest.raises(OutputGone):
            fetch(site, ref, mime)
        assert site.calls == [], "never leaves the media folder"
    site = Site()
    with pytest.raises(OutputGone):
        fetch_out(site, Output("workspace/your_files/a.webp", "image/webp", 0),
                  media_dirs=["workspace/imagine_media/"])
    assert site.calls == [], "never leaves the media folder of the setting"


LENGTH_CHECK = "        if abs(length - float(paid[\"duration\"])) > LENGTH_SLACK_S:"


@mutation(RESULTS_FILE, LENGTH_CHECK, "        if False:", expect="a 10 s video paid as 5 s")
def test_a_video_off_its_paid_length_or_ratio_is_delivered_and_noted(monkeypatch, caplog):
    from relay_muse.engine.results import video_off
    probed = {"streams": [{"codec_type": "video", "width": 1280, "height": 720}], "format": {"duration": "10.000"}}
    assert video_off(probed, {"duration": 5, "ratio": "16:9"}) == "10.00 s, 5 s paid", "a 10 s video paid as 5 s"
    assert video_off(probed, {"duration": 10, "ratio": "9:16"}) == "1280x720, 9:16 paid"
    assert video_off(probed, {"duration": 10, "ratio": "16:9"}) == "" and video_off({}, {"duration": 5}) == ""

    async def fake_probe(data):
        return probed
    monkeypatch.setattr(results, "probe", fake_probe)
    ref = "workspace/imagine_media/a-video-0-1a2b.mp4"
    site = Site(vm_routes_=vm_routes(files={ref: b"mp4-bytes"}))
    ctx = Ctx()

    async def go():
        out = Output(ref, "video/mp4", 0, meta={"job_id": "job-7", "duration": 5, "ratio": "16:9"})
        return [c async for c in results.fetch(ctx, out, settings(), deps=site.deps())]
    with caplog.at_level("WARNING"):
        assert asyncio.run(go()) == [b"mp4-bytes"]
    assert any("muse job job-7 output 0: 10.00 s, 5 s paid" in r.getMessage() for r in caplog.records)
    assert any("not what was paid" in text for _, text in ctx.events)


BEFORE_OF = '    before, most = before_of(state), settings.get("muse.media_max_files")\n'
POLL_GRACE = '        if first is None or (now - first).total_seconds() < settings.get("muse.files_grace_s"):\n'
EDITED = "workspace/your_files/media-generation-fox-snow-watercolor-9x16-0-d74ae617-0900-46fb-aef2-46d9edd0add4.webp"


def names(listing):
    return sorted(f + "/" + e["name"] for f, entries in listing.items() for e in entries if e["type"] == "file")


@mutation(RESULTS_FILE, BEFORE_OF, '    before, most = frozenset(), settings.get("muse.media_max_files")\n',
          expect="a name from before the send")
@mutation(RESULTS_FILE, POLL_GRACE, "        if first is None:\n", expect="within the grace")
def test_a_poll_takes_only_files_whose_names_were_not_there_before_the_send_and_waits_out_the_grace():
    """A job past its hold: the names listed before the send ride on `provider_state.before`."""
    words = history("new-files-301030/history_unlinked_image.json", SID)
    before, after = folders()
    settled = {"seen": [0, 0, True, False, len(words)], "seen_at": NOW.isoformat()}
    later = NOW + timedelta(seconds=1)

    def edit(state):
        return taken(state={**settled, **state}, operation="image_to_image")
    result = poll(Site(vm_routes_=vm_routes([words], folders=(after, after))), edit({"before": names(before)}), later)
    assert isinstance(result.outcome, Done), "the poll takes the unshown image: %s" % (result.outcome,)
    assert [(o.ref, o.mime) for o in result.outcome.outputs] == [(EDITED, "image/webp")]
    first_old = poll(Site(vm_routes_=vm_routes([words], folders=(after, after))), edit({"before": names(after)}),
                     later)
    assert isinstance(first_old.outcome, Pending), "a name from before the send: %s" % (first_old.outcome,)
    old, _ = refused_after_grace(lambda: vm_routes([words], folders=(after, after)),
                                 lambda st: edit(st), {"before": names(after)}, later)
    assert isinstance(old.outcome, Failed) and old.outcome.code == "muse.refused", "a name from before the send"
    # an empty read, then the file lands within the grace: delivered
    first = poll(Site(vm_routes_=vm_routes([words], folders=(before, before))), edit({"before": names(before)}), later)
    assert isinstance(first.outcome, Pending) and first.outcome.state_patch["empty_at"] == later.isoformat()
    state = {"before": names(before), **first.outcome.state_patch}
    landed = poll(Site(vm_routes_=vm_routes([words], folders=(after, after))), edit(state),
                  later + timedelta(seconds=0.01), files_grace_s=60)
    assert isinstance(landed.outcome, Done), "a file that lands within the grace: %s" % (landed.outcome,)
    still = poll(Site(vm_routes_=vm_routes([words], folders=(before, before))), edit(state),
                 later + timedelta(seconds=0.01), files_grace_s=60)
    assert isinstance(still.outcome, Pending), "nothing yet, within the grace: %s" % (still.outcome,)
    assert still.outcome.state_patch["empty_at"] == later.isoformat()


VERDICT_UNLISTED = "    if listed is None:\n        return r, None\n"


@mutation(CHAT_FILE, VERDICT_UNLISTED, "    if listed is None:\n        return r, found\n",
          expect="no listing at the verdict")
def test_a_poll_with_no_names_from_before_the_send_or_no_listing_now_gives_no_verdict():
    words = history("new-files-301030/history_unlinked_image.json", SID)
    _, after = folders()
    settled = {"seen": [0, 0, True, False, len(words)], "seen_at": NOW.isoformat()}
    later = NOW + timedelta(seconds=1)
    no_names = job("image_to_image", external_ref=SID, state={"session_id": SID, **settled})
    site = Site(vm_routes_=vm_routes([words], folders=(after, after)))
    assert isinstance(poll(site, no_names, later).outcome, Pending), "no names kept: never a verdict"
    unlisted = Site(vm_routes_=vm_routes([words], folders=({}, {})))
    result = poll(unlisted, taken(state={**settled, "empty_at": NOW.isoformat()}, operation="image_to_image"),
                  later + timedelta(seconds=60))
    assert isinstance(result.outcome, Pending) and result.outcome.state_patch["empty_at"] is None, \
        "no listing at the verdict: no verdict on this read"
