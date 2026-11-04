"""The gpt credential: what an import accepts, the one sealed shape, and the session write-back and its merge."""

import json

import pytest
from gptkit import BLOB, SESSION0, SESSION1, assert_no_secret, export

from relay_core.testing.mutation import mutation
from relay_gpt.constants import SESSION_COOKIE
from relay_gpt.credential import jar_of, merge_blobs, parse_credential, session_parts, with_session

CRED_FILE = "packages/provider-gpt/src/relay_gpt/credential.py"
NEEDS_SESSION = "    if not any(c[\"value\"] for c in kept if is_session_part(c[\"name\"])):"
NO_SESSION_CHECK = "    if False and not any(c[\"value\"] for c in kept if is_session_part(c[\"name\"])):"
NEWER_WINS = "    newer = mine if _renewed_at(mine) >= _renewed_at(stored) else stored"
OLDER_WINS = "    newer = mine if _renewed_at(mine) < _renewed_at(stored) else stored"
S0, S1 = SESSION_COOKIE + ".0", SESSION_COOKIE + ".1"


def parse(data) -> bytes:
    return parse_credential(json.dumps(data).encode()).blob


def refusal(data) -> str:
    with pytest.raises(ValueError) as e:
        parse_credential(data if isinstance(data, bytes) else json.dumps(data).encode())
    return str(e.value)


def test_cookie_editor_export_keeps_only_chatgpt_cookies():
    parsed = parse_credential(json.dumps(export()).encode())
    assert parsed.hint == "gpt"
    sealed = json.loads(parsed.blob)
    assert set(sealed) == {"cookies", "renewed_at"} and sealed["renewed_at"] == 0.0
    assert {c["name"]: c["domain"] for c in sealed["cookies"]} == {
        S0: "chatgpt.com", S1: "chatgpt.com", "oai-did": ".chatgpt.com", "__cf_bm": ".chatgpt.com"}
    assert all(set(c) == {"name", "value", "domain", "path"} for c in sealed["cookies"])
    assert jar_of(parsed.blob) == {S0: SESSION0, S1: SESSION1, "oai-did": "did-0000-0000-0001",
                                   "__cf_bm": "cf-bm-value"}
    # a subdomain is chatgpt.com's; a look-alike is not
    blob = parse([*export(), {"domain": "ab.chatgpt.com", "name": "x", "value": "1"},
                  {"domain": "notchatgpt.com", "name": "y", "value": "2"}])
    assert "x" in jar_of(blob) and "y" not in jar_of(blob)


def test_url_cookies_shape_and_the_sealed_shape_parse_to_the_same_blob():
    assert parse({"url": "https://chatgpt.com", "cookies": export()}) == BLOB
    assert parse_credential(BLOB).blob == BLOB
    # a cookie without a domain takes the url's host
    bare = [{"name": c["name"], "value": c["value"]} for c in export()[:2]]
    assert session_parts(jar_of(parse({"url": "https://chatgpt.com/", "cookies": bare}))) == {S0: SESSION0,
                                                                                               S1: SESSION1}
    renewed = with_session(BLOB, {S0: "eyJ-new-PLANTED"}, 7.0)
    assert parse_credential(renewed).blob == renewed, "the sealed shape keeps its renewed_at"


@mutation(CRED_FILE, NEEDS_SESSION, NO_SESSION_CHECK, expect="a jar without a session must be refused")
def test_a_jar_without_a_session_part_is_refused_without_echoing_values():
    no_session = [c for c in export() if not c["name"].startswith(SESSION_COOKIE)]
    empty = [{**c, "value": ""} if c["name"] == S0 else c for c in no_session + export()[:1]]
    elsewhere = [{**c, "domain": ".openai.com"} for c in export()]
    for data in (no_session, empty, elsewhere):
        try:
            parse_credential(json.dumps(data).encode())
        except ValueError as e:
            assert_no_secret(str(e))
        else:
            raise AssertionError("a jar without a session must be refused")
    # the bare name counts as the session
    bare = parse([{"domain": "chatgpt.com", "name": SESSION_COOKIE, "value": SESSION0}])
    assert session_parts(jar_of(bare)) == {SESSION_COOKIE: SESSION0}


