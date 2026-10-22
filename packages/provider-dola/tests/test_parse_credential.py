"""Importing a dola credential (coverage SESS02): every accepted shape becomes one sealed shape."""

import json
import logging

import pytest

from relay_core.providers import Proxy
from relay_core.testing.mutation import mutation
from relay_dola import credential as cred
from relay_dola.spec import SPEC

CREDENTIAL_FILE = "packages/provider-dola/src/relay_dola/credential.py"

SECRET = "SECRET-SESSION-VALUE-9f3a"      # a planted value: it must never show up in an error or a log line
UID = "0123456789abcdef"


def editor(**extra):
    """A Cookie-Editor export, the way the extension writes it."""
    return [
        {"domain": ".dola.com", "name": "sessionid", "value": SECRET, "path": "/", "secure": True, "httpOnly": True,
         "sameSite": "no_restriction", "session": False, "expirationDate": 1794045504.5, "hostOnly": False,
         "storeId": "0", **extra},
        {"domain": ".dola.com", "name": "uid_tt", "value": UID, "path": "/", "secure": True, "httpOnly": False,
         "sameSite": "lax", "session": True},
        {"domain": "www.dola.com", "name": "ttwid", "value": "x", "session": True},
    ]


def sealed(raw):
    return json.loads(SPEC.parse_credential(raw if isinstance(raw, bytes) else json.dumps(raw).encode()).blob)


def test_a_cookie_editor_export_keeps_the_cookies_and_drops_the_extension_noise():
    blob = sealed(editor())
    assert set(blob) == {"cookies"} and [c["name"] for c in blob["cookies"]] == ["sessionid", "uid_tt", "ttwid"]
    first, second, third = blob["cookies"]
    assert first["sameSite"] == "no_restriction" and first["expirationDate"] == 1794045504.5 and not first["session"]
    assert second["session"] is True and "expirationDate" not in second, "a session cookie must carry no expiry"
    assert third["path"] == "/" and third["secure"] is False and third["domain"] == "www.dola.com"
    assert "storeId" not in first and "hostOnly" not in first


def test_a_storage_state_becomes_the_same_shape():
    state = {"cookies": [
        {"name": "sessionid", "value": SECRET, "domain": ".dola.com", "path": "/", "expires": 1794045504.5,
         "httpOnly": True, "secure": True, "sameSite": "None"},
        {"name": "uid_tt", "value": UID, "domain": ".dola.com", "path": "/", "expires": -1, "httpOnly": False,
         "secure": True, "sameSite": "Lax"}],
        "origins": [{"origin": "https://www.dola.com", "localStorage": [{"name": "token", "value": SECRET}]}]}
    blob = sealed(state)
    assert SECRET not in json.dumps(blob["cookies"][1]) and "origins" not in blob, "local storage is not sealed"
    a, b = blob["cookies"]
    assert a["sameSite"] == "no_restriction" and a["expirationDate"] == 1794045504.5 and a["session"] is False
    assert b["sameSite"] == "lax" and b["session"] is True and "expirationDate" not in b


def test_a_cookie_header_string_becomes_cookies_on_the_dola_domain():
    blob = sealed(("sessionid=%s; uid_tt=%s; ttwid=a=b" % (SECRET, UID)).encode())
    assert [(c["name"], c["value"], c["domain"]) for c in blob["cookies"]] == [
        ("sessionid", SECRET, ".dola.com"), ("uid_tt", UID, ".dola.com"), ("ttwid", "a=b", ".dola.com")]
    assert all(c["secure"] and c["session"] for c in blob["cookies"])
    with pytest.raises(ValueError):
        sealed(b"sessionid; uid_tt=1")


def test_old_jar_shapes_are_accepted():
    jar = {"cookies": editor(), "proxy": "proxy.example:8080:user:hunter2"}
    for shape in (jar, editor(), {"jars": [jar]}, [jar]):
        blob = sealed(shape)
        assert [c["name"] for c in blob["cookies"]] == ["sessionid", "uid_tt", "ttwid"]
        assert "hunter2" not in json.dumps(blob) and "proxy" not in blob, "the jar's proxy belongs to the account row"
    with pytest.raises(ValueError) as e:
        sealed([jar, jar])
    assert "one account" in str(e.value) and SECRET not in str(e.value)


