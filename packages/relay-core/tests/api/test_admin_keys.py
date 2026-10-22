"""Admin keys: per-provider wallets that are closed unless opened on purpose, the raw key
shown once, the unfinished-jobs cap meanings, and a delete that only removes a key nothing ever used."""

import uuid

import pytest
from apikit import run_app
from jobkit import new_job

from relay_core import keys
from relay_core.db import tx
from relay_core.jobs import store
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_keys.py"
FAKE_VIDEO, FAKE_IMAGE = ("fake", "video"), ("fake", "image")
UNLIMITED_FOR_NOTHING = '{"closed": (0, None), "unlimited": (None, None)}'
CLOSED_AS_UNLIMITED = '{"closed": (None, None), "unlimited": (None, None)}'
LIMIT_KEPT = "await keys.set_max_unfinished(conn, key_id, body.max_unfinished)"
LIMIT_ZERO_LOST = "await keys.set_max_unfinished(conn, key_id, body.max_unfinished or None)"
DELETE_GATE = "result = await keys.delete_key(conn, key_id)"
DELETE_ALWAYS_DISABLES = "result = \"disabled\" if await keys.set_enabled(conn, key_id, False) else \"missing\""


def code_of(r) -> str:
    return r.json()["error"]["code"]


def wallets_of(db, key_id) -> dict:
    return {(p, k): (d, t) for p, k, d, t in db.all(
        "SELECT provider, kind, day_limit, total_limit FROM key_wallets WHERE key_id = %s", uuid.UUID(key_id))}


