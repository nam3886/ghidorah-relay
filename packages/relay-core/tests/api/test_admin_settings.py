"""Admin settings (contracts 10; I19): where each value comes from, a refused value leaves the stored one alone, a
delete restores the env value or the default, and the boot-only values are listed read-only and never writable."""

from apikit import ADMIN_USER, run_app

from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_settings.py"
SOURCE = '"source": settings.source(key)'
SOURCE_FIXED = '"source": "default"'
WRITER = "by=request.state.admin_user.username"
NO_WRITER = "by=None"
KEY_CHECK = "    _known(settings, key)\n    try:"
NO_KEY_CHECK = "    try:"
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
    assert start["description"] and start["env_name"] == "LOG_RETENTION_DAYS" and start["read_only"] is False
    assert {i["key"] for i in out["start"].json()["items"]} >= {KEY, "session_ttl_s", "max_unfinished_default"}
    assert out["put"].json()["value"] == 7
    assert item(out["after_put"], KEY)["value"] == 7 and item(out["after_put"], KEY)["source"] == "db", (
        "the value comes from the database")
    assert out["row"] == (7,)
    assert out["deleted"].json()["deleted"] is True and out["again"].json()["deleted"] is False
    assert (item(out["restored"], KEY)["value"], item(out["restored"], KEY)["source"]) == (30, "default")
    assert db.one("SELECT count(*) FROM settings_kv")[0] == 0


def test_an_env_value_is_shown_as_env_a_put_overrides_it_and_a_delete_brings_it_back(db):
    async def scenario(env):
        h = await env.admin_headers()
        get = lambda: env.client.get("/admin/settings", headers=h)  # noqa: E731
        out = [item(await get(), KEY)]
        await env.client.put("/admin/settings/" + KEY, json={"value": 9}, headers=h)
        out.append(item(await get(), KEY))
        await env.client.delete("/admin/settings/" + KEY, headers=h)
        out.append(item(await get(), KEY))
        return out

    env_only, overridden, back = run_app(db, scenario, env={"LOG_RETENTION_DAYS": "12"})
    assert (env_only["value"], env_only["source"], env_only["default"]) == (12, "env", 30)
    assert (overridden["value"], overridden["source"]) == (9, "db")
    assert (back["value"], back["source"]) == (12, "env")


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


def test_the_boot_only_values_are_listed_read_only_and_a_secret_is_never_among_them(db):
    async def scenario(env):
        h = await env.admin_headers()
        return await env.client.get("/admin/settings", headers=h)

    r = run_app(db, scenario, public_base_url="https://relay.example", trusted_proxies="10.0.0.0/8")
    only = {e["name"]: e for e in r.json()["env_only"]}
    assert set(only) == {"BILLING_TZ", "PUBLIC_BASE_URL", "TRUSTED_PROXIES"}
    assert all(e["read_only"] is True for e in only.values())
    assert only["BILLING_TZ"]["value"] == "UTC" and only["PUBLIC_BASE_URL"]["value"] == "https://relay.example"
    assert only["TRUSTED_PROXIES"]["value"] == "10.0.0.0/8"
    assert all(i["key"] not in only for i in r.json()["items"])
