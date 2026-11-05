"""The on-demand schema check: the recorded schema against what `wire.py` pins, the constants check, and what it
prints. Fixtures only (fixtures/meta/http-261105); nothing here touches the network or a database."""

import asyncio
import base64
import copy
import itertools
import json

import pytest
from metakit import FIX, TOKEN, VIEWER, Ctx, Net, Resp, assert_no_secret, page, settings

from relay_core.testing.mutation import mutation
from relay_meta import drift
from relay_meta.constants import (
    FINAL_CHUNK_FIELD,
    FINAL_PATH,
    GATEWAY_QUERY,
    MIME_PATH,
    PRODUCT_CONTEXT,
    SITE_URL,
    TEXT_PATH,
    TOOL_RESULT_PATHS,
)
from relay_meta.engine.wire import Attachment, build_request, dec, enc

DRIFT_FILE = "packages/provider-meta/src/relay_meta/drift.py"
MOVED_CHECK = "            elif now != (number, kind):"
GONE_CHECK = "        if message not in found:"
APPID_CHECK = '    if GATEWAY_QUERY["x-dgw-appid"] not in js_text:'
STRING_CHECK = "              if isinstance(v, str) and v and v not in js_text]"
PACKAGE_CHECK = "            if not any(f == 2 and v == PACKAGE.encode() for f, _, v in parts):"
CONSTANTS = (FIX / "gateway_constants.txt").read_text(encoding="utf-8")
STRINGS = [v for _, v in PRODUCT_CONTEXT if isinstance(v, str) and v]


def bundle() -> str:
    """The recorded schema as the bundle embeds it (quoted base64 strings)."""
    files = json.loads((FIX / "schema_261105.json").read_text(encoding="utf-8"))
    return ";".join('x="%s"' % b64 for b64 in files.values())


def full_constants() -> str:
    """The recorded excerpts plus every string the excerpts leave out: the live bundle holds them all."""
    return CONSTANTS + " " + " ".join('"%s"' % s for s in STRINGS)


def test_the_recorded_schema_matches_what_wire_pins():
    assert drift.diff(drift.decode_schema(bundle()), drift.PINNED) == []


@mutation(DRIFT_FILE, MOVED_CHECK, "            elif False:")
def test_a_moved_field_number_is_named():
    found = copy.deepcopy(drift.decode_schema(bundle()))
    number, kind = found["Attachment"]["mime_type"]
    found["Attachment"]["mime_type"] = (number + 1, kind)
    assert drift.diff(found, drift.PINNED) == ["Attachment.mime_type: pinned 6 string, found 7 string"]


@mutation(DRIFT_FILE, GONE_CHECK, "        if False:")
def test_a_missing_field_message_and_enum_value_are_named():
    found = copy.deepcopy(drift.decode_schema(bundle()))
    del found["Response"]["is_final_chunk"]
    del found["PromptId"]
    found["AttachmentType"] = {"ATTACHMENT_TYPE_IMAGE": (9, "value")}
    assert drift.diff(found, drift.PINNED) == [
        "PromptId: missing",
        "Response.is_final_chunk: missing",
        "AttachmentType.ATTACHMENT_TYPE_IMAGE: pinned 1 value, found 9 value"]


def test_a_changed_field_type_is_named():
    found = copy.deepcopy(drift.decode_schema(bundle()))
    found["Prompt"]["content"] = (2, "bytes")
    assert drift.diff(found, drift.PINNED) == ["Prompt.content: pinned 2 string, found 2 bytes"]


@mutation(DRIFT_FILE, PACKAGE_CHECK, "            if False:")
def test_other_packages_and_junk_strings_are_ignored():
    junk = 'a="C' + "g" * 300 + '";b="Cg' + "A" * 300 + '"'
    other = enc([(1, "o.proto"), (2, "other.pkg"), (4, [(1, "Attachment"), (2, [(1, "z" * 300), (3, 1), (5, 9)])])])
    foreign = 'c="%s"' % base64.b64encode(other).decode()
    assert len(foreign) > 200       # long enough for the bundle scan to take it
    assert drift.decode_schema(bundle() + ";" + junk + ";" + foreign) == drift.decode_schema(bundle())


