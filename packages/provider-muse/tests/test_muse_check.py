"""The account check (session read, cookies kept, the weekly Usage figure into both pools) and the hunt for the Usage
action's id in the site's scripts, from fixtures/muse/usage-261026.json."""

import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from musekit import FIXTURES, NOW, SESSION_OK, Ctx, Site, settings

from relay_core.providers import Balance, Cooldown, Suspect, Tier
from relay_core.testing.mutation import mutation
from relay_muse.credential import jar_of
from relay_muse.engine import check, web
from relay_muse.engine.web import find_action, parse_usage

CHECK_FILE = "packages/provider-muse/src/relay_muse/engine/check.py"
WEB_FILE = "packages/provider-muse/src/relay_muse/engine/web.py"
USAGE = json.loads((FIXTURES / "usage-261026.json").read_text(encoding="utf-8"))
ACTION = "40034617c46db812e70eec8e362677944a0a532adf"
RESETS = datetime.fromtimestamp(1793601250, UTC)
DPL = "dpl_5ZeD5SsnL1hoEpJ8i1KwiWQ9rTPS"
PAGE = ('<html><script src="/_next/static/chunks/0d9a236stbk__.js?dpl=%s"></script>'
        '<script src="/_next/static/chunks/0a2us3yd981u4.js?dpl=%s"></script></html>' % (DPL, DPL))


def rsc(percent=0, status="SUFFICIENT", paid=False) -> str:
    sub = copy.deepcopy(USAGE["subscription"])
    sub["subscription"]["usage"].update(percentUsed=percent, quotaStatus=status)
    sub["subscription"]["tier"]["isPaid"] = paid
    return '0:{"a":"$@1","f":"","b":"%s"}\n1:%s\n' % (DPL, json.dumps(sub))


def site(answer=None, **answers) -> Site:
    """muse.ai with its page, the scripts (the action's declaration in a chunk only a loader names) and the action."""
    return Site({
        "GET /": (200, PAGE, {}),
        "GET /_next/static/chunks/0d9a236stbk__.js": (200, "self.x=1;" + USAGE["other_action"], {}),
        "GET /_next/static/chunks/0a2us3yd981u4.js": (200, USAGE["chunk_loader"], {}),
        "GET /_next/static/chunks/1h1o8k_e0bu30.js": (200, "noop", {}),
        "GET /_next/static/chunks/28gp_oyvbad8g.js": (200, USAGE["action_declaration"], {}),
        "POST /": answer or (200, rsc(9), {}),
        **answers})


def run(s: Site, ctx=None, **over):
    ctx = ctx or Ctx()
    return asyncio.run(check.check_account(ctx, settings(**over), deps=s.deps(), now=lambda: NOW)), ctx


@pytest.fixture(autouse=True)
def fresh_cache():
    web.ACTION_IDS.clear()
    web.HUNTS.clear()
    yield
    web.ACTION_IDS.clear()
    web.HUNTS.clear()


@mutation(CHECK_FILE, "Balance(pool, total * (100 - usage.percent) // 100, usage.resets_at, WEEK)",
          "Balance(pool, total, usage.resets_at, WEEK)", expect="in proportion")
def test_an_alive_account_reports_what_is_left_of_both_pools_from_the_usage():
    s = site()
    health, ctx = run(s)
    assert health.state == "alive" and health.identity == check.identity_of(SESSION_OK["vm_id"])
    assert SESSION_OK["vm_id"] not in json.dumps(dict(health.state_patch)) + health.identity
    assert health.effects == (Balance("image", 364, RESETS, "week"), Balance("video", 36, RESETS, "week"),
                              Tier("free")), "in proportion"
    assert health.state_patch["usage_percent"] == 9 and health.state_patch["vm_state"] == "RUNNING"
    action = next(c for c in s.calls if c.method == "POST" and c.path == "/")
    assert action.headers["Next-Action"] == ACTION and action.headers["x-deployment-id"] == DPL
    assert action.headers["Accept"] == "text/x-component" and action.headers["Sec-Fetch-Dest"] == "empty"
    assert json.loads(action.data) == [{"includeAgreement": True}]
    assert [jar_of(b)["hatch_vml"] for b in ctx.renewed] == ["vml-2"], "the session read's rotation is kept"
    assert s.vms == [], "a check opens no tunnel"


