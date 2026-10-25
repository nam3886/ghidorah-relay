"""Writing back the cookies a site rotated during a signed-in browser run (`renew_jar`): written when they rotated,
not when nothing moved, never when the browser lost or changed the account's session, never over a newer rotation
another run wrote, cut short when it hangs, and a failed write never fails the run. Pure: the browser and the run are
stand-ins."""

import asyncio
import json
import time
from types import SimpleNamespace

from relay_browser import ui
from relay_browser.ui import renew_jar, rotated_cookies
from relay_core.testing.mutation import mutation

U = "packages/relay-browser/src/relay_browser/ui.py"
DAY = 86400
T0 = 1_800_000_000
STABLE = ("sessionid", "uid_tt")
STORED = [
    {"name": "sessionid", "value": "S1", "domain": ".dola.com", "path": "/", "secure": True, "httpOnly": True,
     "session": False, "expirationDate": T0 + 60 * DAY},
    {"name": "uid_tt", "value": "U1", "domain": ".dola.com", "path": "/", "session": False,
     "expirationDate": T0 + 60 * DAY},
    {"name": "ttwid", "value": "W1", "domain": ".dola.com", "path": "/", "session": False, "expirationDate": T0},
    {"name": "flow_user_country", "value": "VN", "domain": ".www.dola.com", "path": "/", "session": True},
]


def browser(**changes):
    """What the browser holds at the end: the stored jar (Playwright's shape), `changes` = {name: (value, expires)}
    laid over it (None drops the cookie), plus an analytics cookie the jar never held."""
    out = []
    for c in STORED:
        value, expires = c["value"], c.get("expirationDate", -1)
        if c["name"] in changes:
            if changes[c["name"]] is None:
                continue
            value, expires = changes[c["name"]]
        out.append({"name": c["name"], "value": value, "domain": c["domain"], "path": c["path"], "expires": expires})
    out.append({"name": "_ga", "value": "GA", "domain": ".dola.com", "path": "/", "expires": T0 + DAY})
    return out


class Browser:
    def __init__(self, cookies, error=None):
        self.jar, self.error, self.asked = cookies, error, []

    async def cookies(self, urls):
        self.asked.append(urls)
        if self.error:
            raise self.error
        return self.jar


class Db:
    def __init__(self, blob):
        self.stored = blob


class Run:
    """A run whose `renew_credential` works like the core's `Renewal`: every write is `merge(what this run knows is
    stored, blob)` by compare-and-swap on that; a lost swap reads the account again and merges once more."""

    def __init__(self, stored=None, error=None, db=None, slow_s=0.0):
        blob = json.dumps({"cookies": STORED}).encode()
        self.account = SimpleNamespace(id="a1", credential=blob)
        self.db = db or Db(stored or blob)
        self.plain, self.error, self.writes, self.slow_s = blob, error, [], slow_s

    @property
    def stored(self):
        return self.db.stored

    async def renew_credential(self, blob, merge=None):
        if self.error:
            raise self.error
        await asyncio.sleep(self.slow_s)
        self.writes.append(blob)
        if self.db.stored != self.plain:            # another run wrote meanwhile: read it again
            self.plain = self.db.stored
        self.db.stored = self.plain = merge(self.plain, blob) if merge else blob
        return self.db.stored


def renew(run, cookies, **kw):
    return asyncio.run(renew_jar(Browser(cookies) if isinstance(cookies, list) else cookies, run,
                                 ("https://www.dola.com",), **kw))


def jar_of(blob):
    return {c["name"]: c for c in json.loads(blob)["cookies"]}


ROTATE_RULE = '        if c["value"] == old.get("value") and not moved:'
EXPIRY_RULE = '            not session and abs(expires - (old.get("expirationDate") or 0)) >= EXPIRY_SLACK_S)'
CAS_RULE = '    data["cookies"] = [changes[_key(c)] if _key(c) in changes and c == started.get(_key(c)) else c'
CAS_ANY = '    data["cookies"] = [changes[_key(c)] if _key(c) in changes else c'


@mutation(U, '        new = {**old, "value": c["value"], "session": session}',
          '        new = {**old, "session": session}', expect="a rotated cookie")