def test_pinned_covers_every_field_the_wire_sends_and_reads():
    """Schema-driven: every leaf of a built Request and every Response path the reader follows must be a pinned
    field, so a field the wire starts to use cannot go unpinned."""
    ids = (str(i) for i in itertools.count(1))
    request = build_request(text="t", conversation="c", viewer="1", attachments=[Attachment("5", "image/png", "a")],
                            now_ms=1, ids=lambda: next(ids), rand22=1)

    def walk(message: str, raw: bytes) -> None:
        by_number = {number: (name, kind) for name, (number, kind) in drift.PINNED[message].items()}
        for number, _, value in dec(raw, 0):
            assert number in by_number, "Request leaf %d of %s is not pinned" % (number, message)
            kind = by_number[number][1]
            if kind.startswith("msg:") and value:       # an empty message (`auth_context`) has no leaf to pin
                walk(kind[4:], value)

    def follow(path) -> None:
        message = "Response"
        for number in path:
            by_number = {n: kind for n, kind in drift.PINNED[message].values()}
            assert number in by_number, "Response path %s is not pinned at %s" % (path, message)
            message = by_number[number].removeprefix("msg:")

    walk("Request", request)
    for path in (FINAL_PATH, MIME_PATH, *TOOL_RESULT_PATHS, TEXT_PATH, (FINAL_CHUNK_FIELD,)):
        follow(path)
    assert drift.PINNED["AttachmentType"]["ATTACHMENT_TYPE_IMAGE"] == (1, "value")      # the enum `build_request` sends


def test_the_recorded_excerpts_hold_the_gateway_app_id():
    assert GATEWAY_QUERY["x-dgw-appid"] in CONSTANTS
    assert drift.check_constants(full_constants()) == []


@mutation(DRIFT_FILE, APPID_CHECK, "    if False:")
@mutation(DRIFT_FILE, STRING_CHECK, "              if False]")
def test_a_missing_gateway_constant_is_named():
    without = full_constants().replace(GATEWAY_QUERY["x-dgw-appid"], "")
    assert drift.check_constants(without) == ["gateway query x-dgw-appid: not in the bundle",
                                              "product_context 1.1.2: not in the bundle"]     # the same number
    without = full_constants().replace("blocking_confirmation", "")
    assert drift.check_constants(without) == ["product_context 1.23.3: not in the bundle"]


def test_the_output_holds_no_value_from_the_page():
    text = full_constants().replace("blocking_confirmation", "").replace(GATEWAY_QUERY["x-dgw-appid"], "")
    found = copy.deepcopy(drift.decode_schema(bundle()))
    found["Attachment"]["filename"] = (70, "string")
    lines = drift.diff(found, drift.PINNED) + drift.check_constants(text)
    assert len(lines) == 4
    out = "\n".join(lines)
    for value in (*(v for v in STRINGS if len(v) > 5), GATEWAY_QUERY["x-dgw-appid"]):
        assert value not in out
    assert_no_secret(out)
    assert TOKEN not in out and VIEWER not in out


def test_the_recorded_page_names_the_bundle_scripts():
    names = drift.scripts_of(page("page_signed_in"))
    assert names and all(n.startswith("/_next/static/") and n.endswith(".js") for n in names)
    assert "/_next/static/immutable/chunks/44jh16onv2k3y.js" in names
    assert len(names) == len(set(names))
    assert drift.scripts_of(page("page_signed_out")) == drift.scripts_of(page("page_signed_out"))


def test_report_joins_both_checks():
    assert drift.report(bundle() + full_constants()) == []
    assert drift.report("nothing here")[0] == "Request: missing"


def test_main_needs_an_account_label():
    with pytest.raises(SystemExit):
        drift.main([])


def run_fetch(routes: dict):
    ctx, net = Ctx(), Net(routes)
    return ctx, net, asyncio.run(drift.fetch_bundle(ctx.account, settings(), make_session=net.make_session))


def test_the_live_read_goes_through_the_proxy_and_the_scripts_carry_no_cookie():
    names = drift.scripts_of(page("page_signed_in"))
    routes = {("GET", SITE_URL + "/"): Resp(200, page("page_signed_in")),
              **{("GET", SITE_URL + n): Resp(200, "js-of-" + n) for n in names}}
    ctx, net, text = run_fetch(routes)
    assert text == "\n".join("js-of-" + n for n in names)
    page_call, *script_calls = net.calls
    assert page_call.cookies and [c.url for c in script_calls] == [SITE_URL + n for n in names]
    assert all(c.cookies == {} and not c.allow_redirects and c.proxies == page_call.proxies and c.proxies
               for c in script_calls)
    assert all(c.session is not page_call.session for c in script_calls)
    assert ctx.calls == []                      # no cookie written back, nothing sent to the platform
    assert all(s.closed for s in net.sessions)
    assert_no_secret(text)


def test_an_unreadable_bundle_says_why_without_a_value():
    signed_in = Resp(200, page("page_signed_in"))
    for routes, why in (({("GET", SITE_URL + "/"): Resp(200, page("page_vercel"))}, "page state: vercel"),
                        ({("GET", SITE_URL + "/"): ConnectionError("proxy user:PLANTED-pw failed")},
                         "could not read the bundle (NetError)"),
                        ({("GET", SITE_URL + "/"): signed_in, ("GET", SITE_URL + "/_next"): Resp(404)},
                         "no script of the page")):
        with pytest.raises(drift.Unreadable) as e:
            run_fetch(routes)
        assert why in str(e.value)
        assert_no_secret(e.value)
