"""The alerts hub (contracts 4.8, 16): one list of every stored alert kind in one shape, a detail per group and an
acknowledge per group that respects `through_id` and the alert kind. Billing rows are planted as `test_admin_alerts.py`
does; unclear rows are planted next to a job with the state the test needs.
"""

import logging
import uuid

from apikit import ADMIN_USER, run_app
from jobkit import account, claim, key, submit
from test_admin_alerts import plant

from relay_core import alerts
from relay_core.db import open_pool
from relay_core.providers import Accepted, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSpec, sent
from relay_core.testing.mutation import mutation

ALERTS = "packages/relay-core/src/relay_core/alerts.py"
ROUTES = "packages/relay-core/src/relay_core/admin/routes_alert_groups.py"
MARKER = "PLANTED-MARKER-7f3a"

ACK_UNCLEAR = "WHERE group_id = %s AND id <= %s AND acknowledged_at IS NULL"
ACK_UNCLEAR_ALL = "WHERE group_id = %s AND %s IS NOT NULL AND acknowledged_at IS NULL"
ACK_BILLING = "AND key_id = %s AND provider = %s AND kind = %s AND id <= %s AND acknowledged_at IS NULL"
ACK_BILLING_ALL = "AND key_id = %s AND provider = %s AND kind = %s AND %s IS NOT NULL AND acknowledged_at IS NULL"
ACK_BY_KIND = "acknowledged_by = %s WHERE alert = %s "
ACK_ANY_KIND = "acknowledged_by = %s WHERE %s IS NOT NULL "
OPEN_FILTER = '(">" if status == "open" else "=")'
OPEN_LOOSE = '(">=" if status == "open" else "=")'
KEYS_DISTINCT = "count(DISTINCT j.key_id) AS keys"
KEYS_ROWS = "count(j.key_id) AS keys"
GROUP_FORM = "    if kind not in alerts.kinds() or not alerts.group_form(kind, group):"
NO_GROUP_FORM = "    if kind not in alerts.kinds():"


def plant_job(db, key_id, status="waiting", error_code=None, provider="fake"):
    """A job in a consistent state for the checker: a waiting job is due to poll, a terminal one is finished."""
    job_id = db.job(key_id, provider=provider)
    if status == "waiting":
        db.run("UPDATE jobs SET status = 'waiting', external_ref = id::text, next_poll_at = clock_timestamp() "
               "WHERE id = %s", job_id)
    elif status == "done":
        db.run("UPDATE jobs SET status = 'done', external_ref = id::text, finished_at = clock_timestamp() "
               "WHERE id = %s", job_id)
        db.run("INSERT INTO job_media (job_id, role, idx, object_key, mime, size) "
               "VALUES (%s, 'output', 0, 'k', 'video/mp4', 1)", job_id)
    elif status == "failed":
        db.run("UPDATE jobs SET status = 'failed', error_code = %s, finished_at = clock_timestamp() WHERE id = %s",
               error_code or "render.deadline", job_id)
    return job_id