@mutation(CHECK_FILE, "    stop = usage.percent >= settings.get(\"muse.usage_stop_percent\") or not usage.quota_ok",
          "    stop = not usage.quota_ok", expect="at the stop percentage")
def test_at_the_stop_percentage_or_an_insufficient_quota_both_pools_read_zero_until_the_reset():
    zero = (Balance("image", 0, RESETS, "week"), Balance("video", 0, RESETS, "week"))
    health, _ = run(site(answer=(200, rsc(95), {})))
    assert health.effects[:2] == zero, "at the stop percentage"
    health, _ = run(site(answer=(200, rsc(40, status="EXHAUSTED"), {})))
    assert health.effects[:2] == zero
    health, _ = run(site(answer=(200, rsc(50), {})), usage_stop_percent=50)
    assert health.effects[:2] == zero
    paid, _ = run(site(answer=(200, rsc(10, paid=True), {})))
    assert paid.effects == (Balance("image", 360, RESETS, "week"), Balance("video", 36, RESETS, "week"), Tier("pro"))


def test_the_usage_action_is_hunted_once_per_deployment_and_a_missing_one_changes_nothing():
    s = site()
    run(s)
    hunted = [c.path for c in s.calls if c.path.startswith("/_next/")]
    assert "/_next/static/chunks/28gp_oyvbad8g.js" in hunted
    assert web.ACTION_IDS == {DPL: ACTION}
    again = site()
    run(again)
    assert not [c for c in again.calls if c.path.startswith("/_next/")], "the id is kept for the deployment"
    web.ACTION_IDS.clear()
    lost = site(**{"GET /_next/static/chunks/28gp_oyvbad8g.js": (404, "", {})})
    health, _ = run(lost)
    assert health.state == "alive" and health.effects == () and health.state_patch["usage_percent"] is None
    assert web.ACTION_IDS == {DPL: None}
    for broken in ((500, "oops", {}), (200, "0:{}\n", {}), OSError("reset")):
        web.ACTION_IDS.clear()
        health, _ = run(site(answer=broken))
        assert health.state == "alive" and health.effects == ()


@mutation(WEB_FILE, '{3},\\"%s\\"\\)" % USAGE_ACTION)', '{3},\\"\\w+\\"\\)")', expect="another action")
def test_the_action_is_found_by_its_export_name_only():
    assert find_action(USAGE["action_declaration"]) == ACTION
    assert find_action(USAGE["other_action"]) is None, "another action"
    usage = parse_usage(rsc(9))
    assert usage == web.Usage(9, True, RESETS, False)
    assert parse_usage("0:{}\n1:not json percentUsed\n") is None
    assert parse_usage('1:{"subscription":{"usage":{"percentUsed":101}}}') is None


@mutation(CHECK_FILE, '                return Health("unknown", effects=signed_out_effects(now()))',
          '                return Health("signed_out")', expect="spaced strike")
def test_a_401_is_a_spaced_strike_with_a_recheck_and_no_cookie_is_kept():
    s = site(**{"GET /api/session": (401, {"error": "Unauthorized"}, {"hatch_vml": "x"})})
    health, ctx = run(s)
    assert health.state == "unknown", "spaced strike"
    strike, rest = health.effects
    assert isinstance(strike, Suspect) and isinstance(rest, Cooldown) and rest.recheck
    assert ctx.renewed == [] and [c.path for c in s.calls] == ["/api/session"]


