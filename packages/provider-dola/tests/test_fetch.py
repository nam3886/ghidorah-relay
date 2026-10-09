"""Fetch (contracts 5.4.1 "fetch"; coverage RES14, RES26, RES16): direct first, then through the account proxy; master
quality through the cell's own `fallback_api` and the qAAB token; silent fallback; images take only the clean link."""

import asyncio
import base64
import contextlib
import hashlib
import http.server
import json
import logging
import secrets
import threading
import tracemalloc

import httpx
import pytest
from kit_b import (
    CONV,
    Ctx,
    FakeTransport,
    Resp,
    Settings,
    account,
    chain_files,
    chain_with,
    http_for,
    job,
    load_case,
    user,
)

from relay_core import net
from relay_core.net import FetchFailed, UnsafeUrl
from relay_core.providers import Output
from relay_core.testing.fake_media import Answer, Body, MediaSite, logical_url, public_resolver, seen
from relay_core.testing.mutation import mutation
from relay_dola import credential
from relay_dola.engine import fetch as fetch_mod
from relay_dola.engine import http as http_mod
from relay_dola.engine.fetch import (
    MEDIA_HOSTS,
    MEDIA_HTTP_HOSTS,
    decode_qaab,
    fetch,
    master_hq_url,
    walk_field,
)
from relay_dola.engine.http import CurlTransport, DolaHttp
from relay_dola.engine.poll import poll

FETCH = "packages/provider-dola/src/relay_dola/engine/fetch.py"
NET = "packages/relay-core/src/relay_core/net.py"
HTTP = "packages/provider-dola/src/relay_dola/engine/http.py"

MIB = 1 << 20
PROXIES = {"http": "http://user:pass@proxy.invalid:8080", "https": "http://user:pass@proxy.invalid:8080"}
PROXY_URL = PROXIES["https"]
# media links as dola gives them: plain http on the video CDN, https on the image CDN
RENDITION = "http://v16-dola.dola.com/rendition-%s.mp4"
IMAGE = "https://p16-flow-image-sign.ibyteimg.com/raw.png"
MASTER_URL = "http://v16-dola.dola.com/master/h264-720p.mp4?sig=abc123"
# a qAAB token and its seed, made with v4's own key schedule (resolver._decode_qaab round-trips it): port parity
SEED = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8w"
V4_TOKEN = ("qAABANYTvgYokgwQE6gZbOf471LOxNMuc4DtnFxgHDdQs2rRcMI4+ZQD8iaV8vay/yIb2X5zwEcMnBM9ESyXXzu+3x8=")
V4_URL = "https://cdn.example.com/master/h264-720p.mp4?sig=abc123"
# the same schedule applied to MASTER_URL
TOKEN = ("qAABAKeh1olSrJJds+S+iT/5gayXR6Ng7rANHuFZuWlTVtB9eHVPJdaMEN0A0baCGXgWyfLBxkRtdry+6vUA+ujEJIA=")


RESET = httpx.ReadError("reset")     # a connection reset as the transport reports it


def run(coro):
    return asyncio.run(coro)


def media_route(request):
    """What the CDN serves for the links these tests use."""
    url = logical_url(request)
    if url == MASTER_URL:
        return b"MASTER-BYTES"
    for vid in ("v1", "v2", "v9"):
        if url == RENDITION % vid:
            return b"RENDITION-" + vid.encode()
    return url == IMAGE and b"\x89PNG" or 404


def media_ok() -> MediaSite:
    return MediaSite(media_route, media_route)


def collect(ctx, out, settings=None, transport=None, media=None):
    """The bytes of fetch(); the dola API goes through `transport`, the media through `media`."""
    transport = transport or FakeTransport()
    media = media or media_ok()
    http = http_for(transport, ctx.account)

    async def go():
        return b"".join([c async for c in fetch(ctx, out, settings or Settings(), http=http, media_transport=media,
                                                  media_resolver=public_resolver)])
    return run(go()), transport


def video_out(vid="v1", url=RENDITION % "v1", conversation=CONV):
    return Output(url, "video/mp4", 0, vid, {"conversation_id": conversation, "source": "main_url"})


