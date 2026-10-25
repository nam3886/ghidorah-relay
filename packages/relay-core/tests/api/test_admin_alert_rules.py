"""Alert rules and resolved today: the admin turns an alert kind off or mutes it for a while, and the hub's list and
the stats follow; resolved today counts the groups acknowledged since the billing day began. Billing rows are planted
as `test_admin_alerts.py` does, unclear rows as `test_admin_alert_groups.py` does. The fake registry has no account,
so its live `accounts_low` condition is always raised: the live kind every test here turns off or mutes."""

import inspect
import re
import time

from apikit import ADMIN_USER, run_app
from test_admin_alert_groups import plant_unclear, stamp
from test_admin_alerts import plant

from relay_core import alerts
from relay_core.admin import routes_alert_groups, routes_stats
from relay_core.testing.mutation import mutation

ALERTS = "packages/relay-core/src/relay_core/alerts.py"
ROUTES = "packages/relay-core/src/relay_core/admin/routes_alert_groups.py"
STATS = "packages/relay-core/src/relay_core/admin/routes_stats.py"
HOUR_MS = 3_600_000


def rule_url(source: str, kind: str) -> str:
    return "/admin/alerts/rules/%s/%s" % (source, kind)


def kinds_of(items) -> list[str]:
    return sorted(i["kind"] for i in items)


def test_every_kind_the_relay_raises_has_one_rule_and_no_rule_names_a_kind_it_does_not_raise():
    raised = set(re.findall(r'_alert\("(\w+)"', inspect.getsource(routes_stats)))
    rules = routes_alert_groups.RULES
    assert {r.kind for r in rules if r.source == "live"} == raised
    assert {r.kind for r in rules if r.source == "stored"} == set(alerts.kinds())
    assert len({(r.source, r.kind) for r in rules}) == len(rules)


def test_the_rules_list_every_kind_with_its_threshold_on_and_unmuted_until_changed(db):
    async def scenario(env):
        return await env.client.get("/admin/alerts/rules", headers=await env.admin_headers())

    items = run_app(db, scenario).json()["items"]
    by = {(i["source"], i["kind"]): i for i in items}
    assert len(items) == len(routes_alert_groups.RULES)
    assert by[("live", "accounts_low")]["thresholds"] == [{"setting": "alert_accounts_low", "value": 3}]
    assert by[("live", "disk_low")]["thresholds"] == [{"setting": "alert_disk_free_gb", "value": 10}]
    assert by[("stored", "pool_drift")]["thresholds"] == [{"setting": "alert_pool_drift", "value": 5}]
    assert by[("live", "worker_stale")]["fixed"] == "no report for 60 s"
    assert by[("live", "worker_stale")]["thresholds"] == [] and by[("stored", "clamp")]["severity"] == "error"
    assert all(i["enabled"] and i["muted_until_ms"] is None and i["updated_by"] is None for i in items)


@mutation(ALERTS, "AND r.muted_until > clock_timestamp()) AS muted_until", ") AS muted_until",
          expect="an ended mute shows no end")
def test_a_muted_stored_kind_stays_listed_with_its_end_and_an_ended_mute_shows_none(db):
    k, _clamp, _drift = plant(db)
    plant_unclear(db, k, "Odd reply")

    async def scenario(env):
        h = await env.admin_headers()
        before = int(time.time() * 1000)
        muted = await env.client.post(rule_url("stored", "unclear_reply"), json={"mute_minutes": 60}, headers=h)
        listed = await env.client.get("/admin/alerts/groups", headers=h)
        db.run("UPDATE alert_rules SET muted_until = clock_timestamp() - interval '1 minute'")
        ended = await env.client.get("/admin/alerts/groups", headers=h)
        rules = await env.client.get("/admin/alerts/rules", headers=h)
        return before, muted, listed, ended, rules

    before, muted, listed, ended, rules = run_app(db, scenario)
    assert muted.status_code == 200, muted.text
    end = muted.json()["muted_until_ms"]
    assert before + HOUR_MS - 60_000 < end < before + HOUR_MS + 60_000
    assert muted.json()["updated_by"] == ADMIN_USER and muted.json()["enabled"] is True
    by = {i["kind"]: i for i in listed.json()["items"]}
    assert by["unclear_reply"]["muted_until_ms"] == end
    assert by["clamp"]["muted_until_ms"] is None, "a mute holds one kind only"
    assert [i["muted_until_ms"] for i in ended.json()["items"]] == [None] * 3, "an ended mute shows no end"
    unclear = next(i for i in rules.json()["items"] if i["kind"] == "unclear_reply")
    assert unclear["muted_until_ms"] is None and unclear["updated_by"] == ADMIN_USER