def plant_unclear(db, key_id, text, *, status="waiting", error_code=None, provider="fake", account_id=None) -> int:
    """One unclear reply on a new job of `key_id`; returns the row id."""
    job_id = plant_job(db, key_id, status, error_code, provider)
    built = alerts.build(provider, text)
    return db.one("INSERT INTO unclear_replies (provider, job_id, account_id, text, wording, group_id) "
                  "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                  provider, job_id, account_id, built.text, built.wording, built.group_id)[0]


def stamp(db, table, row_id) -> None:
    db.run("UPDATE %s SET acknowledged_at = clock_timestamp(), acknowledged_by = 'ann' WHERE id = %%s" % table, row_id)


def group_of(text, provider="fake") -> str:
    return alerts.build(provider, text).group_id


def groups_url(q="") -> str:
    return "/admin/alerts/groups" + q


def detail_url(kind, group, q="") -> str:
    return "/admin/alerts/groups/%s/%s%s" % (kind, group, q)


def code_of(r) -> str:
    return r.json()["error"]["code"]


# ---- the list ---------------------------------------------------------------------------------------------------
@mutation(ALERTS, "SOURCES = (BILLING, UNCLEAR)", "SOURCES = (UNCLEAR,)", expect="a clamp and a drift are missing")
def test_one_list_holds_a_clamp_a_drift_and_an_unclear_group_in_the_common_shape(db):
    k, clamp, drift = plant(db)
    plant_unclear(db, k, "Hmm, order [12] is odd.")

    async def scenario(env):
        return await env.client.get(groups_url(), headers=await env.admin_headers())

    r = run_app(db, scenario)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert sorted(i["kind"] for i in items) == ["clamp", "drift", "unclear_reply"], \
        "a clamp and a drift are missing: %s" % [i["kind"] for i in items]
    by_kind = {i["kind"]: i for i in items}
    assert set(by_kind["unclear_reply"]) == {"kind", "group", "severity", "title", "provider", "first_ms", "last_ms",
                                            "count", "open_count", "keys", "newest_id", "reply", "key_label",
                                            "status", "detail"}
    assert by_kind["clamp"]["group"] == "%s.fake.video" % k and by_kind["clamp"]["newest_id"] == clamp
    assert by_kind["drift"]["newest_id"] == drift and by_kind["clamp"]["keys"] == 1
    unclear = by_kind["unclear_reply"]
    assert unclear["severity"] == "warning" and unclear["title"] == "hmm, order [#] is odd." and unclear["keys"] == 1
    assert unclear["status"] == "open" and unclear["detail"] == detail_url("unclear_reply", unclear["group"])
    assert unclear["count"] == unclear["open_count"] == 1 and isinstance(unclear["first_ms"], int)


def test_open_groups_come_first_then_the_severity_rank_and_the_rank_is_not_the_names_order(db, monkeypatch):
    k, clamp, drift = plant(db)
    plant_unclear(db, k, "Odd reply")
    note = alerts.Source(("note",), "SELECT 'note' AS kind, 'g1' AS grp, 'info' AS severity, 'a note' AS title, "
                         "'fake' AS provider, clock_timestamp() AS first_at, clock_timestamp() AS last_at, "
                         "1 AS count, 1 AS open_count, 1 AS keys, 1 AS newest_id, NULL AS reply, NULL AS key_label")
    monkeypatch.setattr(alerts, "SOURCES", (*alerts.SOURCES, note))
    done = plant_unclear(db, k, "Already seen")
    stamp(db, "unclear_replies", done)

    async def scenario(env):
        h = await env.admin_headers()
        everything = await env.client.get(groups_url("?status=all"), headers=h)
        only_note = await env.client.get(groups_url("?kind=note"), headers=h)
        return everything, only_note

    everything, only_note = run_app(db, scenario)
    order = [(i["kind"], i["status"]) for i in everything.json()["items"]]
    assert order[-1] == ("unclear_reply", "acknowledged"), "acknowledged groups come last: %s" % order
    open_kinds = [kind for kind, status in order if status == "open"]
    assert sorted(open_kinds[:2]) == ["clamp", "drift"]
    assert open_kinds[-2:] == ["unclear_reply", "note"], "severity ranks error, warning, info: %s" % open_kinds
    assert [i["kind"] for i in only_note.json()["items"]] == ["note"]


@mutation(ALERTS, OPEN_FILTER, OPEN_LOOSE, expect="the open list holds an acknowledged group")
def test_the_status_and_kind_filters_pick_open_or_acknowledged_groups(db):
    k, clamp, _drift = plant(db)
    seen = plant_unclear(db, k, "Seen wording")
    plant_unclear(db, k, "Open wording")
    stamp(db, "unclear_replies", seen)
    stamp(db, "billing_alerts", clamp)

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda q: env.client.get(groups_url(q), headers=h)  # noqa: E731
        return await get(""), await get("?status=acknowledged"), await get("?status=all&kind=clamp")

    default, acknowledged, clamps = run_app(db, scenario)
    titles = lambda r: sorted(i["title"] for i in r.json()["items"])  # noqa: E731
    assert [i["status"] for i in default.json()["items"]] == ["open"] * 2, "the open list holds an acknowledged group"
    assert "open wording" in titles(default) and "seen wording" not in titles(default)
    assert sorted(i["kind"] for i in acknowledged.json()["items"]) == ["clamp", "unclear_reply"]
    assert [i["kind"] for i in clamps.json()["items"]] == ["clamp"]


def test_replies_that_differ_only_in_numbers_a_marker_or_a_url_share_a_group_and_providers_never_do(db):
    k = key(db)
    plant_unclear(db, k, "Order [4821] is in, see https://a.example/1 now")
    plant_unclear(db, k, "Order [77] is in, see https://b.example/22 now")
    plant_unclear(db, k, "Order [4821] is in, see https://a.example/1 now", provider="other")

    async def scenario(env):
        return await env.client.get(groups_url(), headers=await env.admin_headers())

    items = run_app(db, scenario).json()["items"]
    assert sorted((i["provider"], i["count"]) for i in items) == [("fake", 2), ("other", 1)]


@mutation(ALERTS, KEYS_DISTINCT, KEYS_ROWS, expect="keys counts rows, not distinct keys")
def test_keys_counts_the_distinct_api_keys_behind_a_group(db):
    one, other = key(db), key(db)
    plant_unclear(db, one, "Same wording")
    plant_unclear(db, one, "Same wording")
    plant_unclear(db, other, "Same wording")

    async def scenario(env):
        return await env.client.get(groups_url(), headers=await env.admin_headers())

    (item,) = run_app(db, scenario).json()["items"]
    assert item["count"] == 3 and item["keys"] == 2, "keys counts rows, not distinct keys: %r" % item["keys"]


NEWEST_REPLY = "WHERE t.id = max(u.id)) AS reply"
LABEL_ELSE_PREFIX = "COALESCE(NULLIF(k.label, ''), k.key_prefix)"


@mutation(ALERTS, NEWEST_REPLY, "WHERE t.id = min(u.id)) AS reply", expect="the newest stored reply")
@mutation(ALERTS, "SELECT left(t.text, %(title_limit)s)", "SELECT t.text", expect="cut like the title")
@mutation(ALERTS, LABEL_ELSE_PREFIX, "k.key_prefix", expect="the key's label")
@mutation(ALERTS, LABEL_ELSE_PREFIX, "k.label", expect="the prefix of a key without a label")
def test_a_reply_group_carries_its_newest_stored_reply_and_a_key_is_named_by_its_label_else_its_prefix(db):
    k, _clamp, _drift = plant(db)
    db.run("UPDATE api_keys SET label = 'Shop A' WHERE id = %s", k)
    bare, _c, _d = plant(db)
    db.run("UPDATE api_keys SET label = '', key_prefix = 'rk-q7mfz2kd' WHERE id = %s", bare)
    plant_unclear(db, k, "Sorry, I can’t create that video [1]")
    plant_unclear(db, k, "Sorry, I CAN’T create that video [2]")
    plant_unclear(db, k, "A long reply" + " that goes on" * 30)
    plant_unclear(db, bare, "Words under a key without a label")

    async def scenario(env):
        h = await env.admin_headers()
        bare_rows = await env.client.get(detail_url("unclear_reply", group_of("Words under a key without a label")),
                                         headers=h)
        return await env.client.get(groups_url(), headers=h), bare_rows

    listed, bare_rows = run_app(db, scenario)
    items = listed.json()["items"]
    [row] = bare_rows.json()["items"]
    assert row["key_label"] == "rk-q7mfz2kd", "the prefix of a key without a label: %r" % row["key_label"]
    replies = {i["title"][:6]: i for i in items if i["kind"] == "unclear_reply"}
    assert replies["sorry,"]["reply"] == "Sorry, I CAN’T create that video [2]", "the newest stored reply: %r" % replies
    assert len(replies["a long"]["reply"]) == 200, "cut like the title: %d" % len(replies["a long"]["reply"])
    assert all(i["key_label"] is None for i in replies.values())
    named = sorted(i["key_label"] for i in items if i["kind"] == "clamp")
    assert named == ["Shop A", "rk-q7mfz2kd"], "the key's label, else the prefix of a key without a label: %r" % named
    assert all(i["reply"] is None for i in items if i["kind"] != "unclear_reply")


ONE_MEDIA = '"media": media.pop() if len(media) == 1 else None'


@mutation(ALERTS, ONE_MEDIA, '"media": None', expect="all the group's jobs are videos")
@mutation(ALERTS, ONE_MEDIA, '"media": media.pop() if len(media) >= 1 else None', expect="mixed media: none")
def test_an_unclear_detail_says_the_one_media_type_of_its_jobs_and_none_when_they_are_mixed(db):
    k = key(db)
    for text, kinds in (("All videos [1]", ("video", "video")), ("All images [1]", ("image",)),
                        ("Both kinds [1]", ("video", "image"))):
        for kind in kinds:
            row = plant_unclear(db, k, text)
            db.run("UPDATE jobs SET media_type = %s WHERE id = (SELECT job_id FROM unclear_replies WHERE id = %s)",
                   kind, row)

    async def scenario(env):
        h = await env.admin_headers()
        return [(await env.client.get(detail_url("unclear_reply", group_of(t)), headers=h)).json()["media"]
                for t in ("All videos [1]", "All images [1]", "Both kinds [1]")]

    videos, images, mixed = run_app(db, scenario)
    assert videos == "video", "all the group's jobs are videos: %r" % videos
    assert images == "image", "all the group's jobs are images: %r" % images
    assert mixed is None, "mixed media: none, %r" % mixed


# ---- the detail -------------------------------------------------------------------------------------------------
def test_an_unclear_detail_lists_its_rows_newest_first_with_the_jobs_outcomes_and_a_deleted_accounts_label_is_null(db):
    k = key(db)
    acc = account(db)
    db.run("UPDATE accounts SET label = 'acc-1' WHERE id = %s", acc)
    first = plant_unclear(db, k, "Wording [1]", status="done", account_id=acc)
    second = plant_unclear(db, k, "Wording [2]", status="failed", error_code="dola.no_video")
    third = plant_unclear(db, k, "Wording [3]", status="waiting")
    dangling = uuid.uuid4()                                   # an account that no longer exists
    gone = plant_unclear(db, k, "Wording [4]", status="failed", error_code="render.deadline", account_id=dangling)
    group = group_of("Wording [1]")

    async def scenario(env):
        h = await env.admin_headers()
        whole = await env.client.get(detail_url("unclear_reply", group), headers=h)
        paged = await env.client.get(detail_url("unclear_reply", group, "?limit=2&offset=1"), headers=h)
        return whole, paged

    whole, paged = run_app(db, scenario)
    body = whole.json()
    assert [i["id"] for i in body["items"]] == [gone, third, second, first]
    assert body["outcomes"] == {"done": 1, "failed": 2, "open": 1}
    assert body["failed_codes"] == {"dola.no_video": 1, "render.deadline": 1}
    assert body["count"] == 4 and body["kind"] == "unclear_reply" and body["group"] == group
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[first]["account_label"] == "acc-1" and by_id[second]["account_label"] is None
    assert by_id[gone]["account_id"] == str(dangling) and by_id[gone]["account_label"] is None
    assert set(by_id[first]) == {"id", "at_ms", "job_id", "account_id", "account_label", "key_id", "text", "job_status",
                                 "job_error_code", "acknowledged_ms", "acknowledged_by", "job_title", "key_label"}
    assert by_id[second]["job_status"] == "failed" and by_id[second]["job_error_code"] == "dola.no_video"
    assert [i["id"] for i in paged.json()["items"]] == [third, second]


def test_a_billing_detail_shows_the_rows_of_that_alert_kind_and_wallet_as_the_billing_list_does(db):
    k, clamp, drift = plant(db)

    async def scenario(env):
        h = await env.admin_headers()
        group = "%s.fake.video" % k
        detail = await env.client.get(detail_url("clamp", group), headers=h)
        other = await env.client.get(detail_url("drift", group), headers=h)
        listing = await env.client.get("/admin/alerts?status=all", headers=h)
        return detail, other, listing

    detail, other, listing = run_app(db, scenario)
    assert [i["id"] for i in detail.json()["items"]] == [clamp] and [i["id"] for i in other.json()["items"]] == [drift]
    billing = {i["id"]: i for i in listing.json()["items"]}
    names = ("key_label", "job_id", "job_title")
    row = {k: v for k, v in detail.json()["items"][0].items() if k not in names}
    assert row == billing[clamp], "the hub's billing item is the billing list's row"
    assert {k: v for k, v in other.json()["items"][0].items() if k not in names} == billing[drift]


UNNAMED_KEY = "LEFT JOIN api_keys k ON k.id = b.key_id"
UNNAMED_JOB = "LEFT JOIN ledger l ON l.id = b.ledger_id AND l.key_id = b.key_id "
LEDGER_ANY_KEY = "LEFT JOIN ledger l ON l.id = b.ledger_id "
UNCLEAR_KEY = "LEFT JOIN api_keys k ON k.id = j.key_id "
JOB_JOIN = "LEFT JOIN jobs j ON j.id = l.job_id AND j.key_id = b.key_id "
JOB_ANY_KEY = "LEFT JOIN jobs j ON j.id = l.job_id "


@mutation(ALERTS, UNNAMED_KEY, "LEFT JOIN api_keys k ON false", expect="the clamp names its key")
@mutation(ALERTS, UNNAMED_JOB, "LEFT JOIN ledger l ON false ", expect="the clamp names its job")
@mutation(ALERTS, JOB_JOIN, "LEFT JOIN jobs j ON false ", expect="the clamp names its job")
@mutation(ALERTS, UNCLEAR_KEY, "LEFT JOIN api_keys k ON false ", expect="the unclear reply names its key")
@mutation(ALERTS, 'u.acknowledged_by, j.title, " + KEY_NAME', 'u.acknowledged_by, NULL, " + KEY_NAME',
          expect="the unclear reply names its job")
def test_alert_details_carry_the_job_title_and_the_key_label_next_to_the_ids(db):
    k, clamp, drift = plant(db)
    db.run("UPDATE api_keys SET label = 'Main key' WHERE id = %s", k)
    job = plant_job(db, k, "done")
    db.run("UPDATE jobs SET title = 'A Quiet Lake' WHERE id = %s", job)
    ledger = db.one("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
                    "VALUES (%s, %s, 'fake', 'video', 'charge', 1, current_date) RETURNING id", k, job)[0]
    db.run("UPDATE key_wallets SET used_total = 1 WHERE key_id = %s", k)   # agrees with the ledger
    named = db.one("INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, "
                   "used_total) VALUES ('clamp', %s, 'fake', 'video', %s, 5, 2, 3) RETURNING id", k, ledger)[0]
    reply = plant_unclear(db, k, "Wording [1]")
    db.run("UPDATE jobs SET title = 'Unclear one' WHERE id = (SELECT job_id FROM unclear_replies WHERE id = %s)", reply)

    async def scenario(env):
        h = await env.admin_headers()
        billing = await env.client.get(detail_url("clamp", "%s.fake.video" % k), headers=h)
        return billing, await env.client.get(detail_url("unclear_reply", group_of("Wording [1]")), headers=h)

    billing, unclear = run_app(db, scenario)
    by_id = {i["id"]: i for i in billing.json()["items"]}
    assert (by_id[named]["key_label"], by_id[named]["job_id"], by_id[named]["job_title"]) == (
        "Main key", str(job), "A Quiet Lake"), "the clamp names its key; the clamp names its job"
    assert (by_id[clamp]["key_label"], by_id[clamp]["job_id"], by_id[clamp]["job_title"]) == ("Main key", None, None)
    [item] = unclear.json()["items"]
    assert item["key_label"] == "Main key", "the unclear reply names its key"
    assert item["job_title"] == "Unclear one", "the unclear reply names its job"


@mutation(ALERTS, UNNAMED_JOB, LEDGER_ANY_KEY, expect="another key's ledger row is never named")
@mutation(ALERTS, JOB_JOIN, JOB_ANY_KEY, expect="another key's job is never named")
def test_a_billing_alert_never_names_a_ledger_row_or_a_job_of_another_key(db):
    k, other = key(db), key(db)
    db.run("UPDATE key_wallets SET used_total = 2 WHERE kind = 'video' AND key_id = %s", other)
    db.run("UPDATE key_wallets SET used_total = 1 WHERE kind = 'video' AND key_id = %s", k)
    mine, theirs = plant_job(db, k, "done"), plant_job(db, other, "done")
    db.run("UPDATE jobs SET title = 'Mine' WHERE id = %s", mine)
    db.run("UPDATE jobs SET title = 'Not yours' WHERE id = %s", theirs)
    ledger = ("INSERT INTO ledger (key_id, job_id, provider, kind, entry, amount, billing_day) "
              "VALUES (%s, %s, 'fake', 'video', 'charge', 1, current_date) RETURNING id")
    alert = ("INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, used_total) "
             "VALUES ('clamp', %s, 'fake', 'video', %s, 5, 2, 3) RETURNING id")
    # another key's ledger row with another key's job; with this key's job; and this key's row with another key's job
    both, ledger_only, job_only = (db.one(alert, k, db.one(ledger, other, theirs)[0])[0],
                                   db.one(alert, k, db.one(ledger, other, mine)[0])[0],
                                   db.one(alert, k, db.one(ledger, k, theirs)[0])[0])

    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.get(detail_url("clamp", "%s.fake.video" % k), headers=h)

    items = {i["id"]: i for i in run_app(db, scenario).json()["items"]}
    check = Soft()
    check((items[both]["job_id"], items[both]["job_title"]) == (None, None), "another key's ledger row is never named")
    check((items[ledger_only]["job_id"], items[ledger_only]["job_title"]) == (None, None),
          "another key's ledger row is never named: even for this key's job")
    check((items[job_only]["job_id"], items[job_only]["job_title"]) == (None, None), "another key's job is never named")
    check.done()


class Soft:
    """Every failed check of a test is named in its one failure, so each marker's `expect=` finds its own."""

    def __init__(self):
        self.failed = []

    def __call__(self, ok: bool, what: str) -> None:
        if not ok:
            self.failed.append(what)

    def done(self) -> None:
        assert not self.failed, "; ".join(self.failed)


# ---- acknowledge ------------------------------------------------------------------------------------------------
@mutation(ALERTS, ACK_UNCLEAR, ACK_UNCLEAR_ALL, expect="a row above through_id was stamped")
@mutation(ALERTS, ACK_BILLING, ACK_BILLING_ALL, expect="a billing row above through_id was stamped")
def test_acknowledge_stamps_only_the_open_rows_up_to_through_id_and_a_new_row_reopens_the_group(db, caplog):
    k = key(db)
    ids = [plant_unclear(db, k, "Same words [%d]" % n) for n in range(3)]
    group = group_of("Same words [0]")
    k2, clamp, drift = plant(db)
    ledger = db.one("INSERT INTO ledger (key_id, provider, kind, entry, amount, billing_day) "
                    "VALUES (%s, 'fake', 'video', 'adjust', 0, current_date) RETURNING id", k2)[0]
    clamp2 = db.one("INSERT INTO billing_alerts (alert, key_id, provider, kind, ledger_id, amount, used_day, "
                    "used_total) VALUES ('clamp', %s, 'fake', 'video', %s, 1, 1, 1) RETURNING id", k2, ledger)[0]

    async def scenario(env):
        h = await env.admin_headers()
        post = lambda kind, grp, n: env.client.post(  # noqa: E731
            detail_url(kind, grp, "/acknowledge"), headers=h, json={"through_id": n})
        with caplog.at_level(logging.INFO, logger="relay_core.alerts"):
            first = await post("unclear_reply", group, ids[1])
            again = await post("unclear_reply", group, ids[1])
            billing = await post("clamp", "%s.fake.video" % k2, clamp)
        late = plant_unclear(db, k, "Same words [9]")
        reopened = await env.client.get(detail_url("unclear_reply", group), headers=h)
        return first, again, billing, late, reopened

    first, again, billing, late, reopened = run_app(db, scenario)
    assert first.json()["acknowledged"] == 2 and first.json()["group"]["open_count"] == 1, \
        "a row above through_id was stamped"
    assert db.one("SELECT acknowledged_by FROM unclear_replies WHERE id = %s", ids[2]) == (None,)
    assert db.one("SELECT acknowledged_by FROM unclear_replies WHERE id = %s", ids[0]) == (ADMIN_USER,)
    assert again.json()["acknowledged"] == 0, "a second call changes nothing"
    assert billing.json()["acknowledged"] == 1 and billing.json()["group"]["open_count"] == 1, \
        "a billing row above through_id was stamped"
    assert db.one("SELECT acknowledged_by FROM billing_alerts WHERE id = %s", clamp2) == (None,)
    assert reopened.json()["status"] == "open" and reopened.json()["open_count"] == 2 and late
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and "acknowledged" in r.getMessage()]
    assert len(lines) == 3 and all("Same words" not in line and "same words" not in line for line in lines)


