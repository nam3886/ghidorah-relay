"""Admin billing alerts (contracts 4.1, 15): a clamp and a drift are listed, acknowledged once by a named operator, and
the api can change nothing else of an alert."""

import psycopg
import pytest
from apikit import ADMIN_USER, run_app

from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_alerts.py"
MIGRATION = "packages/relay-core/src/relay_core/db/migrations/0012_billing_alerts_acknowledge.sql"
KEEP_FIRST = "acknowledged_at = COALESCE(acknowledged_at, clock_timestamp()), "
KEEP_LAST = "acknowledged_at = clock_timestamp(), "
GRANT = "GRANT UPDATE (acknowledged_at, acknowledged_by) ON billing_alerts"
GRANT_MORE = "GRANT UPDATE (acknowledged_at, acknowledged_by, amount) ON billing_alerts"


def code_of(r) -> str:
    return r.json()["error"]["code"]


def plant(db) -> tuple:
    """A clamp (it names a ledger row) and a drift (it names none), newest last."""
    key = db.key(("fake", "video", None, None))
    ledger = db.one("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) "
                    "VALUES (%s, 'fake', 'video', 'adjust', 0, current_date) RETURNING id", key)[0]
    clamp = db.one("INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, "
                   "used_total) VALUES ('clamp', %s, 'fake', 'video', %s, 5, 2, 3) RETURNING id", key, ledger)[0]
    drift = db.one("INSERT INTO billing_alerts (alert, key_id, provider, kind, amount, used_day, used_total, "
                   "detail) VALUES ('drift', %s, 'fake', 'video', 0, 1, 9, 'used_total 9, ledger says 0') "
                   "RETURNING id", key)[0]
    return key, clamp, drift


@mutation(ROUTES, '"open": " WHERE acknowledged_at IS NULL"', '"open": ""',
          expect="an acknowledged alert leaves the open list")
@mutation(ROUTES, '"acknowledged": " WHERE acknowledged_at IS NOT NULL"', '"acknowledged": ""',
          expect="only acknowledged alerts")
def test_a_clamp_and_a_drift_are_listed_newest_first_and_acknowledging_one_moves_it_out_of_the_open_list(db):
    key, clamp, drift = plant(db)

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q="": env.client.get("/admin/alerts" + q, headers=h)  # noqa: E731
        out = {"open": await get()}
        out["ack"] = await env.client.post("/admin/alerts/%d/acknowledge" % clamp, headers=h)
        out["after"] = await get()
        out["acknowledged"] = await get("?status=acknowledged")
        out["all"] = await get("?status=all")
        out["bogus"] = await get("?status=bogus")
        out["limit"] = await get("?limit=0")
        return out

    out = run_app(db, scenario)
    assert [(a["id"], a["alert"]) for a in out["open"].json()["items"]] == [(drift, "drift"), (clamp, "clamp")]
    first = out["open"].json()["items"][0]
    assert first["detail"] == "used_total 9, ledger says 0" and first["used_total"] == 9 and first["ledger_id"] is None
    assert first["key_id"] == str(key) and first["acknowledged_ms"] is None and isinstance(first["at_ms"], int)
    acked = out["ack"].json()
    assert acked["id"] == clamp and acked["acknowledged_by"] == ADMIN_USER and isinstance(acked["acknowledged_ms"], int)
    assert [a["id"] for a in out["after"].json()["items"]] == [drift], "an acknowledged alert leaves the open list"
    assert [a["id"] for a in out["acknowledged"].json()["items"]] == [clamp], "only acknowledged alerts"
    assert [a["id"] for a in out["all"].json()["items"]] == [drift, clamp]
    assert out["bogus"].status_code == 400 and out["limit"].status_code == 400
    assert db.one("SELECT acknowledged_by FROM billing_alerts WHERE id = %s", drift) == (None,)


@mutation(ROUTES, KEEP_FIRST, KEEP_LAST, expect="keeps the first acknowledge")
def test_a_second_acknowledge_changes_nothing_and_an_unknown_alert_is_404(db):
    _key, clamp, _drift = plant(db)

    async def scenario(env):
        h = await env.admin_headers()
        post = lambda n: env.client.post("/admin/alerts/%d/acknowledge" % n, headers=h)  # noqa: E731
        first = await post(clamp)
        db.run("UPDATE billing_alerts SET acknowledged_by = 'someone-else', "
               "acknowledged_at = acknowledged_at - interval '1 hour' WHERE id = %s", clamp)
        return first, await post(clamp), await post(clamp + 1000), await post(0)

    first, second, ghost, zero = run_app(db, scenario)
    assert second.json()["acknowledged_ms"] == first.json()["acknowledged_ms"] - 3_600_000, (
        "keeps the first acknowledge")
    assert second.json()["acknowledged_by"] == "someone-else"
    assert ghost.status_code == 404 and zero.status_code == 404


@mutation(MIGRATION, GRANT, GRANT_MORE, expect="only the acknowledge columns")
def test_the_api_may_update_the_acknowledge_columns_of_an_alert_and_nothing_else_the_worker_nothing(db):
    _key, clamp, _drift = plant(db)
    with db.connect() as c:
        columns = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                           "WHERE table_schema = 'relay' AND table_name = 'billing_alerts'")]
        got = {role: {col for col in columns if c.execute(
            "SELECT has_column_privilege(%s, 'relay.billing_alerts', %s, 'UPDATE')", (role, col)).fetchone()[0]}
            for role in ("relay_api", "relay_worker")}
    assert got["relay_api"] == {"acknowledged_at", "acknowledged_by"}, "only the acknowledge columns"
    assert got["relay_worker"] == set()

    async def scenario(env):
        for sql in ("UPDATE billing_alerts SET amount = 0 WHERE id = %s", "UPDATE billing_alerts SET alert = 'drift' "
                    "WHERE id = %s", "DELETE FROM billing_alerts WHERE id = %s"):
            async with env.pool.connection() as conn:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute(sql, (clamp,))

    run_app(db, scenario)
    assert db.one("SELECT amount, alert FROM billing_alerts WHERE id = %s", clamp) == (5, "clamp")


ALERTS_PY = "packages/relay-core/src/relay_core/alerts.py"
ITEM_KEYS = ["id", "alert", "key_id", "provider", "kind", "ledger_id", "amount", "used_day", "used_total", "detail",
             "acknowledged_by", "at_ms", "acknowledged_ms"]


@mutation(ALERTS_PY, 'item["at_ms"] = _ms(item.pop("at"))', 'item["at_ms"] = _ms(item["at"])',
          expect="the thirteen keys of an alert, in this order")
def test_an_alert_has_exactly_the_thirteen_keys_the_dashboard_has_always_read(db):
    plant(db)

    async def scenario(env):
        h = await env.admin_headers()
        first = await env.client.get("/admin/alerts", headers=h)
        item = first.json()["items"][0]
        acked = await env.client.post("/admin/alerts/%d/acknowledge" % item["id"], headers=h)
        return first, acked

    first, acked = run_app(db, scenario)
    for item in first.json()["items"] + [acked.json()]:
        assert list(item) == ITEM_KEYS, "the thirteen keys of an alert, in this order"