def test_no_workspace_is_an_unknown_read_and_a_failed_read_fails_the_check():
    health, _ = run(site(**{"GET /api/session": (200, {"status": "unassigned"}, {})}))
    assert health.state == "unknown" and health.effects == ()
    for answer in ((403, {"error": "Forbidden"}, {}), (200, "<html>", {}), OSError("reset")):
        with pytest.raises(RuntimeError) as failed:
            run(site(**{"GET /api/session": answer}))
        assert "pw" not in str(failed.value)


RETRY_LINE = "                    self.queue.append(path)\n"


class SlowSite:
    """The site's scripts, each read taking `delay` s; the first read of `flaky` fails. Reads are counted."""

    def __init__(self, delay=0.0, flaky=()):
        self.delay, self.flaky, self.reads, self.settings = delay, set(flaky), [], settings()
        self.scripts = {"/_next/static/chunks/0d9a236stbk__.js": "x;" + USAGE["other_action"],
                        "/_next/static/chunks/0a2us3yd981u4.js": USAGE["chunk_loader"],
                        "/_next/static/chunks/1h1o8k_e0bu30.js": "noop",
                        "/_next/static/chunks/28gp_oyvbad8g.js": USAGE["action_declaration"]}

    async def get(self, path, accept=""):
        self.reads.append(path)
        await asyncio.sleep(self.delay)
        if path in self.flaky:
            self.flaky.discard(path)
            raise OSError("reset")
        return SimpleNamespace(status_code=200, text=self.scripts.get(path, ""))


@mutation(WEB_FILE, RETRY_LINE, "                    pass\n", expect="a script that failed is read again")
def test_a_hunt_cut_by_its_budget_goes_on_at_the_next_read_and_retries_a_failed_script():
    slow = SlowSite(delay=0.05, flaky={"/_next/static/chunks/28gp_oyvbad8g.js"})
    hunt = web.Hunt(PAGE)
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(hunt.run(slow), 0.08))
    assert hunt.seen == {"/_next/static/chunks/0d9a236stbk__.js", "/_next/static/chunks/0a2us3yd981u4.js"}
    assert asyncio.run(hunt.run(slow)) == ACTION, "a script that failed is read again"
    assert slow.reads.count("/_next/static/chunks/0d9a236stbk__.js") == 1, "a script read is never read again"
    missing = SlowSite()
    del missing.scripts["/_next/static/chunks/28gp_oyvbad8g.js"]
    assert asyncio.run(web.Hunt(PAGE).run(missing)) is None


REMEMBER = '            remember_action((getattr(ctx.account, "provider_state", None) or {}).get("usage_action"))\n'


@mutation(CHECK_FILE, REMEMBER, "", expect="no hunt after a restart")
def test_the_action_id_is_kept_on_the_account_so_a_restarted_worker_does_not_hunt_again():
    s = site()
    health, _ = run(s)
    assert health.state_patch["usage_action"] == {"deployment": DPL, "id": ACTION}
    web.ACTION_IDS.clear()                                  # a restarted worker
    ctx = Ctx()
    ctx.account.provider_state = {"usage_action": health.state_patch["usage_action"]}
    again = site()
    health, _ = run(again, ctx)
    assert not [c for c in again.calls if c.path.startswith("/_next/")], "no hunt after a restart"
    assert health.state_patch["usage_percent"] == 9
    for junk in ({"deployment": "x", "id": ACTION}, {"deployment": DPL, "id": "nope"}, "dpl", None):
        web.ACTION_IDS.clear()
        web.remember_action(junk)
        assert web.ACTION_IDS == {}


def test_a_usage_not_read_now_clears_the_last_figure():
    health, _ = run(site(answer=(500, "oops", {})))
    assert health.state == "alive"
    assert {k: health.state_patch[k] for k in ("usage_percent", "quota_ok", "usage_resets_at")} == dict.fromkeys(
        ("usage_percent", "quota_ok", "usage_resets_at"))