# ---- direct first, proxy only as the fallback (RES26): the route rules themselves are proven in the core ------------
def test_the_download_goes_direct_first_and_never_touches_the_proxy_when_direct_works():
    media = MediaSite(b"video", b"other")
    body, transport = collect(Ctx(), video_out(), media=media)
    assert body == b"video" and media.routes == [None], "the media download went through the account proxy first"
    assert not transport.gets, "the media link must not go through the cookie transport"
    assert not {"cookie", "authorization"} & seen(media)[0]["headers"], "the signed CDN link carries no cookie"


def test_a_failed_direct_download_falls_back_to_the_account_proxy():
    media = MediaSite(403, b"video")
    body, _ = collect(Ctx(), video_out(), media=media)
    assert body == b"video" and media.routes == [None, PROXY_URL], \
        "a failed direct download does not fall back to the proxy: %r" % media.routes
    assert PROXY_URL == credential.proxy_url(account().proxy)


def test_a_failed_download_with_no_proxy_is_an_error_and_with_both_failing_too():
    with pytest.raises(FetchFailed):
        collect(Ctx(account(proxy=False)), video_out(), media=MediaSite(403))
    with pytest.raises(FetchFailed):
        collect(Ctx(), video_out(), media=MediaSite(500, 500))


def test_an_empty_body_and_a_transport_exception_are_failed_attempts_not_a_video():
    """A 200 with no bytes became a 0-second 'video' once (v4 fa10b65)."""
    with pytest.raises(FetchFailed) as info:
        collect(Ctx(account(proxy=False)), video_out(), media=MediaSite(b""))
    assert info.value.reason == "empty"
    with pytest.raises(FetchFailed):
        collect(Ctx(), video_out(), media=MediaSite(RESET, RESET))
    assert collect(Ctx(), video_out(), media=MediaSite(RESET, b"video"))[0] == b"video"


@mutation(FETCH,
          "                async for part in parts:\n                    handed = True\n"
          "                    yield part\n",
          "                yield b\"\".join([part async for part in parts])\n",
          expect="dola's fetch held the whole file")
def test_a_200_mb_video_streams_through_fetch_in_bounded_memory():
    body = Body(200 * MIB)
    media = MediaSite(body, body)
    http = http_for(FakeTransport())
    digest, size, biggest = hashlib.sha256(), 0, 0

    async def go():
        nonlocal size, biggest
        stream = fetch(Ctx(), video_out(), Settings(), http=http, media_transport=media, media_resolver=public_resolver)
        async with contextlib.aclosing(stream) as parts:
            async for chunk in parts:
                digest.update(chunk)
                size, biggest = size + len(chunk), max(biggest, len(chunk))
    tracemalloc.start()
    try:
        run(go())
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert biggest <= MIB, "dola's fetch held the whole file: a chunk of %d bytes" % biggest
    assert digest.hexdigest() == body.sha256() and size == body.size
    assert peak < 8 * MIB, "dola's fetch held the whole file: peak %.1f MiB" % (peak / MIB)


def test_a_link_outside_dolas_media_hosts_is_never_fetched():
    media = media_ok()
    for url in ("https://evil.example/x.mp4", "http://v16-dola.dola.com.evil.example/x", "ftp://v16-dola.dola.com/x",
                "https://v16-dola.dola.com\\.evil.example/x"):
        with pytest.raises(UnsafeUrl):
            collect(Ctx(), video_out(url=url), media=media)
    assert media.requests == []


def media_refs_of(case) -> list[str]:
    kind = (case.get("job") or {}).get("kind") or "video"
    from kit_b import created_at, fetched_at, first_user_text
    duration = (case.get("job") or {}).get("duration")
    ctx = Ctx()
    j = job(kind=kind, prompt=first_user_text(case["chain"]), marker="", since=created_at(case),
            duration=int(duration) if duration else None)
    http = http_for(FakeTransport({CONV: case["chain"]}), ctx.account)
    outcome = run(poll(ctx, j, ctx.settings, http=http, clock=lambda: fetched_at(case))).outcome
    return [o.ref for o in getattr(outcome, "outputs", [])]


def test_every_media_link_of_every_recorded_chain_is_on_an_allowed_host():
    refs = [r for p in chain_files() for r in media_refs_of(load_case(p.stem))]
    assert len(refs) >= 13, "the recorded chains gave only %d media links" % len(refs)
    for ref in refs:
        assert net.host_allowed(ref, MEDIA_HOSTS, http_hosts=MEDIA_HTTP_HOSTS), \
            "a recorded media link is off the list: %s" % ref.split("?")[0].split("/")[2]


