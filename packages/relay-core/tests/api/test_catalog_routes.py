"""Catalog routes: models, aliases, the Gemini catalog, provider options and `/v1/points`, each as the
calling key sees them."""

import dataclasses
import json

from apikit import NewApiKey, run_app

from relay_core import keys
from relay_core.db import tx
from relay_core.providers import ModelSpec
from relay_core.testing.fake_provider import FAKE_MAX_PROMPT_CHARS, FakeSpec
from relay_core.testing.mutation import mutation

CATALOG = "packages/relay-core/src/relay_core/api/routes_catalog.py"
FILTER = "if keys.model_allowed(key.models, m.id, canonical) and"
PAUSED_FILTER = " and not model_disabled(settings, s, m)]"
NAME_ENTRY = '"name": m.display_name or m.id,'
GEMINI_NAME = '"displayName": name,'
ROUTES = ("/v1/models", "/v1/models/aliases", "/v1beta/models", "/models", "/v1beta/models/fake-video",
          "/models/fake-video", "/v1/providers/fake/options", "/v1/points")


class AliasSpec(FakeSpec):
    """A second provider whose one model has an alias, limits, and options built from its snapshots."""

    def catalog(self) -> list[ModelSpec]:
        return [ModelSpec(self.name + "-video", "video", ("generate",), aliases=(self.name + "-fast",),
                          limits={"durations": [5, 10]}, price_keys={"default": "other-video"})]

    def extras(self, snapshots):
        return {"snapshots": {name: dict(data) for name, data in snapshots.items()}}


async def make_key(env, *, models=(keys.ANY_MODEL,), wallets=None, max_unfinished=None) -> NewApiKey:
    declared = [(s.name, kind) for s in map(env.registry.spec, env.registry.names()) for kind in s.caps.wallet_kinds]
    async with tx(env.pool) as conn:
        made = await keys.create_key(conn, models=models, declared=declared, wallets=wallets or {},
                                     max_unfinished=max_unfinished)
    return NewApiKey(made.id, made.raw)


def ids(r) -> list[str]:
    return [m["id"] for m in r.json()["data"]]


def test_every_catalog_route_needs_a_key(db):
    async def scenario(env):
        out = []
        for path in ROUTES:
            error = (await env.client.get(path)).json()["error"]
            out.append(error["code"] if isinstance(error["code"], str) else error["details"][0]["reason"])
        return out                                  # the Gemini paths answer in the Gemini body

    assert run_app(db, scenario) == ["auth.invalid_key"] * len(ROUTES)


@mutation(CATALOG, '"limits": {**m.limits, "max_prompt_chars": spec.max_prompt_chars},', '"limits": dict(m.limits),',
          expect="limits.max_prompt_chars")
def test_models_list_the_catalog_with_prices_and_limits(db):
    async def scenario(env):
        env.add_provider(AliasSpec("other"))
        key = await make_key(env)
        return await env.client.get("/v1/models", headers=key.headers)

    r = run_app(db, scenario)
    assert r.status_code == 200 and r.json()["object"] == "list"
    by_id = {m["id"]: m for m in r.json()["data"]}
    assert list(by_id) == ["fake-video", "fake-image", "other-video"]
    assert by_id["other-video"] == {"id": "other-video", "name": "other-video", "object": "model", "owned_by": "other",
                                    "kind": "video", "operations": ["generate"], "aliases": ["other-fast"],
                                    "limits": {"durations": [5, 10], "max_prompt_chars": FAKE_MAX_PROMPT_CHARS},
                                    "prices": {"default": 10}, "is_alias": False}, "limits.max_prompt_chars"
    assert by_id["fake-image"]["prices"] == {"default": 1} and by_id["fake-image"]["kind"] == "image"


class NamedSpec(AliasSpec):
    """AliasSpec whose one model has a product name."""

    def catalog(self) -> list[ModelSpec]:
        return [dataclasses.replace(m, display_name="Other Video 2") for m in super().catalog()]