def test_a_rotated_cookie_is_written_back_with_its_new_expiry_and_nothing_else_moves():
    run = Run()
    assert renew(run, browser(ttwid=("W2", T0 + 2 * DAY)), stable=STABLE) is True, "a rotated cookie"
    jar = jar_of(run.stored)
    assert (jar["ttwid"]["value"], jar["ttwid"]["expirationDate"]) == ("W2", T0 + 2 * DAY), "a rotated cookie"
    assert jar["sessionid"] == STORED[0] and jar["uid_tt"] == STORED[1], "an unchanged cookie moved"
    assert "_ga" not in jar, "a cookie the jar never held was taken"
    assert set(jar["ttwid"]) == set(STORED[2]), "the cookie lost or gained a field"


@mutation(U, EXPIRY_RULE, '            not session and abs(expires - (old.get("expirationDate") or 0)) >= 0)',
          expect="nothing rotated")
@mutation(U, ROTATE_RULE, '        if c["value"] == old.get("value") and False:', expect="nothing rotated")
def test_nothing_is_written_when_nothing_rotated_or_an_expiry_moved_by_seconds():
    run = Run()
    assert renew(run, browser(), stable=STABLE) is False and run.writes == [], "nothing rotated"
    assert renew(run, browser(ttwid=("W1", T0 + 30)), stable=STABLE) is False and run.writes == [], "clock jitter"
    # an expiry that moved by days is a rotation even with the same value (the site extended the cookie)
    assert renew(run, browser(ttwid=("W1", T0 + 2 * DAY)), stable=STABLE) is True


STABLE_RULE = "    if not all(_one_session(stored, browser, name, host) for name in stable):"


@mutation(U, STABLE_RULE, "    if False:", expect="another session")
def test_never_written_when_the_browser_lost_the_session_or_carries_another_one():
    for lost in (browser(sessionid=None, ttwid=("W2", T0)), browser(sessionid=("", -1), ttwid=("W2", T0)),
                 browser(sessionid=("S-GUEST", T0), ttwid=("W2", T0)),
                 browser(uid_tt=("U-OTHER", T0), ttwid=("W2", T0)), browser(uid_tt=None, ttwid=("W2", T0))):
        run = Run()
        assert renew(run, lost, stable=STABLE) is False and run.writes == [], "another session was written"


def jar_run(cookies):
    blob = json.dumps({"cookies": cookies}).encode()
    run = Run(db=Db(blob))
    run.account.credential = run.plain = blob
    return run


@mutation(U, "    if len(mine) != 1 or not mine[0].get(\"value\") or (mine[0].get(\"path\") or \"/\") != \"/\":",
          "    if not mine or not mine[0].get(\"value\"):", expect="not exactly one")
@mutation(U, "    if host is not None and not _on_host(mine[0][\"domain\"], host):", "    if False:",
          expect="another domain")
@mutation(U, "    return len(theirs) == 1 and _key(theirs[0]) == _key(mine[0]) and theirs[0].get(\"value\") == "
             "mine[0][\"value\"]", "    return theirs[0].get(\"value\") == mine[0][\"value\"]", expect="other path")
def test_dola_renews_only_when_the_jar_holds_exactly_one_root_path_sessionid_and_uid_tt_on_its_domain():
    rotated = browser(ttwid=("W2", T0 + DAY))
    assert renew(jar_run(STORED), rotated, stable=STABLE, host="dola.com") is True
    scoped = {**STORED[0], "path": "/chat", "value": "S-PATH"}
    for jar, why in ((STORED + [scoped], "not exactly one: a path-scoped duplicate"),
                     ([{**STORED[0], "path": "/chat"}] + STORED[1:], "not exactly one: a sessionid off the root path"),
                     (STORED[1:], "not exactly one: no sessionid in the jar"),
                     ([STORED[0], STORED[2], STORED[3]], "not exactly one: no uid_tt in the jar")):
        run = jar_run(jar)
        assert renew(run, rotated, stable=STABLE, host="dola.com") is False and run.writes == [], why
    elsewhere = [{**c, "domain": ".example.com"} if c["name"] in STABLE else c for c in STORED]
    run = jar_run(elsewhere)
    assert renew(run, [{**c, "domain": ".example.com"} if c["name"] in STABLE else c for c in rotated],
                 stable=STABLE, host="dola.com") is False, "a session of another domain was renewed"
    # the browser holds the same value at another path, or a second sessionid
    other_path = [{**c, "path": "/x"} if c["name"] == "sessionid" else c for c in rotated]
    two = rotated + [{**rotated[0], "path": "/chat"}]
    for cookies in (other_path, two):
        run = jar_run(STORED)
        assert renew(run, cookies, stable=STABLE, host="dola.com") is False, "the browser's other path was trusted"


