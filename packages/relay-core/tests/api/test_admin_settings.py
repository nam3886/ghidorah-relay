"""Admin settings (I19): where each value comes from, a refused value leaves the stored one alone, a
delete restores the default, and the boot-only values are listed read-only and never writable."""

import json

from apikit import ADMIN_USER, run_app

from relay_core import crypto
from relay_core.settings import Settings, SettingsError, Spec
from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_settings.py"
SOURCE = '"source": settings.source(key)'
SOURCE_FIXED = '"source": "default"'
WRITER = "by=request.state.admin_user.username"
NO_WRITER = "by=None"
KEY_CHECK = "    _known(settings, key)\n    try:"
NO_KEY_CHECK = "    try:"
FIXED = '"fixed": fixed_limits(state.registry)'
NO_FIXED = '"fixed": []'
WORKER_QUERY = "ORDER BY at DESC, worker_id LIMIT %s"
NO_WORKERS = "AND false ORDER BY at DESC, worker_id LIMIT %s"
API_CONTAINER = {"read": True, "cpus": 1.5, "memory_bytes": 12 * 1024**3, "pids": 2048, "shm_bytes": 64 * 1024**2}
WORKER_CONTAINER = {"read": True, "cpus": 8.0, "memory_bytes": 12 * 1024**3, "pids": None, "shm_bytes": 1024**3}
ALLOWED = '"allowed": spec.allowed'
NO_ALLOWED = '"allowed": None'
KEY = "log_retention_days"


def code_of(r) -> str:
    return r.json()["error"]["code"]


def item(listing, key: str) -> dict:
    return next(i for i in listing.json()["items"] if i["key"] == key)


@mutation(ROUTES, SOURCE, SOURCE_FIXED, expect="the value comes from the database")
def test_the_list_says_where_each_value_comes_from_and_a_delete_restores_the_default(db):
    async def scenario(env):
        h = await env.admin_headers()
        out = {"start": await env.client.get("/admin/settings", headers=h)}
        out["put"] = await env.client.put("/admin/settings/" + KEY, json={"value": 7}, headers=h)
        out["after_put"] = await env.client.get("/admin/settings", headers=h)
        out["row"] = db.one("SELECT value FROM settings_kv WHERE key = %s", KEY)
        out["deleted"] = await env.client.delete("/admin/settings/" + KEY, headers=h)
        out["again"] = await env.client.delete("/admin/settings/" + KEY, headers=h)
        out["restored"] = await env.client.get("/admin/settings", headers=h)
        return out

    out = run_app(db, scenario)
    start = item(out["start"], KEY)
    assert (start["value"], start["source"], start["default"], start["type"]) == (30, "default", 30, "int")
    assert start["description"] and start["read_only"] is False
    assert {i["key"] for i in out["start"].json()["items"]} >= {KEY, "session_ttl_s", "max_unfinished_default"}
    assert out["put"].json()["value"] == 7
    assert item(out["after_put"], KEY)["value"] == 7 and item(out["after_put"], KEY)["source"] == "db", (
        "the value comes from the database")
    assert out["row"] == (7,)
    assert out["deleted"].json()["deleted"] is True and out["again"].json()["deleted"] is False
    assert (item(out["restored"], KEY)["value"], item(out["restored"], KEY)["source"]) == (30, "default")
    assert db.one("SELECT count(*) FROM settings_kv")[0] == 0


def test_an_environment_twin_is_ignored_and_a_put_and_a_delete_move_between_db_and_default(db, monkeypatch):
    monkeypatch.setenv("LOG_RETENTION_DAYS", "12")

    async def scenario(env):
        h = await env.admin_headers()
        get = lambda: env.client.get("/admin/settings", headers=h)  # noqa: E731
        out = [item(await get(), KEY)]
        await env.client.put("/admin/settings/" + KEY, json={"value": 9}, headers=h)
        out.append(item(await get(), KEY))
        await env.client.delete("/admin/settings/" + KEY, headers=h)
        out.append(item(await get(), KEY))
        return out

    twin, overridden, back = run_app(db, scenario)
    assert (twin["value"], twin["source"], twin["default"]) == (30, "default", 30)
    assert (overridden["value"], overridden["source"]) == (9, "db")
    assert (back["value"], back["source"]) == (30, "default")