@mutation(CREDENTIAL_FILE, 'if not by_name.get(SESSION_COOKIE, {}).get("value"):', "if False:", expect="DID NOT RAISE")
def test_a_credential_without_the_identity_cookie_is_refused_with_a_fixed_reason():
    only_uid = [c for c in editor() if c["name"] != "sessionid"]
    empty = [{**c, "value": ""} if c["name"] == "sessionid" else c for c in editor()]
    for bad in (only_uid, empty):
        with pytest.raises(ValueError, match="no sessionid cookie"):
            SPEC.parse_credential(json.dumps(bad).encode())


@pytest.mark.parametrize("raw", [b"", b"   ", b"not json {", b"[]", b"{}", b'{"cookies": []}', b'{"cookies": "x"}',
                                 b'[1, 2]', b'[{"name": "a"}]', b'[{"name": "sessionid", "value": 3, "domain": "d"}]',
                                 b"\xff\xfe\x00", b'{"jars": []}'])
def test_garbage_is_a_value_error_never_another_exception(raw):
    with pytest.raises(ValueError):
        SPEC.parse_credential(raw)


def test_no_error_or_log_line_holds_a_cookie_value(caplog):
    broken = json.dumps([{"name": "sessionid", "value": SECRET, "domain": ""}]).encode()
    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError) as e:
        SPEC.parse_credential(broken)
    assert SECRET not in str(e.value) and SECRET not in caplog.text
    with pytest.raises(ValueError) as e:
        SPEC.parse_credential(("sessionid=%s; broken" % SECRET).encode())
    assert SECRET not in str(e.value)
    SPEC.parse_credential(json.dumps(editor()).encode())
    assert SECRET not in caplog.text


@mutation(CREDENTIAL_FILE, "    if not session:\n", "    if True:\n", expect="session cookie must carry no expiry")
def test_a_session_cookie_gets_no_expiry_so_the_browser_does_not_drop_it():
    cookies = sealed(editor())["cookies"]
    assert "expirationDate" not in cookies[1], "a session cookie must carry no expiry"
    again = cred.jar_from_blob(json.dumps({"cookies": cookies}))
    assert again == cookies, "the sealed shape reads back unchanged"


@mutation(CREDENTIAL_FILE, 'hint="dola")', 'hint="dola " + by_name["uid_tt"]["value"][:8])',
          expect="the hint holds part of a session cookie")
def test_the_account_hint_holds_no_part_of_a_session_cookie():
    parsed = SPEC.parse_credential(json.dumps(editor()).encode())
    cookies = cred.jar_from_blob(parsed.blob)
    assert parsed.hint == "dola", "the hint holds part of a session cookie: %r" % parsed.hint
    assert UID[:4] not in parsed.hint and SECRET[:4] not in parsed.hint
    assert set(cred.identity(cookies)) == {"sessionid", "uid_tt"}, "only the identity cookies are compared"
    assert cred.IDENTITY_COOKIE_NAMES == ("sessionid", "uid_tt")
    assert SPEC.parse_credential(b"sessionid=abc").hint == "dola"
    assert not hasattr(cred, "fingerprint"), "a label built from a cookie must not come back"


def test_the_cookie_header_and_the_proxy_url():
    cookies = cred.jar_from_blob(SPEC.parse_credential(json.dumps(editor()).encode()).blob)
    assert cred.cookie_header(cookies) == "sessionid=%s; uid_tt=%s; ttwid=x" % (SECRET, UID)
    assert cred.proxy_url(None) is None
    assert cred.proxy_url(Proxy("10.0.0.1", 8080)) == "http://10.0.0.1:8080"
    assert cred.proxy_url(Proxy("p.example", 3128, "us er:p@ss:w/rd")) == "http://us%20er:p%40ss%3Aw%2Frd@p.example:3128"
    assert cred.proxy_url(Proxy("p.example", 3128, "onlyuser")) == "http://onlyuser@p.example:3128"


def test_a_sealed_credential_that_is_not_a_credential_is_a_value_error():
    for bad in (b"", b"nope", b'{"cookies": []}'):
        with pytest.raises(ValueError):
            cred.jar_from_blob(bad)