@mutation(ALERTS, "    where = [NOT_OFF] + (", "    where = [] + (", expect="a kind turned off is still listed")
@mutation(ALERTS, '        elif rule["enabled"]:', "        else:", expect="a live kind turned off is still listed")
def test_a_kind_turned_off_leaves_the_list_and_the_stats_keeps_its_detail_and_comes_back_when_on(db):
    k, _clamp, _drift = plant(db)
    unclear = plant_unclear(db, k, "Odd reply")
    group = alerts.build("fake", "Odd reply").group_id

    async def scenario(env):
        h = await env.admin_headers()
        post = lambda s, kind, body: env.client.post(rule_url(s, kind), json=body, headers=h)  # noqa: E731
        stats = lambda: env.client.get("/admin/stats", headers=h)  # noqa: E731
        groups = lambda: env.client.get("/admin/alerts/groups", headers=h)  # noqa: E731
        before = (await stats()).json()
        await post("stored", "unclear_reply", {"enabled": False})
        await post("live", "accounts_low", {"enabled": False})
        off = (await groups()).json(), (await stats()).json()
        detail = await env.client.get("/admin/alerts/groups/unclear_reply/%s" % group, headers=h)
        await post("stored", "unclear_reply", {"enabled": True})
        await post("live", "accounts_low", {"enabled": True})
        on = (await groups()).json(), (await stats()).json()
        return before, off, detail, on

    before, (off_groups, off_stats), detail, (on_groups, on_stats) = run_app(db, scenario)
    assert "accounts_low" in kinds_of(before["alerts"]) and before["muted_alerts"] == []
    assert kinds_of(off_groups["items"]) == ["clamp", "drift"], "a kind turned off is still listed"
    assert "accounts_low" not in kinds_of(off_stats["alerts"] + off_stats["muted_alerts"]), \
        "a live kind turned off is still listed"
    assert detail.status_code == 200 and detail.json()["newest_id"] == unclear, "its rows are still read"
    assert kinds_of(on_groups["items"]) == ["clamp", "drift", "unclear_reply"]
    assert "accounts_low" in kinds_of(on_stats["alerts"])


@mutation(ALERTS, 'if rule is None or (rule["enabled"] and rule["muted_until_ms"] is None):',
          'if rule is None or rule["enabled"]:', expect="a muted live kind is still raised")
@mutation(STATS, '"muted_alerts": muted,', '"muted_alerts": [],', expect="the muted condition is listed apart")
def test_a_muted_live_kind_is_listed_apart_with_its_end_and_raised_again_once_the_mute_ends(db):
    async def scenario(env):
        h = await env.admin_headers()
        await env.client.post(rule_url("live", "accounts_low"), json={"mute_minutes": 30}, headers=h)
        muted = (await env.client.get("/admin/stats", headers=h)).json()
        await env.client.post(rule_url("live", "accounts_low"), json={"mute_minutes": 0}, headers=h)
        unmuted = (await env.client.get("/admin/stats", headers=h)).json()
        return muted, unmuted

    muted, unmuted = run_app(db, scenario)
    assert "accounts_low" not in kinds_of(muted["alerts"]), "a muted live kind is still raised"
    apart = [a for a in muted["muted_alerts"] if a["kind"] == "accounts_low"]
    assert [a["provider"] for a in apart] == ["fake"], "the muted condition is listed apart"
    assert apart[0]["muted_until_ms"] > time.time() * 1000
    assert "accounts_low" in kinds_of(unmuted["alerts"]) and unmuted["muted_alerts"] == []


