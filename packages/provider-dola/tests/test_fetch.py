"""Fetch (contracts 5.4.1 "fetch"; coverage RES14, RES26, RES16): direct first, then through the account proxy; master
quality through the cell's own `fallback_api` and the qAAB token; silent fallback; images take only the clean link."""

import asyncio
import base64
import json

import pytest
from kit_b import CONV, Ctx, FakeTransport, Resp, Settings, account, chain_with, http_for, job, user

from relay_core.providers import Output
from relay_core.testing.mutation import mutation
from relay_dola.engine import fetch as fetch_mod
from relay_dola.engine.fetch import decode_qaab, fetch, master_hq_url, walk_field
from relay_dola.engine.http import DownloadError
from relay_dola.engine.poll import poll

FETCH = "packages/provider-dola/src/relay_dola/engine/fetch.py"
HTTP = "packages/provider-dola/src/relay_dola/engine/http.py"

PROXIES = {"http": "http://user:pass@proxy.invalid:8080", "https": "http://user:pass@proxy.invalid:8080"}
RENDITION = "https://cdn.example.com/rendition-%s.mp4"
MASTER_URL = "https://cdn.example.com/master/h264-720p.mp4?sig=abc123"
# a qAAB token and its seed, made with v4's own key schedule (resolver._decode_qaab round-trips it): port parity
SEED = "AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8w"
TOKEN = ("qAABANYTvgYokgwQE6gZbOf471LOxNMuc4DtnFxgHDdQs2rRcMI4+ZQD8iaV8vay/yIb2X5zwEcMnBM9ESyXXzu+3x8=")


def run(coro):
    return asyncio.run(coro)


def collect(ctx, out, settings=None, transport=None):
    transport = transport or FakeTransport()
    http = http_for(transport, ctx.account)

    async def go():
        return b"".join([c async for c in fetch(ctx, out, settings or Settings(), http=http)])
    return run(go()), transport


def video_out(vid="v1", url=RENDITION % "v1", conversation=CONV):
    return Output(url, "video/mp4", 0, vid, {"conversation_id": conversation, "source": "main_url"})


# ---- direct first, proxy only as the fallback (RES26) ---------------------------------------------------------------
@mutation(HTTP, "            return await self._download_once(url, None)\n        except DownloadError:",
          "            return await self._download_once(url, self.proxies)\n        except DownloadError:",
          expect="the media download goes through the account proxy first")
def test_the_download_goes_direct_first_and_never_touches_the_proxy_when_direct_works():
    ctx = Ctx()
    body, transport = collect(ctx, video_out(), transport=FakeTransport(get_routes={RENDITION % "v1": b"video"}))
    assert body == b"video"
    assert [g["proxies"] for g in transport.gets] == [None], \
        "the media download goes through the account proxy first: %r" % [g["proxies"] for g in transport.gets]
    assert transport.gets[0]["headers"] == {}, "the signed CDN link carries no cookie"


@mutation(HTTP, "        except DownloadError:\n            if self.proxies is None:",
          "        except ZeroDivisionError:\n            if self.proxies is None:",
          expect="media download returned HTTP 403")
def test_a_failed_direct_download_falls_back_to_the_account_proxy():
    def route(url, proxies):
        return 403 if proxies is None else b"video"
    body, transport = collect(Ctx(), video_out(), transport=FakeTransport(get_routes=route))
    assert body == b"video"
    assert [g["proxies"] for g in transport.gets] == [None, PROXIES], \
        "a failed direct download does not fall back to the proxy: %r" % [g["proxies"] for g in transport.gets]


def test_a_failed_download_with_no_proxy_is_an_error_and_with_both_failing_too():
    with pytest.raises(DownloadError):
        collect(Ctx(account(proxy=False)), video_out(), transport=FakeTransport(get_routes={RENDITION % "v1": 403}))
    with pytest.raises(DownloadError):
        collect(Ctx(), video_out(), transport=FakeTransport(get_routes=lambda u, p: 500))