@mutation(ROUTES, ALLOWED, NO_ALLOWED, expect="assert None == 'at least 1'")
def test_every_setting_says_what_it_allows_and_none_names_an_env_variable(db):
    async def scenario(env):
        h = await env.admin_headers()
        listing = await env.client.get("/admin/settings", headers=h)
        put = await env.client.put("/admin/settings/" + KEY, json={"value": 7}, headers=h)
        gone = await env.client.delete("/admin/settings/" + KEY, headers=h)
        return listing, put, gone

    listing, put, gone = run_app(db, scenario)
    items = {i["key"]: i for i in listing.json()["items"]}
    assert items[KEY]["allowed"] == "at least 1", "allowed"
    assert items["unsent_deadline_s"]["allowed"] == "at least 60"
    assert items["media_orphan_sweep"]["allowed"] == "true or false"
    assert items["filename_pattern"]["allowed"] == "not empty"
    assert items["fake.browser_slots"]["allowed"] == "1\u201330"
    assert items["fake.alert_drift_rate"]["allowed"] is None
    for i in (*items.values(), put.json(), gone.json()):
        assert not any(k.startswith("env") for k in i) and i["source"] in ("db", "default"), i
        assert "allowed" in i


@mutation(ROUTES, '"basic": spec.basic', '"basic": False', expect="basic")
@mutation(ROUTES, '"group": key.partition(".")[0] if "." in key else "core"', '"group": "core"', expect="group")
def test_each_setting_names_its_group_and_whether_it_is_basic(db):
    async def scenario(env):
        return await env.client.get("/admin/settings", headers=await env.admin_headers())

    items = {i["key"]: i for i in run_app(db, scenario).json()["items"]}
    assert {k: i["group"] for k, i in items.items() if k in (KEY, "fake.disabled_models")} == {
        KEY: "core", "fake.disabled_models": "fake"}, "group"
    assert all(i["group"] == ("core" if "." not in k else k.split(".")[0]) for k, i in items.items()), "group"
    assert {k for k, i in items.items() if i["basic"]} == {"fake.disabled_models"}, "basic"


@mutation("packages/relay-core/src/relay_core/settings.py", "        if unknown:", "        if False:",
          expect="unknown model id saved")
def test_disabled_models_takes_only_the_providers_model_ids(db):
    async def scenario(env):
        h = await env.admin_headers()
        def put(value):
            return env.client.put("/admin/settings/fake.disabled_models", json={"value": value}, headers=h)

        return {"listing": await env.client.get("/admin/settings", headers=h), "unknown": await put(["fake-nope"]),
                "not_text": await put(["fake-video", 3]), "not_a_list": await put("fake-video"),
                "ok": await put(["fake-image"])}

    out = run_app(db, scenario)
    first = item(out["listing"], "fake.disabled_models")
    assert (first["type"], first["value"], first["default"]) == ("list", [], [])
    assert first["allowed"] == "a list of fake model ids: fake-video, fake-image"
    refused = {n: (out[n].status_code, out[n].json().get("error", {}).get("code"),
                   out[n].json().get("error", {}).get("param")) for n in ("unknown", "not_text", "not_a_list")}
    assert refused == dict.fromkeys(refused, (400, "input.invalid", "value")), "unknown model id saved: %s" % refused
    assert out["ok"].status_code == 200 and out["ok"].json()["value"] == ["fake-image"], out["ok"].text
    assert db.one("SELECT value FROM settings_kv WHERE key = 'fake.disabled_models'") == (["fake-image"],)


@mutation(ROUTES, FIXED, NO_FIXED, expect="where 0 = len([])")
def test_the_list_carries_the_fixed_limits_core_first_then_each_provider(db):
    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.get("/admin/settings", headers=h)

    fixed = run_app(db, scenario).json()["fixed"]
    assert len(fixed) > 50
    assert all(set(f) == {"group", "name", "value", "reason"} for f in fixed)
    assert all(f["value"] and f["reason"] for f in fixed)
    groups = [f["group"] for f in fixed]
    assert groups[0] == "core" and groups[-1] == "fake"
    assert groups == sorted(groups, key=lambda g: g != "core"), "core rows come first"
    rows = {(f["group"], f["name"]): f["value"] for f in fixed}
    assert rows[("core", "jobs.engine.MAX_INFLIGHT")] == "32 jobs"
    assert rows[("core", "leases.LEASE_TTL_S")] == "60 s"
    assert rows[("fake", "caps.max_attempts")] == "3 attempts"
    assert rows[("fake", "max_prompt_chars")] == "1000 chars"
    assert rows[("fake", "fake.demo_bound")] == "7 items"


