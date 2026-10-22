"""`run_command` (coverage RUN14, TPL00 engine half): titles, refresh_catalog, list_conversations,
recover. The titles command is HTTP only: the core calls it with no lease and no browser token."""

import asyncio
import json

import pytest
from kit_b import CONV, Ctx, FakeTransport, Resp, account, chain_with, http_for, user

from relay_core.testing.mutation import mutation
from relay_dola import catalog
from relay_dola.engine import templates
from relay_dola.engine.commands import CommandRefused, run_command
from relay_dola.engine.reconcile import Listing
from relay_dola.engine.templates import CatalogError

COMMANDS = "packages/provider-dola/src/relay_dola/engine/commands.py"
NOW = 1_790_000_000.0


def run(coro):
    return asyncio.run(coro)


def command(kind, args, transport=None, ctx=None, lister=None):
    ctx = ctx or Ctx()
    transport = transport or FakeTransport()
    result = run(run_command(ctx, kind, args, ctx.settings, http=http_for(transport, ctx.account), lister=lister,
                             clock=lambda: NOW))
    return result, transport


async def forbidden_lister(*a, **k):
    raise AssertionError("a browser listing was asked for")


# ---- titles ------------------------------------------------------------------------------------------------------
@mutation(COMMANDS, "TITLES_PER_CALL = 50", "TITLES_PER_CALL = 500", expect="a title call carried more than 50")
def test_titles_come_from_batch_get_50_per_call_and_merge():
    refs = [str(38_417_000_000_000_000 + i) for i in range(120)]
    names = {r: "Name %s" % r[-3:] for r in refs}
    result, transport = command("titles", {"refs": refs}, FakeTransport(names=names), lister=forbidden_lister)
    sizes = [len(c) for c in transport.batch_calls()]
    assert sizes and max(sizes) <= 50, "a title call carried more than 50 conversations: %r" % sizes
    assert sizes == [50, 50, 20]
    assert result == {"titles": names}, "the names of every chunk must come back"


def test_titles_skip_placeholders_and_unnamed_conversations_and_one_bad_call_loses_only_its_chunk():
    transport = FakeTransport(names={"1": "Larva Battle Royale", "2": "New chat", "3": "  "})
    assert command("titles", {"refs": ["1", "2", "3", "4"]}, transport)[0] == {"titles": {"1": "Larva Battle Royale"}}
    assert command("titles", {"refs": []}, transport)[0] == {"titles": {}}
    assert len(transport.posts) == 1, "no refs, no call"

    class OneBad(FakeTransport):
        def post(self, url, **kw):
            if kw["json"]["uplink_body"]["batch_get_conv_info_uplink_body"]["conversation_id"][0] == "0":
                return Resp(503, {})
            return super().post(url, **kw)
    refs = [str(i) for i in range(60)]
    got = command("titles", {"refs": refs}, OneBad(names={r: "n" + r for r in refs}))[0]["titles"]
    assert set(got) == {str(i) for i in range(50, 60)}, "only the failing chunk may be lost"


def test_titles_use_the_account_cookie_and_proxy_and_open_no_browser():
    ctx = Ctx(token=None)                                        # no browser token, as the core's title sweep
    result, transport = command("titles", {"refs": ["1"]}, FakeTransport(names={"1": "A"}), ctx, forbidden_lister)
    assert result == {"titles": {"1": "A"}}
    (call,) = transport.posts
    assert call["url"].endswith("/im/conversation/batch_get") and call["json"]["cmd"] == 1111
    assert "sessionid=" in call["headers"]["cookie"] and call["proxies"]["https"].startswith("http://user:pass@")
    assert not transport.gets


# ---- refresh_catalog -------------------------------------------------------------------------------------------
def pack(prompts, category, extra_meta=None):
    meta = {"category_list": [{"category_id": 5, "category_name": "Posters"}]}
    meta.update(extra_meta or {})
    return {"meta": meta,
            "template": {"image_list": [{"prompt": p, "show_value": p, "category": category,
                                         "default_image": {"uri": "tos/%s.jpeg" % i, "url": "https://x/%s.jpg" % i,
                                                           "width": 640, "height": 480}}
                                        for i, p in enumerate(prompts)]},
            "simple_template": [{"prompt": "simple one", "category": [{"category_id": 5, "category_name": "Posters"}]}]}


OPTIONS = {"option_list": [
    {"value": "style", "options": [
        {"value": "skill_image_styles_3d", "show_name": "3D", "background_url": "https://x/3d.jpg",
         "is_default": False},
        {"value": "skill_image_styles_japanese_anime", "show_name": "Anime", "background_url": "https://x/a.jpg",
         "is_default": True},
        {"value": "skill_image_styles_brand_new", "show_name": "New", "background_url": ""}]},
    {"value": "ratio", "options": [{"value": "16:9", "show_name": "16:9", "background_url": "https://x/r.jpg"}]}]}


