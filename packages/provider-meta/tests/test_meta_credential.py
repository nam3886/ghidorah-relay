"""Importing a meta credential: every accepted cookie jar shape becomes one sealed shape."""

import json

from relay_core.testing.mutation import mutation
from relay_meta import credential as cred
from relay_meta.spec import SPEC

CREDENTIAL_FILE = "packages/provider-meta/src/relay_meta/credential.py"
SECRET = "SECRET-ECTO-SESSION-7c1d"      # a planted value: it must never show up in an error or the hint


def editor(**extra):
    """A Cookie-Editor export, the way the extension writes it."""
    return [
        {"domain": ".meta.ai", "name": "ecto_1_sess", "value": SECRET, "path": "/", "secure": True, "httpOnly": True,
         "sameSite": "no_restriction", "session": False, "expirationDate": 1794045504.5, "hostOnly": False,
         "storeId": "0", **extra},
        {"domain": ".meta.ai", "name": "datr", "value": "d", "path": "/", "secure": True, "sameSite": "lax",
         "session": True},
    ]


def sealed(raw):
    return json.loads(SPEC.parse_credential(raw if isinstance(raw, bytes) else json.dumps(raw).encode()).blob)


def refusal(raw) -> str:
    """The refusal text, or "accepted" when the credential was taken."""
    try:
        SPEC.parse_credential(raw if isinstance(raw, bytes) else json.dumps(raw).encode())
    except ValueError as e:
        assert SECRET not in str(e)
        return str(e)
    return "accepted"


def test_a_cookie_editor_export_keeps_the_cookies_and_drops_the_extension_noise():
    blob = sealed(editor())
    assert set(blob) == {"cookies"} and [c["name"] for c in blob["cookies"]] == ["ecto_1_sess", "datr"]
    first, second = blob["cookies"]
    assert first["sameSite"] == "no_restriction" and first["expirationDate"] == 1794045504.5 and not first["session"]
    assert second["session"] is True and "expirationDate" not in second, "a session cookie must carry no expiry"
    assert "storeId" not in first and "hostOnly" not in first
    assert SPEC.parse_credential(json.dumps(editor()).encode()).hint == "meta"


def test_an_edit_this_cookie_expiry_and_a_storage_state_become_the_same_shape():
    etc = [{"domain": "www.meta.ai", "name": "abra_sess", "value": SECRET, "expiry": 1794045504}]
    assert sealed(etc)["cookies"][0]["expirationDate"] == 1794045504
    state = {"cookies": [
        {"name": "ecto_1_sess", "value": SECRET, "domain": ".meta.ai", "path": "/", "expires": 1794045504.5,
         "httpOnly": True, "secure": True, "sameSite": "None"},
        {"name": "rd_challenge", "value": "x", "domain": ".meta.ai", "path": "/", "expires": -1, "httpOnly": False,
         "secure": True, "sameSite": "Lax"}], "origins": []}
    first, second = sealed(state)["cookies"]
    assert first["expirationDate"] == 1794045504.5 and first["sameSite"] == "no_restriction"
    assert second["session"] is True and "expirationDate" not in second


def test_a_cookie_header_string_gets_the_meta_domain():
    cookies = sealed(("ecto_1_sess=%s; datr=d" % SECRET).encode())["cookies"]
    assert [(c["name"], c["domain"], c["secure"]) for c in cookies] == [
        ("ecto_1_sess", ".meta.ai", True), ("datr", ".meta.ai", True)]


NEEDS_SESSION = ('    if not any(c["name"] in SESSION_COOKIES and c["value"] and on_site(c["domain"]) '
                 'for c in cookies):')
ANY_VALUE = '    if not any(c["name"] in SESSION_COOKIES and on_site(c["domain"]) for c in cookies):'


@mutation(CREDENTIAL_FILE, NEEDS_SESSION, "    if False:", expect="no meta.ai session cookie")
@mutation(CREDENTIAL_FILE, NEEDS_SESSION, ANY_VALUE, expect="no meta.ai session cookie")
@mutation(CREDENTIAL_FILE, '    return host == SITE_HOST or host.endswith("." + SITE_HOST)', "    return True",
          expect="no meta.ai session cookie")
def test_a_jar_without_a_meta_ai_session_cookie_is_refused():
    no_session = [{"domain": ".meta.ai", "name": "datr", "value": "d"}]
    other_site = [{"domain": ".notmeta.ai", "name": "ecto_1_sess", "value": SECRET},
                  {"domain": ".facebook.com", "name": "abra_sess", "value": SECRET}]
    empty_value = [{"domain": ".meta.ai", "name": "ecto_1_sess", "value": ""}]
    for jar in (no_session, other_site, empty_value, b"datr=d"):
        got = refusal(jar)
        assert "no meta.ai session cookie" in got, "wanted the refusal: no meta.ai session cookie, got %r" % got
    assert sealed([{"domain": "meta.ai", "name": "abra_sess", "value": SECRET}]), "the bare site domain is the site"


@mutation(CREDENTIAL_FILE, "    if len(data) > MAX_COOKIES:", "    if False:", expect="too many cookies")
@mutation(CREDENTIAL_FILE, "        if len(cookies) > MAX_COOKIES:", "        if False:", expect="too many cookies")
def test_a_jar_longer_than_any_real_one_is_refused_in_every_shape():
    jar = editor() + [{"domain": ".meta.ai", "name": "c%d" % i, "value": "v"} for i in range(cred.MAX_COOKIES)]
    assert refusal(jar) == "too many cookies"
    header = "ecto_1_sess=%s; " % SECRET + "; ".join("c%d=v" % i for i in range(cred.MAX_COOKIES))
    assert refusal(header.encode()) == "too many cookies"


def test_malformed_input_is_refused_with_a_fixed_reason():
    assert refusal(b"") == "empty credential"
    assert refusal(b"[not json") == "the credential is not valid JSON"
    assert refusal([]) == "no cookies" and refusal({"origins": []}) == "no cookies"
    assert refusal([{"name": "ecto_1_sess", "value": SECRET}]) == "a cookie needs a name, a value and a domain"
    assert refusal(["ecto_1_sess"]) == "a cookie must be an object"
    assert refusal(b"ecto_1_sess") == "a Cookie header holds name=value pairs"
