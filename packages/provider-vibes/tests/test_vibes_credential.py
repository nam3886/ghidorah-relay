"""Importing a vibes credential: every accepted shape becomes one sealed shape that carries `meta_session`."""

import json

import pytest

from relay_core.testing.mutation import mutation
from relay_vibes import credential as cred
from relay_vibes.spec import SPEC

CREDENTIAL_FILE = "packages/provider-vibes/src/relay_vibes/credential.py"
SECRET = "SECRET-VIBES-SESSION-7c1d.Ab_9-x%3D"      # a planted value: it must never show up in an error or the hint


def editor(**extra):
    """A Cookie-Editor export of vibes.ai, with an auth.meta.com cookie beside it."""
    return [
        {"domain": "vibes.ai", "name": "meta_session", "value": SECRET, "path": "/", "secure": True, "httpOnly": True,
         "sameSite": "lax", "session": False, "expirationDate": 1794045504.5, "hostOnly": True, "storeId": "0",
         **extra},
        {"domain": "vibes.ai", "name": "cookie_ack", "value": "true", "path": "/"},
        {"domain": ".auth.meta.com", "name": "abra_sess", "value": "renewal-only", "path": "/"},
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


def test_a_cookie_editor_export_seals_the_session_and_keeps_every_cookie_without_the_noise():
    blob = sealed(editor())
    assert blob["meta_session"] == SECRET
    assert [(c["name"], c["domain"]) for c in blob["cookies"]] == [
        ("meta_session", "vibes.ai"), ("cookie_ack", "vibes.ai"), ("abra_sess", ".auth.meta.com")]
    assert all(set(c) == {"name", "value", "domain"} for c in blob["cookies"])
    assert SPEC.parse_credential(json.dumps(editor()).encode()).hint == "vibes"
    assert cred.session_of(SPEC.parse_credential(json.dumps(editor()).encode()).blob) == SECRET


def test_the_url_wrapped_export_the_bare_header_and_the_single_value_all_give_the_session():
    wrapped = {"url": "https://vibes.ai/", "cookies": [{"name": "meta_session", "value": SECRET}]}
    assert sealed(wrapped)["meta_session"] == SECRET, "the export's url gives a cookie its domain"
    for header in ("cookie_ack=true; meta_session=%s" % SECRET, "Cookie: meta_session=%s" % SECRET):
        blob = sealed(header.encode())
        assert blob["meta_session"] == SECRET and all(c["domain"] == "vibes.ai" for c in blob["cookies"])
    assert sealed({"meta_session": SECRET}) == {
        "meta_session": SECRET, "cookies": [{"name": "meta_session", "value": SECRET, "domain": "vibes.ai"}]}
    assert sealed([{"domain": ".www.vibes.ai", "name": "meta_session", "value": SECRET}])["meta_session"] == SECRET


NEEDS_SESSION = ('    session = next((c["value"] for c in cookies\n'
                 '                    if c["name"] == SESSION_COOKIE and c["value"] and on_site(c["domain"])), None)')
ANY_VALUE = ('    session = next((c["value"] for c in cookies\n'
             '                    if c["name"] == SESSION_COOKIE and on_site(c["domain"])), None)')


@mutation(CREDENTIAL_FILE, NEEDS_SESSION, ANY_VALUE, expect="no vibes.ai meta_session cookie")
@mutation(CREDENTIAL_FILE, '    return host == SITE_HOST or host.endswith("." + SITE_HOST)', "    return True",
          expect="no vibes.ai meta_session cookie")
def test_a_credential_without_a_vibes_ai_session_is_refused():
    no_session = [{"domain": "vibes.ai", "name": "cookie_ack", "value": "true"}]
    other_site = [{"domain": ".meta.ai", "name": "meta_session", "value": SECRET},
                  {"domain": "notvibes.ai", "name": "meta_session", "value": SECRET}]
    empty = [{"domain": "vibes.ai", "name": "meta_session", "value": ""}]
    for raw in (no_session, other_site, empty, {"meta_session": ""}, b"cookie_ack=true", b"meta_session="):
        got = refusal(raw)
        assert "no vibes.ai meta_session cookie" in got, "wanted: no vibes.ai meta_session cookie, got %r" % (got,)


@mutation(CREDENTIAL_FILE, "    if not _COOKIE_VALUE.fullmatch(session):", "    if False:",
          expect="characters a cookie cannot carry")
def test_a_session_value_that_could_break_the_cookie_header_is_refused():
    for value in ("abc\r\nX-Evil: 1", "abc; other=1", "a b", 'a"b', "a,b", "a\\b", "café"):
        got = refusal({"meta_session": value})
        assert "characters a cookie cannot carry" in got, "a value that breaks the header: %r -> %r" % (value, got)
    with pytest.raises(ValueError, match="not readable"):
        cred.session_of(json.dumps({"meta_session": "a\r\nb"}).encode())


@mutation(CREDENTIAL_FILE, "    if len(data) > MAX_COOKIES:", "    if False:", expect="too many cookies")
@mutation(CREDENTIAL_FILE, "        if len(cookies) > MAX_COOKIES:", "        if False:", expect="too many cookies")
def test_a_list_longer_than_any_real_export_is_refused_in_every_shape():
    jar = editor() + [{"domain": "vibes.ai", "name": "c%d" % i, "value": "v"} for i in range(cred.MAX_COOKIES)]
    assert refusal(jar) == "too many cookies"
    header = "meta_session=%s; " % SECRET + "; ".join("c%d=v" % i for i in range(cred.MAX_COOKIES))
    assert refusal(header.encode()) == "too many cookies"


def test_malformed_input_is_refused_with_a_fixed_reason():
    assert refusal(b"") == "empty credential"
    assert refusal(b"[not json") == "the credential is not valid JSON"
    assert refusal([]) == "no cookies" and refusal({"url": "https://vibes.ai"}) == "no cookies"
    assert refusal([{"name": "meta_session", "value": SECRET}]) == "a cookie needs a name, a value and a domain"
    assert refusal(["meta_session"]) == "a cookie must be an object"
    assert refusal(b"meta_session") == "a Cookie header holds name=value pairs"
    assert refusal({"meta_session": 12}) == "a cookie needs a name, a value and a domain"
    for blob in (b"", b"[]", b'{"cookies": []}'):
        with pytest.raises(ValueError, match="not readable"):
            cred.session_of(blob)