def test_refresh_catalog_returns_the_templates_and_styles_snapshots_in_the_shape_the_spec_reads():
    transport = FakeTransport(packs={0: pack(["hero section", " ", "app mockup"], None, OPTIONS),
                                     5: pack(["poster one"], 5)})
    result, transport = command("refresh_catalog", {}, transport)
    snaps = result["snapshots"]
    assert set(snaps) == {"templates", "styles"}
    cats = snaps["templates"]["categories"]
    assert set(cats) == {"0", "5"} and snaps["templates"]["names"]["5"] == "Posters"
    # index = the position in dola's list; a prompt-less template keeps its place so an index never moves
    assert [t["index"] for t in cats["0"]] == [0, 2, 3] and cats["0"][1]["prompt"] == "app mockup"
    assert cats["0"][0]["thumbnail_url"] == "https://x/0.jpg" and cats["0"][0]["width"] == 640
    assert cats["0"][2]["category"] == "Posters" and cats["5"][0]["category"] == "Posters"
    # the options endpoint's reader accepts exactly this shape
    assert [t["prompt"] for t in catalog.template_list(snaps["templates"], 0)][:2] == ["hero section", "app mockup"]
    assert [t["prompt"] for t in catalog.template_list(snaps["templates"], 5)][:1] == ["poster one"]
    styles = snaps["styles"]
    assert [s["value"] for s in styles["styles"]] == ["3d", "anime"], "an unknown style must be dropped"
    assert styles["styles"][1]["default"] is True and styles["ratios"] == [
        {"label": "16:9", "image": "https://x/r.jpg", "default": False, "value": "16:9"}]
    assert [p["json"]["condition"]["image_condition"]["category_id"] for p in transport.posts] == [0, 5]
    assert all(p["url"].endswith("/samantha/skill/pack") and p["json"]["skill_type"] == 3 for p in transport.posts)


def test_a_snapshot_holds_no_credential_and_the_catalog_needs_no_browser():
    ctx = Ctx(token=None)
    result, _ = command("refresh_catalog", {}, FakeTransport(packs={0: pack(["a"], None), 5: pack(["b"], 5)}), ctx)
    blob = json.dumps(result)
    assert "fake-sessionid" not in blob and "sessionid" not in blob and "proxy.invalid" not in blob


def test_a_catalog_that_cannot_be_read_fails_the_command_and_keeps_the_old_snapshot():
    class Down(FakeTransport):
        def post(self, *a, **k):
            return Resp(500, {})
    with pytest.raises(CatalogError):
        command("refresh_catalog", {}, Down())

    class Boom(FakeTransport):
        def post(self, *a, **k):
            raise OSError("reset")
    with pytest.raises(CatalogError):
        command("refresh_catalog", {}, Boom())


def test_style_map_covers_the_13_styles():
    assert len(templates.STYLE_MAP) == 13 and set(templates.STYLE_MAP.values()) == set(catalog.VALID_STYLES)


# ---- list_conversations ---------------------------------------------------------------------------------------
def test_list_conversations_runs_the_probe_under_the_command_purpose():
    seen = {}

    async def lister(ctx, purpose="reconcile"):
        seen.update(ctx=ctx, purpose=purpose)
        return Listing(["38417999999999991", "38417999999999992"], 2, False)
    ctx = Ctx(token=object())
    result, _ = command("list_conversations", {}, ctx=ctx, lister=lister)
    assert result == {"conversations": ["38417999999999991", "38417999999999992"]}
    assert seen["ctx"] is ctx and seen["purpose"] == "command", "the browser token is a command token"


# ---- recover and the rest ---------------------------------------------------------------------------------------
def test_recover_through_run_command_returns_outputs_for_the_core_to_store():
    import base64
    cell = {"type": 2, "video": {"vid": "v1", "video_model": json.dumps({"video_list": {"720p": {
        "main_url": base64.b64encode(b"https://x/1.mp4").decode()}}})}}
    body = chain_with([{"index_in_conv": 3, "create_time": NOW - 5, "content_type": 9999, "content": json.dumps([
        {"block_type": 2074, "content": {"creation_block": {"creations": [cell]}}}])},
        user("Generated video: a red kite [4100]", NOW - 30)])
    result, _ = command("recover", {"job_id": "j", "account_id": "a", "external_ref": CONV},
                        FakeTransport({CONV: body}))
    assert [(o.ref, o.upstream_id) for o in result["outputs"]] == [("https://x/1.mp4", "v1")]


def test_an_unknown_kind_is_refused():
    with pytest.raises(CommandRefused, match="no command of kind"):
        command("merge_credential", {})


def test_a_command_with_a_bad_credential_fails_instead_of_running_blind():
    ctx = Ctx(account())
    object.__setattr__(ctx.account, "credential", b"not json")
    with pytest.raises(ValueError):
        run(run_command(ctx, "titles", {"refs": ["1"]}, ctx.settings, clock=lambda: NOW))