@mutation(FETCH, 'MEDIA_HTTP_HOSTS = ("v16-dola.dola.com", "v19-dola.dola.com")',
          'MEDIA_HTTP_HOSTS = ("dola.com", "ibyteimg.com")', expect="plain http was allowed on a host that is not")
def test_plain_http_is_allowed_on_dolas_video_cdn_hosts_only():
    for ok in ("http://v16-dola.dola.com/a.mp4", "http://v19-dola.dola.com/a.mp4", "https://v16-dola.dola.com/a.mp4",
               "https://p16-flow-image-sign.ibyteimg.com/a.png"):
        assert net.host_allowed(ok, MEDIA_HOSTS, http_hosts=MEDIA_HTTP_HOSTS), ok
    for bad in ("http://www.dola.com/a.mp4", "http://p16-flow-image-sign.ibyteimg.com/a.png",
                "http://v17-dola.dola.com/a.mp4", "http://ibyteimg.com/a.png"):
        assert not net.host_allowed(bad, MEDIA_HOSTS, http_hosts=MEDIA_HTTP_HOSTS), \
            "plain http was allowed on a host that is not dola's video CDN: %s" % bad
    media = media_ok()
    with pytest.raises(UnsafeUrl):
        collect(Ctx(), Output("http://p16-flow-image-sign.ibyteimg.com/raw.png", "image/png", 0, "i", {}), media=media)
    assert media.requests == []


# ---- images: the clean link only ------------------------------------------------------------------------------------
def test_an_image_is_downloaded_from_its_clean_link_and_never_asks_for_a_master():
    out = Output(IMAGE, "image/png", 0, "%s:0" % CONV, {"conversation_id": CONV})
    got, transport = collect(Ctx(), out, Settings({"dola.master_quality": True}))
    assert got == b"\x89PNG" and not transport.posts and not transport.gets


# ---- master quality via qAAB (RES14) --------------------------------------------------------------------------------
def test_decode_qaab_matches_the_v4_key_schedule_and_refuses_what_it_cannot_decode():
    assert decode_qaab(V4_TOKEN, SEED) == V4_URL, "the v4 token no longer decodes"
    assert decode_qaab(TOKEN, SEED) == MASTER_URL
    assert decode_qaab(TOKEN, base64.b64encode(bytes(range(2, 50))).decode()) == "", "a wrong seed gave a URL"
    assert decode_qaab(base64.b64encode(b"\xa8\x00\x01\x00" + b"x" * 7).decode(), SEED) == "", "a ragged payload"
    assert decode_qaab(base64.b64encode(b"\xa8\x00\x01\x00").decode(), SEED) == "", "an empty payload"
    assert len(fetch_mod.QAAB_SALT) == 64, "a salt cut to 16 bytes decodes to garbage"


def test_walk_field_finds_a_field_inside_json_strings_too():
    node = {"video": {"video_model": json.dumps({"fallback_api": "https://a/1", "x": [{"fallback_api": "https://a/2"}]})}}
    assert walk_field(node, "fallback_api") == ["https://a/1", "https://a/2"]
    assert walk_field({"a": 1}, "fallback_api") == []


def master_cell(vid, rendition):
    model = {"video_list": {"720p": {"main_url": base64.b64encode(rendition.encode()).decode()}},
             "fallback_api": "https://vod-urls-mya.byteintlapi.com/video/fplay/1/abc/%s?aid=1&key_seed=XX"
                             "&codec_type=3" % vid}
    return {"type": 2, "video": {"vid": vid, "video_model": json.dumps(model), "duration": "30.0"}}


def conversation(*vids):
    cells = [master_cell(v, RENDITION % v) for v in vids]
    return chain_with([{"index_in_conv": 2, "create_time": 1_790_000_000, "content_type": 9999, "content": json.dumps(
        [{"block_type": 2074, "content": {"creation_block": {"creations": cells}}}])},
        user("Generated video: a red kite [4100]", 1_789_999_900)])


def master_response(vid):
    return Resp(200, {"video_info": {"data": {"video_list": {
        "v_low": {"main_url": "bGl0ZQ==", "bitrate": 100},
        "v_best": {"main_url": TOKEN, "bitrate": 12_000_000}}}}, "key_seed": SEED})