@mutation(ALERTS, ACK_BY_KIND, ACK_ANY_KIND, expect="the clamp acknowledge stamped the drift row")
def test_acknowledging_a_clamp_group_leaves_the_drift_row_of_the_same_wallet_open(db):
    k, clamp, drift = plant(db)

    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.post(detail_url("clamp", "%s.fake.video" % k, "/acknowledge"), headers=h,
                                     json={"through_id": max(clamp, drift)})

    r = run_app(db, scenario)
    assert r.json()["acknowledged"] == 1, "the clamp acknowledge stamped the drift row: %r" % r.json()
    assert db.one("SELECT acknowledged_by IS NULL FROM billing_alerts WHERE id = %s", drift) == (True,), \
        "the clamp acknowledge stamped the drift row"


# ---- bounds -----------------------------------------------------------------------------------------------------
@mutation(ROUTES, GROUP_FORM, NO_GROUP_FORM, expect="a malformed group reached the database")
def test_every_bad_value_is_a_400_or_a_404_and_never_a_500(db):
    k, _clamp, _drift = plant(db)
    plant_unclear(db, k, "Some words")
    group = group_of("Some words")

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda url: env.client.get(url, headers=h)  # noqa: E731
        post = lambda url, body: env.client.post(url, headers=h, json=body)  # noqa: E731
        ack = detail_url("unclear_reply", group, "/acknowledge")
        return {
            "kind": await get(groups_url("?kind=nope")),
            "limit0": await get(groups_url("?limit=0")), "limit201": await get(groups_url("?limit=201")),
            "status": await get(groups_url("?status=nope")),
            "offset-1": await get(detail_url("unclear_reply", group, "?offset=-1")),
            "offset-big": await get(detail_url("unclear_reply", group, "?offset=%d" % (2**31 + 1))),
            "dlimit0": await get(detail_url("unclear_reply", group, "?limit=0")),
            "dlimit201": await get(detail_url("unclear_reply", group, "?limit=201")),
            "badkind": await get(detail_url("nope", group)),
            "short": await get(detail_url("unclear_reply", "abc")),
            "upper": await get(detail_url("unclear_reply", group.upper() + "0")),
            "billing-form": await get(detail_url("clamp", "a.b.c")),
            "billing-uuid": await get(detail_url("clamp", "not-a-uuid.fake.video")),
            "ghost": await get(detail_url("unclear_reply", "0" * 16)),
            "nul": await get(detail_url("unclear_reply", "ab%00cd")),
            "nul-billing": await get(detail_url("clamp", "ab%00.cd.ef")),
            "ghost-billing": await get(detail_url("clamp", "%s.fake.image" % k)),
            "ack-ghost": await post(detail_url("unclear_reply", "f" * 16, "/acknowledge"), {"through_id": 1}),
            "ack-badkind": await post(detail_url("nope", group, "/acknowledge"), {"through_id": 1}),
            "t0": await post(ack, {"through_id": 0}), "tbig": await post(ack, {"through_id": 2**63}),
            "tneg": await post(ack, {"through_id": -5}), "tmissing": await post(ack, {}),
            "tstr": await post(ack, {"through_id": "5"}), "tbool": await post(ack, {"through_id": True}),
            "extra": await post(ack, {"through_id": 1, "who": "me"}),
        }

    out = run_app(db, scenario)
    for name, r in out.items():
        assert r.status_code < 500, "a malformed group reached the database: %s -> %d" % (name, r.status_code)
    bad_input = {"kind", "limit0", "limit201", "status", "offset-1", "offset-big", "dlimit0", "dlimit201", "t0", "tbig",
                 "tneg", "tmissing", "tstr", "tbool", "extra"}
    missing = {"nul", "nul-billing", "badkind", "short", "upper", "billing-form", "billing-uuid", "ghost",
               "ghost-billing", "ack-ghost", "ack-badkind"}
    assert {n for n, r in out.items() if r.status_code == 400} == bad_input
    assert {n for n, r in out.items() if r.status_code == 404} == missing
    assert all(code_of(out[n]) == "resource.not_found" for n in missing)
    assert out["kind"].json()["error"]["param"] == "kind" and out["short"].json()["error"]["param"] == "group"