@mutation(U, CAS_RULE, CAS_ANY, expect="S3")
def test_two_runs_from_one_jar_the_one_that_writes_second_never_overwrites_a_newer_rotation():
    # both runs loaded ttwid W1 (S1); run B saw W3 (S3) and wrote first; run A saw W2 (S2) and flow_user_country SG
    db = Db(json.dumps({"cookies": STORED}).encode())
    a, b = Run(db=db), Run(db=db)
    assert renew(b, browser(ttwid=("W3", T0 + DAY)), stable=STABLE) is True
    assert renew(a, browser(ttwid=("W2", T0 + DAY), flow_user_country=("SG", -1)), stable=STABLE) is True
    jar = jar_of(db.stored)
    assert jar["ttwid"]["value"] == "W3", "run A laid its older S2 over run B's S3: %s" % jar["ttwid"]["value"]
    assert jar["flow_user_country"]["value"] == "SG", "run A's own rotation of a cookie no one else renewed was lost"


@mutation(U, "        return await asyncio.wait_for(renew(), RENEW_TIMEOUT_S)", "        return await renew()",
          expect="late")
def test_a_renewal_that_hangs_on_the_browser_or_the_database_is_cut_and_writes_nothing(monkeypatch):
    monkeypatch.setattr(ui, "RENEW_TIMEOUT_S", 0.1)

    class Hung(Browser):
        async def cookies(self, urls):
            await asyncio.sleep(2)
            return self.jar
    for run, cookies in ((Run(), Hung(browser(ttwid=("W2", T0)))), (Run(slow_s=2), browser(ttwid=("W2", T0)))):
        t0 = time.monotonic()
        assert renew(run, cookies, stable=STABLE) is False, "a late renewal was written"
        assert time.monotonic() - t0 < 1, "the renewal was late: it held the verdict back"
        assert run.writes == [], "a late renewal wrote"


@mutation(U, "    except Exception as exc:      # noqa: BLE001 - the credential stays as it was; never a reason "
             "to fail the run", "    except KeyError as exc:", expect="RuntimeError")
def test_a_failed_write_or_an_unreadable_browser_is_a_warning_and_never_fails_the_run():
    warned = []
    log = SimpleNamespace(warning=lambda *a: warned.append(a[0] % a[1:]), info=lambda *a: None)
    assert renew(Run(error=RuntimeError("db down")), browser(ttwid=("W2", T0)), stable=STABLE, log=log) is False, \
        "a failed write"
    assert renew(Run(), Browser([], error=RuntimeError("closed")), stable=STABLE, log=log) is False
    assert warned == ["the rotated cookies were not written back (RuntimeError)"] * 2
    assert not any("W2" in w or "S1" in w for w in warned), "a cookie value reached the log"


MJAR = [{"name": "ecto_1_sess", "value": "E1", "domain": ".meta.ai", "path": "/", "session": False,
         "expirationDate": T0}]
CHALLENGE = ("rd_challenge", "_vcrcs")
META = {"only": CHALLENGE, "domains": ("meta.ai", "www.meta.ai"), "at_most_one": ("ecto_1_sess", "abra_sess")}


def meta_browser(session="E2", extra=()):
    return [{"name": "ecto_1_sess", "value": session, "domain": ".meta.ai", "path": "/", "expires": T0 + 9 * DAY},
            {"name": "rd_challenge", "value": "RC1", "domain": ".meta.ai", "path": "/", "expires": T0 + DAY,
             "httpOnly": True, "secure": True, "sameSite": "None"},
            {"name": "wd", "value": "1280x720", "domain": ".meta.ai", "path": "/", "expires": T0 + DAY}, *extra]


def meta_run(db=None):
    run = Run(db=db or Db(json.dumps({"cookies": MJAR}).encode()))
    run.account.credential = run.plain = json.dumps({"cookies": MJAR}).encode()
    return run