@mutation(CATALOG, NAME_ENTRY, '"name": m.id,', expect="the display name")
@mutation(CATALOG, NAME_ENTRY, '"name": m.display_name,', expect="the id stands in")
@mutation(CATALOG, GEMINI_NAME, '"displayName": m.display_name or name,', expect="gemini displayName is unchanged")
def test_every_catalog_entry_names_the_model_with_its_display_name_or_its_id(db):
    async def scenario(env):
        env.add_provider(NamedSpec("other"))
        h = (await make_key(env)).headers
        out = {p: (await env.client.get(p, headers=h)).json()
               for p in ("/v1/models", "/v1/models/aliases", "/v1beta/models", "/models/other-fast")}
        out["admin"] = (await env.client.get("/admin/models", headers=await env.admin_headers())).json()
        return out

    out = run_app(db, scenario)
    names = {m["id"]: m["name"] for m in out["/v1/models"]["data"]}
    assert names["other-video"] == "Other Video 2", "the display name: %s" % names
    assert (names["fake-video"], names["fake-image"]) == ("fake-video", "fake-image"), "the id stands in: %s" % names
    assert [(m["id"], m["name"]) for m in out["/v1/models/aliases"]["data"]] == [("other-fast", "Other Video 2")]
    assert {m["id"]: m["name"] for m in out["admin"]["data"]} == names, "the admin list names them the same"
    # The Gemini resource keeps its existing value: the id or alias asked for, display name or not.
    shown = {m["name"]: m["displayName"] for m in out["/v1beta/models"]["models"]}
    assert shown["models/other-video"] == "other-video", "gemini displayName is unchanged: %s" % shown
    assert out["/models/other-fast"]["displayName"] == "other-fast", "gemini displayName is unchanged"


def test_the_aliases_list_has_one_entry_per_alias_naming_its_base(db):
    async def scenario(env):
        env.add_provider(AliasSpec("other"))
        key = await make_key(env)
        return await env.client.get("/v1/models/aliases", headers=key.headers)

    data = run_app(db, scenario).json()["data"]
    assert [(m["id"], m["base_model"], m["is_alias"], m["owned_by"], m["prices"]) for m in data] \
        == [("other-fast", "other-video", True, "other", {"default": 7})]


@mutation(
    CATALOG, FILTER, "if True and", expect="the key sees only the models its allow-list admits")
def test_a_key_sees_only_the_models_its_allow_list_admits(db):
    async def scenario(env):
        env.add_provider(AliasSpec("other"))
        one = await make_key(env, models=("fake-video",))
        alias = await make_key(env, models=("other-fast",))         # an alias admits the model it runs
        out = {}
        for who, key in (("one", one), ("alias", alias)):
            out[who] = (ids(await env.client.get("/v1/models", headers=key.headers)),
                        ids(await env.client.get("/v1/models/aliases", headers=key.headers)),
                        [m["name"] for m in (await env.client.get("/v1beta/models", headers=key.headers))
                         .json()["models"]],
                        [m["name"] for m in (await env.client.get("/models", headers=key.headers)).json()["models"]])
        return out

    out = run_app(db, scenario)
    assert out["one"] == (["fake-video"], [], ["models/fake-video"], ["models/fake-video"]), \
        "the key sees only the models its allow-list admits"
    assert out["alias"] == (["other-video"], ["other-fast"], ["models/other-video"], ["models/other-video"])


@mutation(CATALOG, PAUSED_FILTER, "]", expect="a paused model is listed")
@mutation("packages/relay-core/src/relay_core/admin/routes_keys.py", '"disabled": model_disabled(settings, s, m)',
          '"disabled": False', expect="'other-video': False")