def master_routes(vids, fallback=None):
    routes = {}
    for v in vids:
        routes[v] = master_response(v)

    def route(url, proxies):
        for v in vids:
            if "/%s?" % v in url:
                assert "codec_type=8" in url and "logo_type=unwatermarked" in url and "channel=no" in url, url
                return fallback(v) if fallback else routes[v]
        return None
    return route


def outputs_of(*vids):
    """The real outputs a poll gives for a conversation with these cells (so fetch is tested against them)."""
    transport = FakeTransport({CONV: conversation(*vids)})
    ctx = Ctx()
    result = run(poll(ctx, job(prompt="a red kite", marker="4100"), ctx.settings, http=http_for(transport),
                      clock=lambda: 1_790_000_100.0))
    return result.outcome.outputs, transport


def test_the_master_streams_through_byte_for_byte():
    (out,), _ = outputs_of("v1")
    master = Body(3 * MIB + 5)
    media = MediaSite({MASTER_URL: master}, {MASTER_URL: master})
    chains = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"]))
    got, _ = collect(Ctx(), out, Settings({"dola.master_quality": True}), chains, media)
    assert hashlib.sha256(got).hexdigest() == master.sha256() and len(got) == master.size


def test_master_quality_replaces_the_rendition_with_the_h264_master():
    (out,), _ = outputs_of("v1")
    ctx = Ctx()
    chains = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"]))
    media = media_ok()
    got, transport = collect(ctx, out, Settings({"dola.master_quality": True}), chains, media)
    assert got == b"MASTER-BYTES", "the master was not taken: %r" % got
    fallback = next(g for g in transport.gets if "vod-urls-mya.byteintlapi.com" in g["url"])
    assert "sessionid=fake-sessionid" in fallback["headers"]["cookie"] and fallback["proxies"] == PROXIES
    assert [g["url"] for g in transport.gets] == [fallback["url"]], "the media link went through the cookie transport"
    sent = seen(media)
    assert [r["url"] for r in sent] == [MASTER_URL] and not {"cookie", "authorization"} & sent[0]["headers"]
    assert transport.chain_reads() == [CONV], "the cell is found again by the conversation in meta"


def test_the_master_is_taken_per_video_from_its_own_fallback_api():
    """RES14: with n videos each cell has its own fallback_api; one per conversation gives n copies of the first."""
    outs, _ = outputs_of("v1", "v2")
    assert [o.upstream_id for o in outs] == ["v1", "v2"]
    seen = []

    def per_video(vid):
        seen.append(vid)
        return master_response(vid)
    transport = FakeTransport({CONV: conversation("v1", "v2")}, get_routes=master_routes(["v1", "v2"], per_video))
    collect(Ctx(), outs[0], Settings({"dola.master_quality": True}), transport)
    collect(Ctx(), outs[1], Settings({"dola.master_quality": True}), transport)
    assert seen == ["v1", "v2"], "the master was not asked for per video: %r" % seen


@mutation(FETCH, "        if not trusted_master_host(fallback):", "        if False:",
          expect="the account's cookie went to a host that is not dola's")
@mutation(FETCH, "return host_allowed(url, MASTER_HOST_SUFFIXES, default_port_only=True)",
          "return host_allowed(url, MASTER_HOST_SUFFIXES)",
          expect="the account's cookie went to a non-default port")
