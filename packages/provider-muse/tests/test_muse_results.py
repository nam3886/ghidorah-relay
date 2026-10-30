"""The poll of a taken job, the reconcile of a lost send and the fetch, against the in-process muse."""

import asyncio
from datetime import timedelta

import pytest
from musekit import NOW, Ctx, Site, history, job, settings, vm_routes

from relay_core.providers import Done, Failed, Found, Output, OutputGone, Pending, StillUnknown
from relay_core.testing.mutation import mutation
from relay_muse.engine import results

RESULTS_FILE = "packages/provider-muse/src/relay_muse/engine/results.py"
SID = "11111111-2222-4333-8444-555555555555"


def _run(coro_fn, site, j, now=NOW, **over):
    return asyncio.run(coro_fn(Ctx(), j, settings(**over), deps=site.deps(now=lambda: now)))


def poll(site, j, now=NOW, **over):
    return _run(results.poll, site, j, now, **over)


def taken(n=1, state=None, **kw):
    return job(external_ref=SID, state={"session_id": SID, **(state or {})}, n=n, **kw)


def test_a_poll_delivers_the_results_with_no_send():
    site = Site(vm_routes_=vm_routes([history("job_j11.json", SID)]))
    result = poll(site, taken(n=4))
    assert isinstance(result.outcome, Done) and len(result.outcome.outputs) == 4
    vm = site.vms[0]
    assert vm.paths() == ["/client/register-capabilities",
                          "/chat/history?limit=40&transcript_mode=messages&session_id=" + SID]


@mutation(RESULTS_FILE, '    if state.get("seen") != seen:\n', "    if False:\n", expect="settled across two polls")
def test_a_refusal_is_failed_only_when_two_polls_settle_apart():
    words = history("job_j07.json", SID)
    first = poll(Site(vm_routes_=vm_routes([words])), taken())
    assert isinstance(first.outcome, Pending) and first.outcome.answered
    patch = dict(first.outcome.state_patch)
    assert patch == {"seen": [0, 0, True, False, len(words)], "seen_at": NOW.isoformat()}
    too_soon = poll(Site(vm_routes_=vm_routes([words])), taken(state=patch), NOW + timedelta(seconds=0.01))
    assert isinstance(too_soon.outcome, Pending) and too_soon.outcome.state_patch["seen_at"] == NOW.isoformat()
    later = poll(Site(vm_routes_=vm_routes([words])), taken(state=patch), NOW + timedelta(seconds=1))
    assert isinstance(later.outcome, Failed) and later.outcome.code == "muse.refused"
    changed = {"seen": [0, 0, True, False, 1], "seen_at": NOW.isoformat()}
    again = poll(Site(vm_routes_=vm_routes([words])), taken(state=changed), NOW + timedelta(seconds=1))
    assert isinstance(again.outcome, Pending), "settled across two polls"


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


@mutation(RESULTS_FILE, "    if reply.status == 404:\n        raise OutputGone(",
          "    if False:\n        raise OutputGone(", expect="answered 404")
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


REF_CHECK = ('    return isinstance(ref, str) and pattern is not None and bool(pattern.fullmatch(ref))'
             ' and ".." not in ref')


@mutation("packages/provider-muse/src/relay_muse/engine/chat.py", REF_CHECK, "    return isinstance(ref, str)",
          expect="never leaves the media folder")
def test_a_ref_outside_the_media_folder_is_never_read():
    for ref, mime in (("workspace/imagine_media/../secrets.txt", "image/webp"), ("/etc/passwd", "image/webp"),
                      ("workspace/other/a.webp", "image/webp"), ("workspace/your_files/../secrets.txt", "video/mp4"),
                      ("workspace/your_files/a/b.mp4", "video/mp4"), ("workspace/your_files/a.webp", "image/webp")):
        site = Site()
        with pytest.raises(OutputGone):
            fetch(site, ref, mime)
        assert site.calls == [], "never leaves the media folder"


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