@mutation(ROUTES, WORKER_QUERY, NO_WORKERS, expect="['api']")
def test_containers_list_the_api_then_the_recent_workers_newest_first_and_mark_the_silent_ones_stale(db):
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at, limits) VALUES "
           "('w-new', 'fake', now() - interval '1 hour', now() - interval '5 seconds', %s::jsonb)",
           json.dumps(WORKER_CONTAINER))
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
           "('w-old', 'fake', now() - interval '2 hours', now() - interval '3 seconds')")
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at, limits) VALUES "
           "('w-gone', 'fake', now() - interval '3 hours', now() - interval '10 minutes', '5'::jsonb)")

    async def scenario(env):
        env.app.state.container = API_CONTAINER
        h = await env.admin_headers()
        return (await env.client.get("/admin/settings", headers=h)).json()["containers"]

    rows = run_app(db, scenario)
    assert [r["service"] for r in rows] == ["api", "w-old", "w-new", "w-gone"], [r["service"] for r in rows]
    api, old, new, gone = rows
    assert api["reported_ms"] == api["started_ms"] > 0
    assert {k: v for k, v in api.items() if not k.endswith("_ms")} == {
        "service": "api", "provider": None, "this_process": True, "reported": True, "cpus": "1.5", "memory": "12 GiB",
        "pids": "2048", "shm": "64 MiB", "stale": False}
    assert {k: v for k, v in new.items() if not k.endswith("_ms")} == {
        "service": "w-new", "provider": "fake", "this_process": False, "reported": True, "cpus": "8",
        "memory": "12 GiB", "pids": "no limit", "shm": "1 GiB", "stale": False}
    assert {k: v for k, v in old.items() if not k.endswith("_ms")} == {
        "service": "w-old", "provider": "fake", "this_process": False, "reported": False, "cpus": None,
        "memory": None, "pids": None, "shm": None, "stale": False}
    assert (gone["stale"], gone["reported"], gone["cpus"]) == (True, False, None), "a limits value that is no object"
    for r, name in ((new, "w-new"), (old, "w-old")):
        at_ms, started_ms = db.one("SELECT floor(extract(epoch FROM at) * 1000)::bigint, "
                                   "floor(extract(epoch FROM started_at) * 1000)::bigint FROM worker_heartbeats "
                                   "WHERE worker_id = %s", name)
        assert abs(r["reported_ms"] - at_ms) <= 1 and abs(r["started_ms"] - started_ms) <= 1, name


@mutation(ROUTES, "MAX_WORKER_ROWS = 50", "MAX_WORKER_ROWS = 500", expect="assert 61 == 51")
def test_a_crowd_of_heartbeat_rows_is_cut_at_the_hard_limit_keeping_the_newest(db):
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) "
           "SELECT 'w-' || g, 'fake', now() - interval '1 day', now() - make_interval(secs => g) "
           "FROM generate_series(1, 60) g")

    async def scenario(env):
        h = await env.admin_headers()
        return (await env.client.get("/admin/settings", headers=h)).json()["containers"]

    names = [r["service"] for r in run_app(db, scenario)]
    assert len(names) == 51, "assert %d == 51" % len(names)
    assert names[1] == "w-1" and names[-1] == "w-50", "newest first, the hard limit keeps the newest"


@mutation(ROUTES, "FROM worker_heartbeats WHERE at > clock_timestamp() - make_interval(secs => %s) ",
          "FROM worker_heartbeats WHERE %s > 0 ", expect="assert 'w-ancient' not in")
def test_a_heartbeat_row_older_than_the_look_back_is_not_listed(db):
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
           "('w-recent', 'fake', now(), now() - interval '1 hour'), "
           "('w-ancient', 'fake', now() - interval '9 days', now() - interval '3 days')")

    async def scenario(env):
        h = await env.admin_headers()
        return (await env.client.get("/admin/settings", headers=h)).json()["containers"]

    names = [r["service"] for r in run_app(db, scenario)]
    assert "w-ancient" not in names, "assert 'w-ancient' not in %r" % names
    assert names == ["api", "w-recent"], "a worker silent for an hour is still listed, as stale"