def test_a_paused_model_is_left_out_of_every_list_and_its_admin_entry_says_so(db):
    async def scenario(env):
        env.add_provider(AliasSpec("other"))
        await env.settings.set("other.disabled_models", ["other-video"])
        h = (await make_key(env)).headers
        return {"models": ids(await env.client.get("/v1/models", headers=h)),
                "aliases": ids(await env.client.get("/v1/models/aliases", headers=h)),
                "gemini": [m["name"] for m in (await env.client.get("/v1beta/models", headers=h)).json()["models"]],
                "one": (await env.client.get("/models/other-fast", headers=h)).status_code,
                "options": (await env.client.get("/v1/providers/other/options", headers=h)).status_code,
                "admin": {m["id"]: m["disabled"] for m in (await env.client.get(
                    "/admin/models", headers=await env.admin_headers())).json()["data"]}}

    out = run_app(db, scenario)
    assert (out["models"], out["aliases"], out["gemini"], out["one"], out["options"]) == (
        ["fake-video", "fake-image"], [], ["models/fake-video", "models/fake-image"], 404, 404), (
        "a paused model is listed: %s" % out)
    assert out["admin"] == {"fake-video": False, "fake-image": False, "other-video": True}, out["admin"]


def test_the_gemini_catalog_has_the_resource_shape_and_resolves_aliases(db):
    async def scenario(env):
        env.add_provider(AliasSpec("other"))
        key = await make_key(env, models=("fake-video", "other-fast"))
        h = key.headers
        return (await env.client.get("/v1beta/models/fake-video", headers=h),
                await env.client.get("/models/other-fast", headers=h),
                await env.client.get("/v1beta/models/fake-image", headers=h),      # exists, not allowed
                await env.client.get("/models/nothing", headers=h))

    one, alias, hidden, unknown = run_app(db, scenario)
    assert one.status_code == 200 and one.json() == {
        "name": "models/fake-video", "baseModelId": "fake-video", "version": "relay", "displayName": "fake-video",
        "description": "video generation model (fake)", "inputTokenLimit": 0, "outputTokenLimit": 0,
        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]}
    assert alias.json()["name"] == "models/other-fast" and alias.json()["baseModelId"] == "other-video"
    for r in (hidden, unknown):                                  # a model the key may not use is unknown to it
        assert r.status_code == 404
        body = r.json()["error"]
        assert body["code"] == 404 and body["status"] == "NOT_FOUND"
        assert body["details"][0]["reason"] == "resource.not_found"


def test_provider_options_come_from_the_provider_snapshots(db):
    async def scenario(env):
        env.add_provider(AliasSpec("other"))
        db.run("INSERT INTO provider_snapshots (provider, name, data) VALUES ('other', 'templates', %s::jsonb), "
               "('fake', 'x', '{}'::jsonb)", json.dumps({"items": [1, 2]}))
        key = await make_key(env, models=("other-video",))
        return (await env.client.get("/v1/providers/other/options", headers=key.headers),
                await env.client.get("/v1/providers/fake/options", headers=key.headers),      # key has no fake model
                await env.client.get("/v1/providers/nope/options", headers=key.headers))

    ok, not_allowed, unknown = run_app(db, scenario)
    assert ok.status_code == 200 and ok.json() == {"snapshots": {"templates": {"items": [1, 2]}}}
    assert (not_allowed.status_code, unknown.status_code) == (404, 404)
    assert unknown.json()["error"]["code"] == "resource.not_found" and unknown.json()["error"]["param"] == "provider"


def points_of(db, env_key_id, provider="fake", kind="video"):
    db.run("UPDATE key_wallets SET used_day = 7, used_total = 40, "
           "day_anchor = (clock_timestamp() AT TIME ZONE 'UTC')::date WHERE key_id = %s AND provider = %s "
           "AND kind = %s", env_key_id, provider, kind)


@mutation(
    CATALOG, "CASE WHEN w.day_anchor IS DISTINCT FROM d.today THEN 0 ELSE w.used_day END", "w.used_day",
    expect="yesterday's day counter is not shown as today's")
@mutation(
    CATALOG, "WHERE w.key_id = %s ORDER BY", "WHERE w.key_id = %s OR true ORDER BY",
    expect="the key sees only its own wallets")