def test_the_cookie_only_goes_to_dolas_own_hosts():
    """The fallback link carries the account's cookie: it is followed only on dola / ByteDance API hosts, https."""
    from relay_dola.engine.fetch import trusted_master_host
    for url in ("https://vod-urls-mya.byteintlapi.com/video/fplay/1", "https://www.dola.com/x", "https://dola.com/x"):
        assert trusted_master_host(url), url
    for url in ("https://evil.example/x", "http://vod-urls-mya.byteintlapi.com/x",
                "https://byteintlapi.com.evil.example/x", "https://notbyteintlapi.com/x",
                "https://www.dola.com@evil.example/x", "ftp://www.dola.com/x", ""):
        assert not trusted_master_host(url), url
    for url in ("https://vod-urls-mya.byteintlapi.com:8443/video/fplay/1", "https://www.dola.com:444/x"):
        assert not trusted_master_host(url), "the account's cookie went to a non-default port: " + url
    assert trusted_master_host("https://www.dola.com:443/x")
    (out,), _ = outputs_of("v1")

    def evil_cell(vid, rendition):
        cell = master_cell(vid, rendition)
        model = json.loads(cell["video"]["video_model"])
        model["fallback_api"] = model["fallback_api"].replace("vod-urls-mya.byteintlapi.com", "evil.example")
        cell["video"]["video_model"] = json.dumps(model)
        return cell
    body = conversation("v1")
    creations = json.loads(body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"][0]["content"])
    creations[0]["content"]["creation_block"]["creations"] = [evil_cell("v1", RENDITION % "v1")]
    body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"][0]["content"] = json.dumps(creations)
    hostile = FakeTransport({CONV: body}, get_routes=master_routes(["v1"]))
    got, transport = collect(Ctx(), out, Settings({"dola.master_quality": True}), hostile)
    assert not any("evil" in g["url"] for g in transport.gets), "the account's cookie went to a host that is not dola's"
    assert got == b"RENDITION-v1", "the rendition is kept when the master link is not trusted"


@mutation(FETCH, "    if str(out.mime).startswith(\"video/\") and settings.get(\"dola.master_quality\"):",
          "    if str(out.mime).startswith(\"video/\"):", expect="master quality is off but the master was fetched")
def test_master_quality_off_never_reads_the_conversation_or_the_fallback_api():
    (out,), _ = outputs_of("v1")
    chains = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"]))
    got, transport = collect(Ctx(), out, Settings({"dola.master_quality": False}), chains)
    assert got == b"RENDITION-v1", "master quality is off but the master was fetched"
    assert not transport.posts and not transport.gets


@pytest.mark.parametrize("why,fallback", [
    ("the fallback route answers 500", lambda v: Resp(500, {})),
    ("the answer is not JSON", lambda v: Resp(200, bad_json=True)),
    ("no entry has a main_url", lambda v: Resp(200, {"data": {"video_list": {"a": {"bitrate": 1}}}})),
    ("the token cannot be decoded", lambda v: Resp(200, {"video_info": {"data": {"video_list": {
        "a": {"main_url": "bm90LXFhYWI=", "bitrate": 1}}}}, "key_seed": SEED})),
    ("there is no seed", lambda v: Resp(200, {"video_info": {"data": {"video_list": {
        "a": {"main_url": TOKEN, "bitrate": 1}}}}})),
])
def test_any_master_failure_silently_keeps_the_rendition(why, fallback):
    (out,), _ = outputs_of("v1")
    transport = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"], fallback))
    got, _ = collect(Ctx(), out, Settings({"dola.master_quality": True}), transport)
    assert got == b"RENDITION-v1", "%s: the rendition was not kept (%r)" % (why, got)


def test_a_missing_conversation_or_cell_keeps_the_rendition_without_a_master_call():
    (out,), _ = outputs_of("v1")
    settings = Settings({"dola.master_quality": True})
    gone = FakeTransport({}, get_routes=master_routes(["v1"]))        # the conversation is not readable any more
    assert collect(Ctx(), out, settings, gone)[0] == b"RENDITION-v1"
    assert not any("vod-urls-mya.byteintlapi.com" in g["url"] for g in gone.gets)
    other = FakeTransport({CONV: conversation("v9")}, get_routes=master_routes(["v1", "v9"]))   # the cell is not in it
    assert collect(Ctx(), out, settings, other)[0] == b"RENDITION-v1"
    bare = Output(RENDITION % "v1", "video/mp4", 0, "v1", {})        # no conversation in meta (an old output)
    plain = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"]))
    assert collect(Ctx(), bare, settings, plain)[0] == b"RENDITION-v1" and not plain.posts


def test_master_hq_url_never_raises_whatever_the_cell_holds():
    http = http_for(FakeTransport(get_routes=lambda u, p: (_ for _ in ()).throw(RuntimeError("boom"))))
    assert run(master_hq_url(http, master_cell("v1", "https://x/y.mp4"))) is None
    assert run(master_hq_url(http, {"video": "not-a-dict"})) is None
    assert run(master_hq_url(http, {})) is None


def test_no_secret_rides_in_the_output_meta_so_the_seed_route_is_not_stored():
    (out,), _ = outputs_of("v1")
    blob = json.dumps(dict(out.meta)) + str(out.upstream_id)
    assert "vod-urls-mya.byteintlapi.com" not in blob and "key_seed" not in blob and "fallback" not in blob


# ---- a master off the media list, a redirect on the cookie read ----------------------------------------------------
@mutation(FETCH, "        except UnsafeUrl:", "        except Exception:",
          expect="a failed master download went on to the rendition")
@pytest.mark.parametrize("failure", ["403", "too_large"])
def test_a_master_that_fails_to_download_fails_the_output_and_the_rendition_is_never_requested(failure, monkeypatch):
    (out,), _ = outputs_of("v1")
    answer = Resp(200, {"video_info": {"data": {"video_list": {"a": {"main_url": V4_TOKEN, "bitrate": 9}}}},
                        "key_seed": SEED})
    monkeypatch.setattr(fetch_mod, "decode_qaab", lambda token, seed: MASTER_URL)
    monkeypatch.setattr(fetch_mod, "MEDIA_MAX_BYTES", 50)       # the rendition fits, the master below does not

    def route(request):
        if logical_url(request) != MASTER_URL:
            return media_route(request)
        return 403 if failure == "403" else b"x" * 100

    media = MediaSite(route, route)
    transport = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"], lambda v: answer))
    try:
        collect(Ctx(), out, Settings({"dola.master_quality": True}), transport, media)
    except FetchFailed:
        pass
    else:
        raise AssertionError("a failed master download went on to the rendition")
    asked = [r["url"] for r in seen(media)]
    assert MASTER_URL in asked and RENDITION % "v1" not in asked, \
        "a failed master download went on to the rendition: %r" % asked


@mutation(FETCH, "        except UnsafeUrl:", "        except ZeroDivisionError:",
          expect="a refused master failed the job")
@mutation(NET, 'log.warning("media link refused (%s): %s", reason, url_host(url))',
          'log.warning("media link refused (%s)", reason)', expect="the refusal line did not name the refused host")
@mutation(NET, '_refuse(current, "host")', '_refuse(url, "host")',
          expect="the refusal line did not name the refused host")
@pytest.mark.parametrize("shape,master,expected_reason,expected_host", [
    ("first link, host", "https://cdn.example.com/m.mp4?sig=abc123", "host", "cdn.example.com"),
    ("first link, port", "https://v16-dola.dola.com:8443/m.mp4?sig=abc123", "port", "v16-dola.dola.com"),
    ("redirect, host", "https://v16-dola.dola.com/r/host?sig=abc123", "host", "cdn.example.com"),
    ("redirect, port", "https://v16-dola.dola.com/r/port?sig=abc123", "port", "v16-dola.dola.com"),
    ("redirect, malformed", "https://v16-dola.dola.com/r/punycode?sig=abc123", "malformed", "v16-dola.dola.com"),
    ("path, malformed", "https://v16-dola.dola.com/a\x7f.mp4?sig=abc123", "malformed", "v16-dola.dola.com"),
])
def test_a_master_the_download_refuses_keeps_the_rendition_and_logs_only_the_reason_and_host(
        shape, master, expected_reason, expected_host, caplog, monkeypatch):
    (out,), _ = outputs_of("v1")
    answer = Resp(200, {"video_info": {"data": {"video_list": {"a": {"main_url": V4_TOKEN, "bitrate": 9}}}},
                        "key_seed": SEED})
    monkeypatch.setattr(fetch_mod, "decode_qaab", lambda token, seed: master)
    moves = {"/r/host": "https://cdn.example.com/m.mp4?sig=abc123",
             "/r/port": "https://v16-dola.dola.com:8443/m.mp4?sig=abc123",
             "/r/punycode": "https://xn--a-.dola.com/m.mp4?sig=abc123"}

    def route(request):
        moved = moves.get(request.url.path)
        return Answer(302, headers={"location": moved}) if moved else media_route(request)

    transport = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"], lambda v: answer))
    with caplog.at_level(logging.DEBUG):
        try:
            got, _ = collect(Ctx(), out, Settings({"dola.master_quality": True}), transport,
                             MediaSite(route, route))
        except UnsafeUrl:
            raise AssertionError("a refused master failed the job: %s" % shape) from None
    assert got == b"RENDITION-v1", "%s: %r" % (shape, got)
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warned) == 1 and "(%s)" % expected_reason in warned[0], (shape, warned)
    assert warned[0].endswith(": " + expected_host), \
        "the refusal line did not name the refused host (%s): %r" % (expected_host, warned[0])
    assert "sig=abc123" not in warned[0] and "8443" not in warned[0] and "m.mp4" not in warned[0], warned