@mutation(ROUTES, '"stale": age_s > HEARTBEAT_STALE_S', '"stale": False', expect="[False, False, False] ==")
def test_the_stale_flag_is_set_on_the_server_clock_for_a_silent_worker(db):
    db.run("INSERT INTO worker_heartbeats (worker_id, provider, started_at, at) VALUES "
           "('w-fresh', 'fake', now(), now()), ('w-silent', 'fake', now(), now() - interval '2 minutes')")

    async def scenario(env):
        h = await env.admin_headers()
        return (await env.client.get("/admin/settings", headers=h)).json()["containers"]

    flags = {r["service"]: r["stale"] for r in run_app(db, scenario)}
    assert [flags["api"], flags["w-fresh"], flags["w-silent"]] == [False, False, True]


def test_create_app_keeps_the_container_record_it_is_given_and_reads_its_own_when_none_is():
    from relay_core import cgroup
    from relay_core.api.app import create_app
    from relay_core.providers import ProviderRegistry

    given = create_app(pool=None, settings=None, store=None, registry=ProviderRegistry(),  # type: ignore[arg-type]
                       container=API_CONTAINER)
    assert given.state.container == API_CONTAINER and given.state.container is not API_CONTAINER
    own = create_app(pool=None, settings=None, store=None, registry=ProviderRegistry())  # type: ignore[arg-type]
    assert set(own.state.container) == set(cgroup.own_limits()) and own.state.started_ms > 0


def test_a_process_that_cannot_read_its_cgroup_shows_every_limit_as_unreadable(db):
    async def scenario(env):
        env.app.state.container = {"read": False, "cpus": None, "memory_bytes": None, "pids": None,
                                   "shm_bytes": None}
        h = await env.admin_headers()
        return (await env.client.get("/admin/settings", headers=h)).json()["containers"]

    (api,) = run_app(db, scenario)
    assert (api["cpus"], api["memory"], api["pids"], api["shm"]) == ("unreadable",) * 4 and api["reported"] is True


@mutation(ROUTES, WRITER, NO_WRITER, expect="updated_by")
def test_a_put_records_who_made_it(db):
    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.put("/admin/settings/session_ttl_s", json={"value": 3600}, headers=h)

    assert run_app(db, scenario).status_code == 200
    assert db.one("SELECT value, updated_by FROM settings_kv WHERE key = 'session_ttl_s'") == (3600, ADMIN_USER), (
        "updated_by is the operator")


@mutation(ROUTES, KEY_CHECK, NO_KEY_CHECK, expect="param key")
def test_a_refused_value_leaves_the_stored_one_and_a_foreign_key_or_a_boot_only_one_is_refused(db):
    bad_values = ["30", True, 0, 1.5, None, [1]]

    async def scenario(env):
        h = await env.admin_headers()
        await env.client.put("/admin/settings/" + KEY, json={"value": 14}, headers=h)
        out = {"values": [await env.client.put("/admin/settings/" + KEY, json={"value": v}, headers=h)
                          for v in bad_values]}
        out["keys"] = {k: await env.client.put("/admin/settings/" + k, json={"value": "x"}, headers=h)
                       for k in ("not_a_setting", "upstream_url", "BILLING_TZ", "fake.proxy", "x_user_agent")}
        out["deleted"] = {k: await env.client.delete("/admin/settings/" + k, headers=h)
                          for k in ("not_a_setting", "upstream_url")}
        out["extra"] = await env.client.put("/admin/settings/" + KEY, json={"value": 5, "by": "me"}, headers=h)
        out["live"] = item(await env.client.get("/admin/settings", headers=h), KEY)
        return out

    out = run_app(db, scenario)
    for r in out["values"]:
        assert (r.status_code, code_of(r), r.json()["error"]["param"]) == (400, "input.invalid", "value"), r.text
    for r in [*out["keys"].values(), *out["deleted"].values()]:
        assert (r.status_code, code_of(r), r.json()["error"]["param"]) == (400, "input.invalid", "key"), "param key"
    assert out["extra"].status_code == 400
    assert out["live"]["value"] == 14 and db.all("SELECT key, value FROM settings_kv") == [(KEY, 14)], (
        "a refused write changed nothing")


