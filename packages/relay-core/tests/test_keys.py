"""API keys: hashed `rk-` keys, fail-closed lookup, the model allow-list, wallets made
with the key, and delete-or-disable. Everything runs as `relay_api`, the role that serves these operations."""

import asyncio
import re
import uuid

import psycopg
import pytest

from relay_core import billing, errors, keys
from relay_core.billing import KeyDisabled
from relay_core.db import open_pool, tx
from relay_core.keys import CLOSED, KeyRefused, model_allowed
from relay_core.testing.mutation import mutation

K = "packages/relay-core/src/relay_core/keys.py"
CATALOG = {"m-2.5": "m_v25", "m-2.5-30s": "m_v25", "m_v25": "m_v25", "m-2.0": "m_v20", "img": "img"}


def run(db, scenario):
    async def main():
        pool = await open_pool(db.api_dsn)
        try:
            return await scenario(pool)
        finally:
            await pool.close()

    return asyncio.run(main())


async def make(pool, **kw):
    async with tx(pool) as conn:
        return await keys.create_key(conn, **{"declared": (), **kw})


async def look(pool, raw):
    async with tx(pool) as conn:
        return await keys.lookup(conn, raw)


# ---- creation ------------------------------------------------------------------------------------------------------
def test_a_key_is_shown_once_and_only_its_hash_and_lookup_prefix_are_stored(db):
    async def scenario(pool):
        a, b = await make(pool, label="a"), await make(pool)
        assert re.fullmatch(r"rk-[A-Za-z0-9_-]{43}", a.raw) and a.raw != b.raw
        row = db.one("SELECT key_hash, key_prefix, label FROM api_keys WHERE id = %s", a.id)
        assert row == (keys.hash_key(a.raw), a.raw[:11], "a")
        assert len(row[0]) == 64 and a.raw not in "".join(map(str, row))
        assert (await look(pool, a.raw)).id == a.id

    run(db, scenario)


def test_wallets_are_created_with_the_limits_chosen_and_a_new_provider_is_closed_on_the_key(db):
    async def scenario(pool):
        k = await make(pool, wallets={("p1", "video"): (5, 100), ("p1", "image"): CLOSED,
                                      ("p2", "video"): (None, None)})
        rows = db.all("SELECT provider, kind, day_limit, total_limit FROM key_wallets WHERE key_id = %s "
                      "ORDER BY provider, kind", k.id)
        assert rows == [("p1", "image", 0, None), ("p1", "video", 5, 100), ("p2", "video", None, None)]
        async with tx(pool) as conn:                   # a provider that registers later
            await billing.ensure_wallets(conn, "p3", ["video"])
        assert db.one("SELECT day_limit FROM key_wallets WHERE key_id = %s AND provider = %s", k.id, "p3") == (0,)
        with pytest.raises(billing.QuotaExceeded):     # closed means closed, even for a 0-point job
            async with tx(pool) as conn:
                await billing.charge(conn, k.id, "p3", "video", 0, uuid.uuid4())

    run(db, scenario)


def test_declared_pairs_are_required_to_make_a_key(db):
    async def scenario(pool):
        async with tx(pool) as conn:
            with pytest.raises(TypeError, match="declared"):
                await keys.create_key(conn)
        assert db.one("SELECT count(*) FROM api_keys") == (0,)

    run(db, scenario)


@mutation(K, "    billing.require_tx(conn)\n", "    pass\n", expect="DID NOT RAISE NotInTransaction")
def test_a_key_is_not_made_outside_a_transaction(db):
    async def scenario(pool):
        async with pool.connection() as conn:             # autocommit: each statement would commit alone
            with pytest.raises(billing.NotInTransaction):
                await keys.create_key(conn, declared=[("p1", "video")])
        assert db.one("SELECT count(*) FROM api_keys") == (0,)

    run(db, scenario)


def test_a_declared_pair_the_admin_left_out_gets_a_closed_wallet(db):
    async def scenario(pool):
        k = await make(pool, wallets={("p1", "video"): (5, None)},
                       declared=[("p1", "video"), ("p1", "image"), ("p2", "video")])
        rows = db.all("SELECT provider, kind, day_limit FROM key_wallets WHERE key_id = %s ORDER BY 1, 2", k.id)
        assert rows == [("p1", "image", 0), ("p1", "video", 5), ("p2", "video", 0)]

    run(db, scenario)