def test_new_session_parts_replace_the_old_set_whole():                  # Review Focus 3
    blob = with_session(BLOB, {SESSION_COOKIE + ".0": "eyJ-new-PLANTED"}, 2.0)
    assert set(session_parts(jar_of(blob))) == {SESSION_COOKIE + ".0"}
    assert jar_of(blob)[S0] == "eyJ-new-PLANTED" and json.loads(blob)["renewed_at"] == 2.0
    assert {k: v for k, v in jar_of(blob).items() if k not in (S0, S1)} == {
        "oai-did": "did-0000-0000-0001", "__cf_bm": "cf-bm-value"}, "the other cookies stay"
    unsplit = with_session(blob, {SESSION_COOKIE: "eyJ-whole-PLANTED"}, 3.0)
    assert session_parts(jar_of(unsplit)) == {SESSION_COOKIE: "eyJ-whole-PLANTED"}
    resplit = with_session(unsplit, {S0: "a", S1: "b"}, 4.0)
    assert session_parts(jar_of(resplit)) == {S0: "a", S1: "b"}
    assert {c["domain"] for c in json.loads(resplit)["cookies"] if c["name"] in (S0, S1)} == {"chatgpt.com"}


@mutation(CRED_FILE, NEWER_WINS, OLDER_WINS, expect="the newer session must win the merge")
def test_merge_keeps_the_newer_session_set():
    old = with_session(BLOB, {S0: "old-0", S1: "old-1"}, 5.0)
    new = with_session(BLOB, {S0: "new-0"}, 6.0)
    for stored, mine in ((old, new), (new, old)):
        merged = jar_of(merge_blobs(stored, mine))
        assert session_parts(merged) == {S0: "new-0"}, "the newer session must win the merge"
    assert json.loads(merge_blobs(old, new))["renewed_at"] == 6.0
    # a tie goes to mine; the other cookies come from the stored blob
    tie = with_session(BLOB, {S0: "tie-0"}, 5.0)
    assert session_parts(jar_of(merge_blobs(old, tie))) == {S0: "tie-0"}
    stored = json.loads(old)
    stored["cookies"] = [{**c, "value": "cf-new"} if c["name"] == "__cf_bm" else c for c in stored["cookies"]]
    merged = jar_of(merge_blobs(json.dumps(stored).encode(), new))
    assert merged["__cf_bm"] == "cf-new" and merged[S0] == "new-0" and S1 not in merged


def test_errors_never_carry_a_cookie_value():
    bad = [
        b"", b"not json eyJ-PLANTED-x", b"[]", b'{"cookies": "eyJ-PLANTED-x"}',
        json.dumps([{"name": S0, "value": "eyJ-PLANTED-x; injected=1", "domain": "chatgpt.com"}]).encode(),
        json.dumps([{"name": "bad name", "value": "eyJ-PLANTED-x", "domain": "chatgpt.com"}]).encode(),
        json.dumps([{"name": S0, "value": "eyJ-PLANTED-x"}]).encode(),          # no domain and no url
        json.dumps([{"name": S0, "value": "eyJ-PLANTED-x", "domain": "chatgpt.com"}] * 301).encode(),
        json.dumps({"cookies": export(), "renewed_at": "eyJ-PLANTED-x"}).encode(),
    ]
    for raw in bad:
        assert_no_secret(refusal(raw))
    for blob in (b"eyJ-PLANTED-x", b'{"cookies":[{"name": "eyJ-PLANTED-x"}]}'):
        with pytest.raises(ValueError) as e:
            jar_of(blob)
        assert_no_secret(str(e.value))