def test_the_read_only_list_holds_only_the_billing_pin_and_the_address_and_proxies_are_settings(db):
    """Owner 25/10: the public address and the trusted proxies are settings (no env); the billing day's zone is the
    pin `relay migrate` wrote, shown read-only (moving it would shift every daily window)."""
    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.get("/admin/settings", headers=h)

    r = run_app(db, scenario, public_base_url="https://relay.example", trusted_proxies="10.0.0.0/8")
    only = {e["name"]: e for e in r.json()["env_only"]}
    assert set(only) == {"BILLING_TZ"}
    assert only["BILLING_TZ"]["read_only"] is True and only["BILLING_TZ"]["value"] == "UTC"
    assert item(r, "public_address")["value"] == "https://relay.example"
    assert item(r, "trusted_proxies")["value"] == ["10.0.0.0/8"]


SETTINGS_FILE = "packages/relay-core/src/relay_core/settings.py"
PLAIN = "abcdefghijklmnopWXYZ"


@mutation(SETTINGS_FILE, "        return seal_secret(spec.key, value)\n", "        return value\n",
          expect="a secret was stored in plain")
def test_a_secret_setting_is_sealed_never_read_back_and_opened_only_with_the_private_key(db, monkeypatch):
    """A solver key (owner 25/10): the API seals it with the public key; no admin read returns it (only set or not and
    its last 4 characters); only a process given the private keys opens it, at its next reload, no restart."""
    keys, stranger = crypto.generate_keypair(), crypto.generate_keypair()
    monkeypatch.setenv(crypto.PUBLIC_KEY_VAR, keys.public)

    async def scenario(env):
        registry = env.settings._registry
        registry.register(Spec("relay_token", str, "", "A token.", secret=True))
        h = await env.admin_headers()
        out = {"start": item(await env.client.get("/admin/settings", headers=h), "relay_token")}
        out["put"] = await env.client.put("/admin/settings/relay_token", json={"value": "  %s \n" % PLAIN}, headers=h)
        out["row"] = db.one("SELECT value::text FROM settings_kv WHERE key = 'relay_token'")[0]
        listing = await env.client.get("/admin/settings", headers=h)
        out["listing"], out["after"] = listing.text, item(listing, "relay_token")
        out["history"] = (await env.client.get("/admin/settings/history", headers=h)).text
        worker = Settings(env.pool, db.api_dsn, registry=registry, open_keys=[keys.private])
        other = Settings(env.pool, db.api_dsn, registry=registry, open_keys=[stranger.private])
        await worker.reload()
        await other.reload()
        out["opened"], out["unopened"] = worker.get("relay_token"), other.get("relay_token")
        try:
            env.settings.get("relay_token")
            out["api_read"] = "read"
        except SettingsError:
            out["api_read"] = "refused"
        out["refused"] = [(await env.client.put("/admin/settings/relay_token", json={"value": v}, headers=h)
                           ).status_code for v in ("  ", 1234, None)]
        await env.client.put("/admin/settings/relay_token", json={"value": "short"}, headers=h)
        out["short"] = item(await env.client.get("/admin/settings", headers=h), "relay_token")["hint"]
        out["cleared"] = (await env.client.delete("/admin/settings/relay_token", headers=h)).json()
        await worker.reload()
        out["after_clear"] = worker.get("relay_token")
        return out

    out = run_app(db, scenario)
    assert PLAIN not in out["row"], "a secret was stored in plain"
    assert (out["start"]["secret"], out["start"]["source"], out["start"]["hint"], out["start"]["value"],
            out["start"]["default"]) == (True, "default", None, None, None)
    assert out["put"].status_code == 200 and out["put"].json()["value"] is None
    assert (out["after"]["source"], out["after"]["hint"], out["after"]["value"]) == ("db", "WXYZ", None)
    for text in (out["put"].text, out["listing"], out["history"]):
        assert PLAIN not in text and "abcdefghijklmnop" not in text, "a secret came back from an admin read"
    assert out["opened"] == PLAIN, "the worker did not open the key"
    assert out["unopened"] == "", "a key sealed for another key pair was opened"
    assert out["api_read"] == "refused"
    assert out["refused"] == [400, 400, 400]
    assert out["short"] == "", "a short secret showed its last characters"
    assert out["cleared"]["deleted"] is True and out["cleared"]["source"] == "default" and out["after_clear"] == ""
    assert db.one("SELECT count(*) FROM settings_history WHERE key = 'relay_token' AND secret") == (3,)