@mutation(ALERTS, "IS NULL THEN r.muted_until WHEN", "IS NULL THEN NULL WHEN",
          expect="turning a kind on keeps its mute")
@mutation(ROUTES, "not 0 <= body.mute_minutes <= alerts.max_mute(source, kind)", "not 0 <= body.mute_minutes",
          expect="a mute of more than a week")
@mutation(ROUTES, "    if (source, kind) not in _RULE_KEYS:", "    if False:", expect="an unknown rule")
def test_a_rule_change_names_a_known_kind_and_a_mute_from_0_to_a_week_and_keeps_what_it_does_not_name(db):
    async def scenario(env):
        h = await env.admin_headers()
        post = lambda s, kind, body: env.client.post(rule_url(s, kind), json=body, headers=h)  # noqa: E731
        u = "unclear_reply"
        refused = [await post("live", "nope", {"enabled": False}), await post("other", u, {"enabled": False}),
                   await post("stored", u, {}), await post("stored", u, {"mute_minutes": -1}),
                   await post("stored", u, {"mute_minutes": 7 * 24 * 60 + 1}),
                   await post("stored", u, {"mute_minutes": "60"}), await post("stored", u, {"x": 1})]
        await post("stored", "unclear_reply", {"mute_minutes": 60})
        kept = (await post("stored", "unclear_reply", {"enabled": True})).json()
        week = (await post("stored", "unclear_reply", {"mute_minutes": 7 * 24 * 60})).json()
        off = (await post("stored", "unclear_reply", {"enabled": False, "mute_minutes": 0})).json()
        return refused, kept, week, off

    refused, kept, week, off = run_app(db, scenario)
    codes = [(r.status_code, r.json().get("error", {}).get("code")) for r in refused]
    assert codes[:2] == [(404, "resource.not_found")] * 2, "an unknown rule: %s" % codes
    assert [c for c, _ in codes[2:]] == [400] * 5, "a mute of more than a week: %s" % codes
    assert kept["muted_until_ms"] is not None, "turning a kind on keeps its mute"
    assert week["muted_until_ms"] > time.time() * 1000 + 6 * 24 * HOUR_MS
    assert (off["enabled"], off["muted_until_ms"]) == (False, None)


MONEY_OFF = '    if body.enabled is False and alerts.is_money(source, kind):'
MONEY_DAY = "MONEY_MUTE_MAX_MIN if is_money(source, kind) else MAX_MUTE_MIN"


@mutation(ROUTES, MONEY_OFF, "    if False:", expect="a money alert can never be turned off")
@mutation(ALERTS, MONEY_DAY, "MAX_MUTE_MIN", expect="a money alert is muted a day at most")
@mutation(ALERTS, "AND r.kind NOT IN ('clamp', 'drift', 'cost_mismatch'))", ")",
          expect="a money row that says off is still listed")
@mutation(ALERTS, 'return source == "stored" and kind in MONEY_KINDS', "return False",
          expect="a money alert can never be turned off")