@mutation(FETCH, "            if handed or i == len(urls) - 1:", "            if i == len(urls) - 1:",
          expect="a refusal after a handed byte fell back to the rendition")
def test_a_refusal_after_a_byte_of_the_master_was_handed_over_is_not_swallowed(monkeypatch):
    """Two bodies are never mixed: once the caller holds a byte, a refusal propagates."""
    (out,), _ = outputs_of("v1")
    calls = []

    async def two_part_then_refuse(url, **kw):
        calls.append(url)
        yield b"first"
        raise UnsafeUrl("host")

    monkeypatch.setattr(fetch_mod, "stream_download", two_part_then_refuse)
    monkeypatch.setattr(fetch_mod, "_master_for", lambda http, o: _async("https://v16-dola.dola.com/m.mp4"))

    async def go():
        got = []
        async for part in fetch(Ctx(), out, Settings({"dola.master_quality": True}),
                                http=http_for(FakeTransport(), Ctx().account)):
            got.append(part)
        return got

    try:
        run(go())
    except UnsafeUrl:
        pass
    else:
        raise AssertionError("a refusal after a handed byte fell back to the rendition")
    assert calls == ["https://v16-dola.dola.com/m.mp4"], "a refusal after a handed byte fell back to the rendition"


async def _async(value):
    return value