def test_models_must_name_something(db):
    async def scenario(pool):
        for bad in ([], [""], ["  "], ["m", ""]):
            with pytest.raises(ValueError, match="at least one model"):
                await make(pool, models=bad)
        k = await make(pool)
        assert (await look(pool, k.raw)).models == ("*",)
        async with tx(pool) as conn:
            assert await keys.set_models(conn, k.id, [" m-2.5 ", "img"])
            with pytest.raises(ValueError):
                await keys.set_models(conn, k.id, [])
        assert (await look(pool, k.raw)).models == ("m-2.5", "img")

    run(db, scenario)


# ---- lookup is fail-closed -----------------------------------------------------------------------------------------
@mutation(K, "if not enabled:", "if False:", expect="DID NOT RAISE KeyDisabled")
def test_an_unknown_or_malformed_key_gets_one_refusal_and_a_disabled_one_is_not_served(db):
    async def scenario(pool):
        k = await make(pool)
        async with tx(pool) as conn:
            assert await keys.set_enabled(conn, k.id, False)
        refusals = []
        for raw in ("rk-" + "A" * 43, "", "rk-", k.raw[:-1], k.raw + "x", k.raw.replace("rk-", "dk-", 1), None, 7,
                    "rk-" + "!" * 43, " " + k.raw):
            with pytest.raises(KeyRefused) as err:
                await look(pool, raw)
            assert not isinstance(err.value, KeyDisabled)
            refusals.append((err.value.code, str(err.value)))
        assert set(refusals) == {("auth.invalid_key", "")}
        with pytest.raises(KeyDisabled):
            await look(pool, k.raw)
        async with tx(pool) as conn:
            assert await keys.set_enabled(conn, k.id, True)
        assert (await look(pool, k.raw)).id == k.id        # enabling again restores it

    run(db, scenario)


@mutation(K, "raise KeyDisabled(key_id=key_id)", "raise KeyRefused()", expect="relay_core.keys.KeyRefused")
def test_the_holder_of_a_disabled_key_is_told_403_key_disabled(db):
    async def scenario(pool):
        k = await make(pool)
        async with tx(pool) as conn:
            await keys.set_enabled(conn, k.id, False)
        with pytest.raises(KeyDisabled) as err:
            await look(pool, k.raw)
        assert (err.value.code, errors.http_status(err.value.code)) == ("auth.key_disabled", 403)
        assert errors.rule("auth.invalid_key").http_status == 401

    run(db, scenario)


@mutation(K, "        if hmac.compare_digest(stored, wanted):",
          "        if not enabled:\n            raise KeyDisabled()\n        if hmac.compare_digest(stored, wanted):",
           expect="relay_core.billing.KeyDisabled")
def test_a_disabled_key_is_revealed_only_after_the_whole_hash_matched(db):
    async def scenario(pool):
        k = await make(pool)
        async with tx(pool) as conn:
            await keys.set_enabled(conn, k.id, False)
        forged = k.raw[:keys.LOOKUP_LEN] + "A" * (len(k.raw) - keys.LOOKUP_LEN)   # the right prefix, the wrong key
        with pytest.raises(KeyRefused) as err:
            await look(pool, forged)
        assert not isinstance(err.value, KeyDisabled)       # a non-holder learns nothing about the real key

    run(db, scenario)


@mutation(K, "if hmac.compare_digest(stored, wanted):", "if True:", expect="DID NOT RAISE KeyRefused")
def test_the_prefix_alone_never_opens_a_key(db):
    async def scenario(pool):
        k = await make(pool)
        forged = k.raw[:keys.LOOKUP_LEN] + "A" * (len(k.raw) - keys.LOOKUP_LEN)   # same prefix, right shape
        assert forged != k.raw and forged[:11] == k.raw[:11]
        with pytest.raises(KeyRefused):
            await look(pool, forged)
        assert (await look(pool, k.raw)).id == k.id

    run(db, scenario)


def test_a_database_error_during_lookup_is_no_access_not_a_pass(db):
    class Broken:
        async def execute(self, *a, **kw):
            raise psycopg.OperationalError("down")

    with pytest.raises(psycopg.OperationalError):
        asyncio.run(keys.lookup(Broken(), "rk-" + "A" * 43))