def test_an_empty_body_and_a_transport_exception_are_failed_attempts_not_a_video():
    """A 200 with no bytes became a 0-second 'video' once (v4 fa10b65)."""
    with pytest.raises(DownloadError, match="empty"):
        collect(Ctx(account(proxy=False)), video_out(), transport=FakeTransport(get_routes={RENDITION % "v1": b""}))

    def route(url, proxies):
        raise OSError("reset") if proxies is None else None
    with pytest.raises(DownloadError):
        collect(Ctx(), video_out(), transport=FakeTransport(get_routes=route))
    ok = lambda url, proxies: OSError("reset") if proxies is None else b"video"       # noqa: E731
    assert collect(Ctx(), video_out(), transport=FakeTransport(get_routes=ok))[0] == b"video"


def test_the_bytes_come_in_chunks_that_add_up():
    big = bytes(range(256)) * 9000                        # 2.3 MB: more than one chunk
    got, _ = collect(Ctx(), video_out(), transport=FakeTransport(get_routes={RENDITION % "v1": big}))
    chunks = list(fetch_mod.chunks(big))
    assert got == big and len(chunks) == 3 and all(len(c) <= fetch_mod.CHUNK for c in chunks)


# ---- images: the clean link only ------------------------------------------------------------------------------------
def test_an_image_is_downloaded_from_its_clean_link_and_never_asks_for_a_master():
    out = Output("https://x/raw.png", "image/png", 0, "%s:0" % CONV, {"conversation_id": CONV})
    got, transport = collect(Ctx(), out, Settings({"dola.master_quality": True}),
                             FakeTransport(get_routes={"https://x/raw.png": b"\x89PNG"}))
    assert got == b"\x89PNG" and not transport.posts and len(transport.gets) == 1


# ---- master quality via qAAB (RES14) --------------------------------------------------------------------------------
def test_decode_qaab_matches_the_v4_key_schedule_and_refuses_what_it_cannot_decode():
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
        if url == MASTER_URL:
            return b"MASTER-BYTES"
        for v in vids:
            if url == RENDITION % v:
                return b"RENDITION-" + v.encode()
        return None
    return route


def outputs_of(*vids):
    """The real outputs a poll gives for a conversation with these cells (so fetch is tested against them)."""
    transport = FakeTransport({CONV: conversation(*vids)})
    ctx = Ctx()
    result = run(poll(ctx, job(prompt="a red kite", marker="4100"), ctx.settings, http=http_for(transport),
                      clock=lambda: 1_790_000_100.0))
    return result.outcome.outputs, transport


def test_master_quality_replaces_the_rendition_with_the_h264_master():
    (out,), _ = outputs_of("v1")
    ctx = Ctx()
    chains = FakeTransport({CONV: conversation("v1")}, get_routes=master_routes(["v1"]))
    got, transport = collect(ctx, out, Settings({"dola.master_quality": True}), chains)
    assert got == b"MASTER-BYTES", "the master was not taken: %r" % got
    fallback = next(g for g in transport.gets if "vod-urls-mya.byteintlapi.com" in g["url"])
    assert "sessionid=fake-sessionid" in fallback["headers"]["cookie"] and fallback["proxies"] == PROXIES
    assert [g["url"] for g in transport.gets][-1] == MASTER_URL and transport.gets[-1]["headers"] == {}
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


@mutation(FETCH, "    if not fallback or not trusted_master_host(fallback):", "    if not fallback:",
          expect="the account's cookie went to a host that is not dola's")
def test_the_cookie_only_goes_to_dolas_own_hosts():
    """The fallback link carries the account's cookie: it is followed only on dola / ByteDance API hosts, https."""
    from relay_dola.engine.fetch import trusted_master_host
    for url in ("https://vod-urls-mya.byteintlapi.com/video/fplay/1", "https://www.dola.com/x", "https://dola.com/x"):
        assert trusted_master_host(url), url
    for url in ("https://evil.example/x", "http://vod-urls-mya.byteintlapi.com/x",
                "https://byteintlapi.com.evil.example/x", "https://notbyteintlapi.com/x",
                "https://www.dola.com@evil.example/x", "ftp://www.dola.com/x", ""):
        assert not trusted_master_host(url), url
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
    assert not transport.posts and len(transport.gets) == 1


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