@mutation(FETCH, "        if 300 <= answer.status_code < 400:", "        if False:",
          expect="a redirect answer was used as a master")
def test_a_redirect_on_the_cookie_read_is_a_failed_master_lookup_and_keeps_the_rendition():
    (out,), _ = outputs_of("v1")
    redirect_answer = Resp(302, master_response("v1").json())      # a body that WOULD give a master if it were used
    transport = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"], lambda v: redirect_answer))
    got, _ = collect(Ctx(), out, Settings({"dola.master_quality": True}), transport)
    assert got == b"RENDITION-v1", "a redirect answer was used as a master: %r" % got


@contextlib.contextmanager
def redirecting_servers():
    """Two loopback servers: every request to the first (any method) is answered 302 to the second. Yields the list of
    cookie headers the second one saw and the first one's base URL; the second must never be asked."""
    asked = []

    class Target(http.server.BaseHTTPRequestHandler):
        def handle_any(self):
            asked.append(self.headers.get("cookie"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        do_GET = do_POST = handle_any

        def log_message(self, *args):
            pass

    target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)

    class Hop(Target):
        def handle_any(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/next" % target.server_port)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = handle_any

    hop = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hop)
    for srv in (target, hop):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield asked, "http://127.0.0.1:%d" % hop.server_port
    finally:
        for srv in (target, hop):
            srv.shutdown()
            srv.server_close()


@mutation(HTTP, "proxies=proxies,\n                        allow_redirects=False)",
          "proxies=proxies,\n                        allow_redirects=True)",
          expect="the redirect target was asked by GET")
def test_the_cookie_read_never_follows_a_redirect_to_another_host():
    """Real curl_cffi against two local servers: the first answers 302 to the second; the second must never be asked
    (the account's cookie would go with it)."""
    with redirecting_servers() as (asked, base):
        answer = CurlTransport().get(base + "/start", headers={"cookie": "k=v"}, timeout=10, proxies=None)
    assert answer.status_code == 302 and not asked, \
        "the redirect target was asked by GET: %r" % asked


@mutation(HTTP, "proxies=proxies, allow_redirects=False)", "proxies=proxies, allow_redirects=True)",
          expect="the redirect target was asked by POST")
def test_the_api_post_never_follows_a_redirect_and_the_3xx_comes_back_as_the_answer(monkeypatch):
    """Through `DolaHttp.post_chain` (pointed at a loopback server): the 302 is returned, so every caller sees a non-200
    and treats it as a failed read; the second server (and the body of the POST) is never reached."""
    with redirecting_servers() as (asked, base):
        monkeypatch.setattr(http_mod, "BASE", base)
        cookie = "k=" + secrets.token_hex(6)
        answer = run(DolaHttp(cookie).post_chain("/im/chain/single", {"a": 1}, base + "/chat", timeout=10))
        direct = CurlTransport().post(base + "/x", params={}, headers={"cookie": cookie}, json={"a": 1}, timeout=10,
                                      proxies=None)
    assert answer.status_code == 302 and direct.status_code == 302 and not asked, \
        "the redirect target was asked by POST: %r" % asked