def test_points_show_the_keys_own_wallets_and_a_new_day_starts_at_zero(db):
    async def scenario(env):
        mine = await make_key(env, wallets={("fake", "video"): (100, 1000), ("fake", "image"): (None, None)})
        other = await make_key(env, wallets={("fake", "video"): (5, 5)})
        points_of(db, mine.id)
        points_of(db, other.id)
        try:
            today = (await env.client.get("/v1/points", headers=mine.headers)).json()
            db.run("UPDATE key_wallets SET day_anchor = day_anchor - 1 WHERE key_id = %s AND kind = 'video'",
                   mine.id)
            yesterday = (await env.client.get("/v1/points", headers=mine.headers)).json()
        finally:                                     # counters with no ledger behind them are drift: undo the plant
            db.run("UPDATE key_wallets SET used_day = 0, used_total = 0")
        return today, yesterday

    today, yesterday = run_app(db, scenario)
    assert today["fake"] == {
        "video": {"day_limit": 100, "used_day": 7, "total_limit": 1000, "used_total": 40},
        "image": {"day_limit": None, "used_day": 0, "total_limit": None, "used_total": 0}}, \
        "the key sees only its own wallets"
    assert yesterday["fake"]["video"]["used_day"] == 0, "yesterday's day counter is not shown as today's"
    assert yesterday["fake"]["video"]["used_total"] == 40


@mutation(
    CATALOG, 'status NOT IN (\'done\', \'failed\')', "status NOT IN ('done')",
    expect="finished jobs are not unfinished")
@mutation(
    CATALOG, '"max_unfinished_jobs": cap or None', '"max_unfinished_jobs": cap',
    expect="zero means no cap and is shown as null")
def test_points_count_unfinished_jobs_and_show_the_cap_and_the_allow_list(db):
    async def scenario(env):
        key = await make_key(env, models=("fake-video", "fake-image"), max_unfinished=3)
        default = await make_key(env)
        unlimited = await make_key(env, max_unfinished=0)
        for _ in range(2):
            db.job(key.id)                                              # queued
        done = db.job(key.id)
        db.run("UPDATE jobs SET status = 'failed', finished_at = now(), error_code = 'render.deadline', "
               "error = 'the render did not finish in time' WHERE id = %s", done)
        return [(await env.client.get("/v1/points", headers=k.headers)).json() for k in (key, default, unlimited)]

    own, default, unlimited = run_app(db, scenario)
    assert own["unfinished_jobs"] == 2, "finished jobs are not unfinished"
    assert (own["max_unfinished_jobs"], own["models"]) == (3, ["fake-video", "fake-image"])
    assert default["max_unfinished_jobs"] == 20 and default["models"] == ["*"]        # the setting's default
    assert unlimited["max_unfinished_jobs"] is None, "zero means no cap and is shown as null"


LIVE_PRICES = '"prices": pricing.option_prices(spec, m, settings), "is_alias": False}'


@mutation(CATALOG, LIVE_PRICES, '"prices": {"default": 10}, "is_alias": False}', expect="the live price")
def test_models_show_the_live_sale_price_and_null_when_unset(db):
    """The catalog shows `<p>.prices` as it is now (the owner edits it), and null for an option that has no price: a
    request for it is refused (model.unpriced)."""
    async def scenario(env):
        key = await make_key(env)
        await env.settings.set("fake.prices", {"fake-video": 3})
        listed = (await env.client.get("/v1/models", headers=key.headers)).json()
        admin = (await env.client.get("/admin/models", headers=await env.admin_headers())).json()
        return listed, admin

    listed, admin = run_app(db, scenario)
    for body in (listed, admin):
        prices = {m["id"]: m["prices"] for m in body["data"]}
        assert prices["fake-video"] == {"default": 3}, "the live price: %s" % prices
        assert prices["fake-image"] == {"default": None}, "an option with no price shows null: %s" % prices