# ---- the reply text is on no public surface ---------------------------------------------------------------------
def test_the_reply_text_is_on_no_public_surface(db):
    account(db)

    async def scenario(env):
        api_key = await env.new_key()
        made = await env.client.post("/v1/video/generations", headers=api_key.headers,
                                     json={"model": "fake-video", "prompt": "a cat"})
        assert made.status_code == 200, made.text
        job_id = made.json()["id"]
        wp = await open_pool(db.worker_dsn, max_size=4)
        try:
            got = await claim(wp)
            engine = FakeEngine()
            engine.script_submit("*", sent(SubmitResult(Accepted("r", taken=False, unclear="words " + MARKER))))
            await submit(wp, got, engine)
        finally:
            await wp.close()
        status = await env.client.get("/v1/video/generations/" + job_id, headers=api_key.headers)
        return status

    status = run_app(db, scenario, spec=FakeSpec())
    assert MARKER not in status.text and MARKER not in repr(status.headers)
    assert db.one("SELECT text FROM unclear_replies")[0] == "words " + MARKER, "premise: the text is stored"
    for sql in ("SELECT j::text FROM jobs j", "SELECT e::text FROM job_events e", "SELECT r::text FROM request_logs r",
                "SELECT m::text FROM job_media m", "SELECT c::text FROM commands c"):
        assert all(MARKER not in str(r[0]) for r in db.all(sql)), "the reply text reached %s" % sql



def test_a_key_made_with_a_label_of_spaces_is_named_by_its_prefix_in_the_alerts(db):
    async def make_key(env):
        r = await env.client.post("/admin/keys", json={"label": "   "}, headers=await env.admin_headers())
        assert r.status_code == 200, r.text
        return r.json()

    made = run_app(db, make_key)
    plant_unclear(db, uuid.UUID(made["id"]), "Words under a key made with spaces")

    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.get(detail_url("unclear_reply", group_of("Words under a key made with spaces")),
                                    headers=h)

    [row] = run_app(db, scenario).json()["items"]
    assert made["prefix"] and row["key_label"] == made["prefix"], "named by its prefix: %r" % row["key_label"]
