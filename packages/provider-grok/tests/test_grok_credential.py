"""The grok credential: what an import accepts and the one sealed shape."""

import json

import pytest
from grokkit import BLOB, SSO, SSO_RW, USER_ID, assert_no_secret, export

from relay_core.testing.mutation import mutation
from relay_grok.credential import jar_of, parse_credential

CRED_FILE = "packages/provider-grok/src/relay_grok/credential.py"
NEEDS_SSO = '    if not any(c["value"] for c in kept if c["name"] == SSO_COOKIE):'
NO_SSO_CHECK = '    if False and not any(c["value"] for c in kept if c["name"] == SSO_COOKIE):'
GROK_ONLY = '    kept = [c for c in cookies if on_site(c["domain"])]'
ANY_SITE = '    kept = list(cookies)'


def parse(data) -> bytes:
    return parse_credential(json.dumps(data).encode()).blob


def refusal(data) -> str:
    with pytest.raises(ValueError) as e:
        parse_credential(data if isinstance(data, bytes) else json.dumps(data).encode())
    return str(e.value)


@mutation(CRED_FILE, GROK_ONLY, ANY_SITE, expect="a stranger's cookie was kept")
def test_cookie_editor_export_keeps_only_grok_cookies():
    parsed = parse_credential(json.dumps(export()).encode())
    assert parsed.hint == "grok"
    sealed = json.loads(parsed.blob)
    assert set(sealed) == {"cookies"}
    assert {c["name"]: c["domain"] for c in sealed["cookies"]} == {
        "sso": ".grok.com", "sso-rw": ".grok.com", "x-userid": "grok.com", "__cf_bm": ".grok.com"}, \
        "a stranger's cookie was kept"
    assert all(set(c) == {"name", "value", "domain", "path"} for c in sealed["cookies"])
    assert jar_of(parsed.blob) == {"sso": SSO, "sso-rw": SSO_RW, "x-userid": USER_ID, "__cf_bm": "cf-bm-value"}
    # grok.com itself only: a look-alike, x.ai and a subdomain are not the account's
    blob = parse([*export(), {"domain": "notgrok.com", "name": "y", "value": "2"},
                  {"domain": "accounts.x.ai", "name": "z", "value": "3"}])
    assert not {"y", "z"} & set(jar_of(blob))


def test_header_and_url_cookies_and_sealed_shapes_parse_to_one_blob():
    assert parse({"url": "https://grok.com", "cookies": export()}) == BLOB
    assert parse_credential(BLOB).blob == BLOB, "the sealed shape reads back to itself"
    # a cookie without a domain takes the url's host
    bare = [{"name": "sso", "value": SSO}]
    assert jar_of(parse({"url": "https://grok.com/imagine", "cookies": bare})) == {"sso": SSO}
    # a Cookie header, with or without its name
    for text in ("sso=%s; sso-rw=%s; x-userid=%s" % (SSO, SSO_RW, USER_ID),
                 "Cookie: sso=%s; sso-rw=%s; x-userid=%s" % (SSO, SSO_RW, USER_ID),
                 "cookie:sso=%s;sso-rw=%s;x-userid=%s;" % (SSO, SSO_RW, USER_ID)):
        header = parse_credential(text.encode())
        assert jar_of(header.blob) == {"sso": SSO, "sso-rw": SSO_RW, "x-userid": USER_ID}
        assert {c["domain"] for c in json.loads(header.blob)["cookies"]} == {".grok.com"}
        assert parse_credential(header.blob).blob == header.blob


@mutation(CRED_FILE, NEEDS_SSO, NO_SSO_CHECK, expect="a jar without sso was accepted")
def test_a_jar_without_sso_is_refused_without_echoing_values():
    no_sso = [c for c in export() if c["name"] != "sso"]
    empty_sso = [{**c, "value": ""} if c["name"] == "sso" else c for c in export()]
    elsewhere = [{**c, "domain": ".x.ai"} if c["name"] == "sso" else c for c in export()]
    for jar in (no_sso, empty_sso, elsewhere):
        try:
            parse(jar)
            why = None
        except ValueError as e:
            why = str(e)
        assert why == "the credential has no grok.com sso cookie", "a jar without sso was accepted"
        assert_no_secret(why)
    assert refusal(("sso-rw=%s" % SSO_RW).encode()) == "the credential has no grok.com sso cookie"


def test_errors_never_carry_a_cookie_value():
    bad_sso = [{**c, "value": SSO + ";x"} if c["name"] == "sso" else c for c in export()]
    reasons = [
        refusal(b""), refusal(b"{not json " + SSO.encode()), refusal(dict(cookies=[])), refusal([SSO]),
        refusal([{"name": "sso", "value": SSO}]),                        # no domain and no url
        refusal({"url": "http://user:%s@[bad" % SSO, "cookies": export()}),
        refusal(bad_sso), refusal(("sso%s" % SSO).encode()), refusal([*export()] * 60),
        refusal(("; ".join("c%d=%s" % (i, SSO) for i in range(301))).encode())]
    assert reasons[6] == "the grok.com sso cookie holds characters a cookie cannot carry"
    assert reasons[-1] == reasons[-2] == "too many cookies"
    assert_no_secret(*reasons)
    with pytest.raises(ValueError) as e:
        jar_of(b"garbage " + SSO.encode())
    assert str(e.value) == "the sealed grok credential is not readable"
    assert_no_secret(str(e.value))
    # an unclean cookie other than sso is dropped, not a refusal
    blob = parse([*export(), dict(domain=".grok.com", name="g_prefs", value='{"i_l":0}')])
    assert "g_prefs" not in jar_of(blob) and jar_of(blob)["sso"] == SSO