DOMAIN_RULE = ("        if domains is not None and (c[\"domain\"].lstrip(\".\").lower() not in domains or "
               "(c.get(\"path\") or \"/\") != \"/\"):")


@mutation(U, "        if not c.get(\"value\") or (only is not None and c[\"name\"] not in only):",
          "        if not c.get(\"value\"):", expect="only the challenge")
@mutation(U, DOMAIN_RULE, "        if False:", expect="another host")
def test_with_only_the_challenge_names_a_missing_one_is_added_and_nothing_else_is_written():
    run = meta_run()
    evil = {"name": "_vcrcs", "value": "V-EVIL", "domain": "evil.example", "path": "/", "expires": -1}
    stray = {"name": "_ga", "value": "GA", "domain": ".meta.ai", "path": "/", "expires": T0 + DAY}
    assert renew(meta_run(), [evil], **META) is False, "a cookie of another host was added"
    assert renew(run, meta_browser(extra=(evil, stray)), **META) is True
    jar = jar_of(run.stored)
    assert jar["ecto_1_sess"] == MJAR[0], "a rotated session was written: only the challenge cookies may be"
    assert sorted(jar) == ["ecto_1_sess", "rd_challenge"], "a cookie other than only the challenge ones: %s" % (
        sorted(jar),)
    assert jar["rd_challenge"] == {"name": "rd_challenge", "domain": ".meta.ai", "path": "/", "secure": True,
                                   "httpOnly": True, "sameSite": "no_restriction", "value": "RC1", "session": False,
                                   "expirationDate": T0 + DAY}
    run = meta_run()
    assert renew(run, meta_browser(session="E3")[:1], **META) is False
    assert run.writes == [], "a session change alone was written"


@mutation(U, "    data[\"cookies\"] += [c for k, c in changes.items() if k not in held and k not in started]",
          "    data[\"cookies\"] += [c for k, c in changes.items() if k not in started]", expect="RC-B")
def test_a_challenge_cookie_another_run_added_first_is_kept():
    db = Db(json.dumps({"cookies": MJAR}).encode())
    a, b = meta_run(db), meta_run(db)
    rc_b = [{**meta_browser()[1], "value": "RC-B"}]
    assert renew(b, rc_b, **META) is True
    assert renew(a, meta_browser(), **META) is True
    cookies = json.loads(db.stored)["cookies"]
    assert [c["value"] for c in cookies if c["name"] == "rd_challenge"] == ["RC-B"], (
        "run A added its older challenge over run B's RC-B: %s" % cookies)


@mutation(U, DOMAIN_RULE, "        if False:", expect="exact host")
def test_a_challenge_cookie_is_kept_only_at_the_root_path_of_meta_ai_or_www_meta_ai():
    rc = meta_browser()[1]
    for cookie in ({**rc, "domain": ".www.meta.ai"}, {**rc, "domain": "www.meta.ai"}):
        assert renew(meta_run(), [cookie], **META) is True, cookie["domain"]
    for cookie in ({**rc, "domain": ".sub.meta.ai"}, {**rc, "domain": "evilmeta.ai"}, {**rc, "path": "/chat"}):
        run = meta_run()
        assert renew(run, [cookie], **META) is False and run.writes == [], "not the exact host and root path: %s" % (
            cookie,)


@mutation(U, "    if sum(c[\"name\"] in at_most_one for c in stored) > 1:", "    if False:", expect="two session")
def test_nothing_is_kept_when_the_meta_jar_holds_more_than_one_session_cookie():
    two = MJAR + [{**MJAR[0], "name": "abra_sess", "value": "A1"}]
    for jar in (two, MJAR + [{**MJAR[0], "path": "/x"}]):
        blob = json.dumps({"cookies": jar}).encode()
        run = Run(db=Db(blob))
        run.account.credential = run.plain = blob
        assert renew(run, meta_browser(), **META) is False and run.writes == [], "two session cookies: written"


def test_a_session_cookie_stays_one_and_a_dropped_cookie_stays_as_stored():
    changes = rotated_cookies(STORED, browser(flow_user_country=("SG", -1), ttwid=None))
    assert list(changes.values()) == [{**STORED[3], "value": "SG"}]