async def make(env, h, **body) -> dict:
    r = await env.client.post("/admin/keys", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


@mutation(ROUTES, "declared=declared, wallets=wallets)", "declared=[], wallets=wallets)",
          expect="every declared wallet exists")
def test_a_wallet_the_admin_did_not_name_is_closed_and_a_named_one_is_what_was_asked(db):
    async def scenario(env):
        h = await env.admin_headers()
        return await make(env, h, label="client a", wallets=[
            {"provider": "fake", "kind": "video", "mode": "limit", "day_limit": 50, "total_limit": 500}])

    made = run_app(db, scenario)
    got = wallets_of(db, made["id"])
    assert set(got) == {FAKE_VIDEO, FAKE_IMAGE}, "every declared wallet exists"
    assert got[FAKE_VIDEO] == (50, 500) and got[FAKE_IMAGE] == (0, None), "the unnamed pair is closed"
    by_kind = {w["kind"]: w["mode"] for w in made["wallets"]}
    assert by_kind == {"video": "limit", "image": "closed"}


@mutation(ROUTES, UNLIMITED_FOR_NOTHING, CLOSED_AS_UNLIMITED, expect="closed means closed")
def test_closed_and_unlimited_and_a_day_only_limit_are_three_different_wallets(db):
    async def scenario(env):
        h = await env.admin_headers()
        return await make(env, h, wallets=[
            {"provider": "fake", "kind": "video", "mode": "closed"},
            {"provider": "fake", "kind": "image", "mode": "unlimited"}]), await make(env, h, wallets=[
                {"provider": "fake", "kind": "video", "mode": "limit", "day_limit": 5}])

    closed_open, day_only = run_app(db, scenario)
    got = wallets_of(db, closed_open["id"])
    assert got[FAKE_VIDEO] == (0, None), "closed means closed"
    assert got[FAKE_IMAGE] == (None, None)
    assert wallets_of(db, day_only["id"])[FAKE_VIDEO] == (5, None)


def test_a_bad_wallet_is_refused_and_no_key_is_made(db):
    bad = [
        [{"provider": "fake", "kind": "video", "mode": "limit"}],                         # a limit with no number
        [{"provider": "fake", "kind": "video", "mode": "unlimited", "day_limit": 3}],     # a number with no limit
        [{"provider": "fake", "kind": "video", "mode": "limit", "day_limit": 0}],         # 0 is "closed", not a limit
        [{"provider": "ghost", "kind": "video", "mode": "closed"}],                       # not declared
        [{"provider": "fake", "kind": "audio", "mode": "closed"}],
        [{"provider": "fake", "kind": "video", "mode": "closed"},
         {"provider": "fake", "kind": "video", "mode": "unlimited"}],                    # twice
        [{"provider": "fake", "kind": "video", "mode": "open"}],
    ]

    async def scenario(env):
        h = await env.admin_headers()
        return [await env.client.post("/admin/keys", json={"wallets": w}, headers=h) for w in bad] + [
            await env.client.post("/admin/keys", json={"models": []}, headers=h),
            await env.client.post("/admin/keys", json={"models": [" "]}, headers=h),
            await env.client.post("/admin/keys", json={"max_unfinished": -1}, headers=h),
            await env.client.post("/admin/keys", json={"wallet": []}, headers=h)]

    for r in run_app(db, scenario):
        assert (r.status_code, code_of(r)) == (400, "input.invalid"), r.text
    assert db.one("SELECT count(*) FROM api_keys")[0] == 0 and db.one("SELECT count(*) FROM key_wallets")[0] == 0


def test_the_raw_key_is_shown_once_works_and_no_listing_ever_leaks_a_hash(db):
    async def scenario(env):
        h = await env.admin_headers()
        made = [await make(env, h, label=name) for name in ("a", "b", "c")]
        listing = await env.client.get("/admin/keys", headers=h)
        one = await env.client.get("/admin/keys/%s" % made[0]["id"], headers=h)
        async with tx(env.pool) as conn:
            found = await keys.lookup(conn, made[0]["key"])
        return made, listing, one, found

    made, listing, one, found = run_app(db, scenario)
    assert all(m["key"].startswith(keys.PREFIX) for m in made) and len({m["key"] for m in made}) == 3
    assert found.id == uuid.UUID(made[0]["id"]) and found.models == ("*",)
    hashes = [r[0] for r in db.all("SELECT key_hash FROM api_keys")]
    assert len(hashes) == 3
    for text in (listing.text, one.text):
        assert not any(m["key"] in text for m in made), "the raw key came back"
        assert not any(h in text for h in hashes), "a hash leaked"
        assert "key_hash" not in text
    assert [i["prefix"] for i in listing.json()["items"]] and "key" not in one.json()
    assert db.one("SELECT count(*) FROM api_keys WHERE key_hash = %s", made[0]["key"])[0] == 0, "stored raw"


@mutation(ROUTES, LIMIT_KEPT, LIMIT_ZERO_LOST, expect="0 means unlimited")
def test_max_unfinished_null_uses_the_default_and_zero_means_unlimited_through_the_real_creation_gate(db):
    async def scenario(env):
        h = await env.admin_headers()
        key = await make(env, h, wallets=[{"provider": "fake", "kind": k, "mode": "unlimited"} for k in
                                          ("video", "image")], max_unfinished=1)
        key_id = uuid.UUID(key["id"])

        async def create() -> bool:
            try:
                await store.create_job(env.pool, new_job(key_id), env.settings)
            except store.TooManyQueued:
                return False
            return True

        patch = lambda body: env.client.patch("/admin/keys/%s" % key["id"], json=body, headers=h)  # noqa: E731
        out = {"created": key["max_unfinished"], "first": await create(), "second_at_1": await create()}
        out["zero"] = (await patch({"max_unfinished": 0})).json()
        out["three_more"] = [await create() for _ in range(3)]
        await env.settings.set("max_unfinished_default", 5)
        out["null"] = (await patch({"max_unfinished": None})).json()
        out["to_default"] = [await create() for _ in range(3)]       # 4 unfinished so far + 1 = 5, then refused
        out["listed"] = (await env.client.get("/admin/keys/%s" % key["id"], headers=h)).json()
        return out

    out = run_app(db, scenario)
    assert out["created"] == 1 and out["first"] is True and out["second_at_1"] is False, "a cap of 1 holds one job"
    assert out["zero"]["max_unfinished"] == 0 and out["zero"]["max_unfinished_effective"] == 0, "0 means unlimited"
    assert out["three_more"] == [True, True, True], "0 means unlimited"
    assert out["null"]["max_unfinished"] is None and out["null"]["max_unfinished_effective"] == 5
    assert out["to_default"] == [True, False, False], "NULL follows max_unfinished_default (5)"
    assert out["listed"]["unfinished"] == 5 and out["listed"]["jobs"] == 5


def test_a_patch_changes_the_label_the_models_and_the_wallet_limits_and_refuses_what_is_not_valid(db):
    async def scenario(env):
        h = await env.admin_headers()
        key = await make(env, h, label="old")
        url = "/admin/keys/%s" % key["id"]
        patch = lambda body: env.client.patch(url, json=body, headers=h)  # noqa: E731
        out = {"label": await patch({"label": "new", "models": ["fake-video", "fake-image"]}),
               "wallet": await patch({"wallets": [{"provider": "fake", "kind": "image", "mode": "limit",
                                                   "total_limit": 9}]}),
               "closed": await patch({"wallets": [{"provider": "fake", "kind": "image", "mode": "closed"}]})}
        out["bad"] = [await patch(b) for b in ({}, {"label": None}, {"models": []}, {"models": ["  "]},
                                               {"wallets": [{"provider": "ghost", "kind": "video", "mode": "closed"}]},
                                               {"enabled": False})]
        out["ghost"] = await env.client.patch("/admin/keys/%s" % uuid.uuid4(), json={"label": "x"}, headers=h)
        return key, out

    key, out = run_app(db, scenario)
    assert out["label"].json()["label"] == "new" and out["label"].json()["models"] == ["fake-video", "fake-image"]
    assert {w["kind"]: w["total_limit"] for w in out["wallet"].json()["wallets"]}["image"] == 9
    assert wallets_of(db, key["id"])[FAKE_IMAGE] == (0, None)
    for r in out["bad"]:
        assert (r.status_code, code_of(r)) == (400, "input.invalid"), r.text
    assert (out["ghost"].status_code, code_of(out["ghost"])) == (404, "resource.not_found")
    assert db.one("SELECT label, models FROM api_keys WHERE id = %s", uuid.UUID(key["id"])) == (
        "new", ["fake-video", "fake-image"]), "a refused patch changed nothing"


def test_a_disabled_key_is_refused_and_enabling_brings_it_back(db):
    async def scenario(env):
        h = await env.admin_headers()
        key = await make(env, h)
        off = await env.client.post("/admin/keys/%s/disable" % key["id"], headers=h)
        async with tx(env.pool) as conn:
            with pytest.raises(keys.KeyDisabled):
                await keys.lookup(conn, key["key"])
        on = await env.client.post("/admin/keys/%s/enable" % key["id"], headers=h)
        async with tx(env.pool) as conn:
            alive = (await keys.lookup(conn, key["key"])).id
        ghost = await env.client.post("/admin/keys/%s/disable" % uuid.uuid4(), headers=h)
        return key, off, on, alive, ghost

    key, off, on, alive, ghost = run_app(db, scenario)
    assert off.json() == {"id": key["id"], "enabled": False} and on.json() == {"id": key["id"], "enabled": True}
    assert alive == uuid.UUID(key["id"]) and ghost.status_code == 404


@mutation(ROUTES, DELETE_GATE, DELETE_ALWAYS_DISABLES, expect="the unused key is deleted")
def test_delete_removes_a_key_nothing_used_and_only_disables_one_with_a_job(db):
    """A key nothing used is deleted; a key with a job is only disabled."""
    async def scenario(env):
        h = await env.admin_headers()
        unused = await make(env, h, label="unused")
        used = await make(env, h, label="used", wallets=[{"provider": "fake", "kind": "video", "mode": "unlimited"}])
        await store.create_job(env.pool, new_job(uuid.UUID(used["id"])), env.settings)    # a charge: a ledger row
        return (await env.client.delete("/admin/keys/%s" % unused["id"], headers=h),
                await env.client.delete("/admin/keys/%s" % used["id"], headers=h),
                await env.client.delete("/admin/keys/%s" % uuid.uuid4(), headers=h), unused, used)

    gone, kept, ghost, unused, used = run_app(db, scenario)
    assert gone.json() == {"id": unused["id"], "result": "deleted"}, "the unused key is deleted"
    assert db.one("SELECT count(*) FROM api_keys WHERE id = %s", uuid.UUID(unused["id"]))[0] == 0, (
        "the unused key is deleted")
    assert db.one("SELECT count(*) FROM key_wallets WHERE key_id = %s", uuid.UUID(unused["id"]))[0] == 0
    assert kept.json() == {"id": used["id"], "result": "disabled"}
    assert db.one("SELECT enabled FROM api_keys WHERE id = %s", uuid.UUID(used["id"])) == (False,)
    assert db.one("SELECT count(*) FROM ledger WHERE key_id = %s", uuid.UUID(used["id"]))[0] == 1, "history kept"
    assert ghost.status_code == 404


def test_a_key_with_ledger_history_and_no_job_is_disabled_not_deleted(db):
    """The ledger is the audit trail, so a key it names can never vanish."""
    async def scenario(env):
        h = await env.admin_headers()
        key = await make(env, h)
        db.run("UPDATE key_wallets SET used_day = 3, used_total = 3, day_anchor = (now() AT TIME ZONE 'UTC')::date "
               "WHERE key_id = %s AND kind = 'video'", uuid.UUID(key["id"]))
        db.run("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) VALUES "
               "(%s, 'fake', 'video', 'adjust', 3, (now() AT TIME ZONE 'UTC')::date)", uuid.UUID(key["id"]))
        return key, await env.client.delete("/admin/keys/%s" % key["id"], headers=h)

    key, r = run_app(db, scenario)
    assert r.json()["result"] == "disabled"
    assert db.one("SELECT enabled FROM api_keys WHERE id = %s", uuid.UUID(key["id"])) == (False,)


@mutation(ROUTES, "for s in specs for m in s.catalog()]}", "for s in specs for m in s.catalog()[:1]]}",
          expect="every model of every provider")
def test_the_admin_lists_every_model_of_every_provider_for_the_key_form(db):
    """The key form needs the allow-list choices and the admin holds no API key: the same entries as a key that may
    use every model sees on /v1/models, behind the admin session."""
    async def scenario(env):
        admin = await env.client.get("/admin/models", headers=await env.admin_headers())
        public = await env.client.get("/v1/models", headers=(await env.new_key()).headers)
        return admin, public, await env.client.get("/admin/models")

    admin, public, anonymous = run_app(db, scenario)
    assert anonymous.status_code == 401
    data = admin.json()["data"]
    assert len(data) >= 2 and data == public.json()["data"], "every model of every provider: %r" % data
    assert all(m["prices"] is not None and "owned_by" in m for m in data)



STRIP_ON_CREATE = "no label\n        return value.strip()"
STRIP_ON_PATCH = "        return None if value is None else value.strip()"


@mutation(ROUTES, STRIP_ON_CREATE, "no label\n        return value", expect="a label of spaces is stored as it came")
@mutation(ROUTES, STRIP_ON_PATCH, "        return value", expect="a patch to spaces is stored as it came")
def test_a_label_is_stored_without_its_edges_and_a_label_of_spaces_is_no_label(db):
    async def scenario(env):
        h = await env.admin_headers()
        blank = await make(env, h, label="   ")
        padded = await make(env, h, label=" a ")
        edge = await env.client.post("/admin/keys", json={"label": " " + "x" * 100}, headers=h)
        over = await env.client.post("/admin/keys", json={"label": "x" * 101}, headers=h)
        # the key patched to spaces, and one patched to a padded name
        named = await make(env, h, label="old")
        patch = lambda body: env.client.patch("/admin/keys/%s" % named["id"], json=body, headers=h)  # noqa: E731
        cleared = await patch({"label": "  "})
        trimmed = await patch({"label": "  b  "})
        return blank, padded, edge, over, cleared, trimmed, named

    blank, padded, edge, over, cleared, trimmed, named = run_app(db, scenario)
    label = lambda made: db.one("SELECT label FROM api_keys WHERE id = %s", uuid.UUID(made["id"]))[0]  # noqa: E731
    assert blank["label"] == "" and label(blank) == "", "a label of spaces is stored as it came: %r" % label(blank)
    assert padded["label"] == "a" and label(padded) == "a"
    assert (edge.status_code, code_of(edge)) == (400, "input.invalid"), "the limit counts the label as sent"
    assert (over.status_code, code_of(over)) == (400, "input.invalid")
    assert cleared.status_code == 200 and cleared.json()["label"] == "" and trimmed.json()["label"] == "b", (
        "a patch to spaces is stored as it came")
    assert label(named) == "b"