# ---- data on the key -----------------------------------------------------------------------------------------------
def test_max_unfinished_is_data_on_the_key_and_enable_disable_report_a_missing_key(db):
    async def scenario(pool):
        k = await make(pool, max_unfinished=7)
        assert (await look(pool, k.raw)).max_unfinished == 7
        async with tx(pool) as conn:
            assert await keys.set_max_unfinished(conn, k.id, None)
        assert (await look(pool, k.raw)).max_unfinished is None
        with pytest.raises(psycopg.errors.CheckViolation):
            async with tx(pool) as conn:
                await keys.set_max_unfinished(conn, k.id, -1)
        async with tx(pool) as conn:
            ghost = uuid.uuid4()
            assert not await keys.set_enabled(conn, ghost, False)
            assert not await keys.set_max_unfinished(conn, ghost, 1)

    run(db, scenario)


# ---- the allow-list ------------------------------------------------------------------------------------------------
@mutation(K, "return canonical is not None and any(catalog.get(a) == canonical for a in allowed)", "return False",
           expect="model_allowed(allow, \"m-2.5-30s\", CATALOG)")
def test_allowing_a_model_allows_its_aliases_and_variants_and_nothing_else():
    allow = ["m-2.5"]
    assert model_allowed(allow, "m-2.5", CATALOG)
    assert model_allowed(allow, "m-2.5-30s", CATALOG)       # a variant of the same model
    assert model_allowed(allow, "m_v25", CATALOG)           # the canonical name
    assert model_allowed(["m_v25"], "m-2.5-30s", CATALOG)   # the list may hold the canonical name too
    assert not model_allowed(allow, "m-2.0", CATALOG)       # another model
    assert not model_allowed(allow, "img", CATALOG)
    assert not model_allowed(allow, "unheard-of", CATALOG)  # unknown to the catalog: only an exact listing matches
    assert model_allowed(["unheard-of"], "unheard-of", CATALOG)
    assert not model_allowed(["unheard-of"], "m-2.5", CATALOG)


def test_star_allows_everything_and_an_empty_list_nothing():
    assert model_allowed(["*"], "anything", CATALOG) and model_allowed(["img", "*"], "m-2.0", CATALOG)
    assert not model_allowed([], "m-2.5", CATALOG)


# ---- delete or disable ---------------------------------------------------------------------------------------
@mutation(K, "except psycopg.errors.ForeignKeyViolation:", "except psycopg.errors.NotNullViolation:",
           expect="violates foreign key constraint \"jobs_key_id_fkey\"")
def test_only_a_key_without_history_is_deleted_the_rest_are_disabled(db):
    async def scenario(pool):
        clean = await make(pool, wallets={("p1", "video"): (5, None)})
        with_job = await make(pool, wallets={("p1", "video"): (5, None)})
        ledger_only = await make(pool, wallets={("p1", "video"): (5, None)})
        db.job(with_job.id)
        with db.connect() as c:                             # an `adjust` row: ledger history with no job
            c.execute("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) "
                      "VALUES (%s, 'p1', 'video', 'adjust', 0, current_date)", (ledger_only.id,))
        results = {}
        for name, k in (("clean", clean), ("job", with_job), ("ledger", ledger_only)):
            async with tx(pool) as conn:
                results[name] = await keys.delete_key(conn, k.id)
        async with tx(pool) as conn:
            results["ghost"] = await keys.delete_key(conn, uuid.uuid4())
        assert results == {"clean": "deleted", "job": "disabled", "ledger": "disabled", "ghost": "missing"}
        assert db.one("SELECT count(*) FROM api_keys WHERE id = %s", clean.id) == (0,)
        assert db.one("SELECT count(*) FROM key_wallets WHERE key_id = %s", clean.id) == (0,)   # went with the key
        assert sorted(db.all("SELECT id, enabled FROM api_keys WHERE id IN (%s, %s)", with_job.id, ledger_only.id),
                      key=lambda r: r[0]) == sorted([(with_job.id, False), (ledger_only.id, False)], key=lambda r: r[0])
        assert db.one("SELECT count(*) FROM jobs WHERE key_id = %s", with_job.id) == (1,)
        with pytest.raises(KeyDisabled):
            await look(pool, with_job.raw)

    run(db, scenario)


def test_a_refused_delete_does_not_poison_the_callers_transaction(db):
    async def scenario(pool):
        k = await make(pool, wallets={("p1", "video"): (5, None)})
        db.job(k.id)
        other = await make(pool)
        async with tx(pool) as conn:
            assert await keys.delete_key(conn, k.id) == "disabled"
            assert await keys.set_enabled(conn, other.id, False)    # the same transaction carries on
        assert db.one("SELECT enabled FROM api_keys WHERE id = %s", other.id) == (False,)

    run(db, scenario)