def test_a_money_alert_is_never_off_is_muted_a_day_at_most_and_stays_listed_with_its_mute(db):
    k, clamp, drift = plant(db)

    async def scenario(env):
        h = await env.admin_headers()
        post = lambda s, kind, body: env.client.post(rule_url(s, kind), json=body, headers=h)  # noqa: E731
        refused = [await post("stored", "clamp", {"enabled": False}),
                   await post("stored", "drift", {"enabled": False, "mute_minutes": 60}),
                   await post("stored", "cost_mismatch", {"enabled": False}),
                   await post("stored", "clamp", {"mute_minutes": 24 * 60 + 1}),
                   await post("stored", "cost_mismatch", {"mute_minutes": 24 * 60 + 1})]
        day = await post("stored", "clamp", {"mute_minutes": 24 * 60})
        on = await post("stored", "drift", {"enabled": True})
        pool_off = await post("stored", "pool_drift", {"enabled": False})     # not money: it may be turned off
        listed = (await env.client.get("/admin/alerts/groups", headers=h)).json()
        rules = (await env.client.get("/admin/alerts/rules", headers=h)).json()
        # a row that says off (planted past the route) never takes a money kind off the list
        db.run("UPDATE alert_rules SET enabled = false WHERE kind = 'drift'")
        planted = (await env.client.get("/admin/alerts/groups", headers=h)).json()
        return refused, day, on, listed, rules, planted, pool_off

    refused, day, on, listed, rules, planted, pool_off = run_app(db, scenario)
    assert [(r.status_code, r.json().get("error", {}).get("param")) for r in refused[:3]] == \
        [(400, "enabled")] * 3, "a money alert can never be turned off"
    assert [r.status_code for r in refused[3:]] == [400, 400], "a money alert is muted a day at most"
    assert pool_off.status_code == 200 and pool_off.json()["enabled"] is False
    assert day.status_code == 200 and day.json()["muted_until_ms"] < time.time() * 1000 + 25 * HOUR_MS
    assert on.status_code == 200 and on.json()["enabled"] is True
    by = {i["kind"]: i for i in listed["items"]}
    assert by["clamp"]["newest_id"] == clamp and by["clamp"]["muted_until_ms"] == day.json()["muted_until_ms"], \
        "a muted money alert stays listed with its end"
    assert by["drift"]["newest_id"] == drift and by["drift"]["muted_until_ms"] is None
    money = {(i["source"], i["kind"]): (i["money"], i["max_mute_minutes"]) for i in rules["items"]}
    assert money[("stored", "clamp")] == money[("stored", "drift")] == money[("stored", "cost_mismatch")] == (
        True, 24 * 60)
    assert money[("stored", "pool_drift")] == (False, 7 * 24 * 60)
    assert money[("stored", "unclear_reply")] == (False, 7 * 24 * 60) and money[("live", "drift")][0] is False
    assert kinds_of(planted["items"]) == ["clamp", "drift"], "a money row that says off is still listed"


@mutation(ALERTS, "FROM billing_alerts WHERE acknowledged_at >= %(start)s", "FROM billing_alerts WHERE acknowledged_at "
          "IS NOT NULL AND %(start)s IS NOT NULL", expect="a row acknowledged before today")
@mutation(ALERTS, "FROM unclear_replies WHERE acknowledged_at >= %(start)s) r",
          "FROM unclear_replies WHERE acknowledged_at IS NOT NULL) r", expect="a row acknowledged before today")
@mutation(ALERTS, "GROUP BY kind, grp ORDER BY max(at) DESC,", "GROUP BY kind, grp ORDER BY max(at),",
          expect="the newest first")
def test_resolved_today_counts_the_groups_acknowledged_since_the_billing_day_began_newest_first(db):
    k, clamp, drift = plant(db)
    old = plant_unclear(db, k, "First wording")
    new = plant_unclear(db, k, "Second wording")
    plant_unclear(db, k, "Still open wording")
    for table, row in (("billing_alerts", clamp), ("unclear_replies", old)):
        db.run("UPDATE %s SET acknowledged_at = clock_timestamp() - interval '2 days', acknowledged_by = 'bob' "
               "WHERE id = %%s" % table, row)
    stamp(db, "billing_alerts", drift)
    stamp(db, "unclear_replies", new)

    async def scenario(env):
        return await env.client.get("/admin/alerts/resolved", headers=await env.admin_headers())

    body = run_app(db, scenario).json()
    assert body["count"] == 2, "a row acknowledged before today: %s" % body
    assert [(i["kind"], i["title"]) for i in body["items"]] == [
        ("unclear_reply", "second wording"), ("drift", body["items"][1]["title"])], "the newest first"
    assert [(i["resolved_by"], i["resolved_rows"]) for i in body["items"]] == [("ann", 1), ("ann", 1)]
    assert body["items"][0]["resolved_ms"] >= body["items"][1]["resolved_ms"] >= body["day_start_ms"]
    assert body["tz"] == "UTC"
