"""The on-demand schema check: `python -m relay_meta.engine.drift --account <label>`.

meta's deploys can move the protobuf field numbers, the gateway query constants or the `product_context` strings that
`engine/wire.py` and `constants.py` rely on. This compares the live public bundle with what is pinned here: each message
and field the wire uses (name, number, type) and the enum values it sends (`ATTACHMENT_TYPE_IMAGE` = 1), then the
gateway app id and the `PRODUCT_CONTEXT` strings.

It prints `OK` or one line per difference: names and numbers only, never a value from the page. It does one page read
(it counts as a page load: run it with meta drained or on a rested account) and fetches the `/_next/static/…js`
scripts the page names on a cookie-less session through the same proxy; no cookie or token ever goes to them.
"""

import argparse
import asyncio
import base64
import binascii
import os
import re
import sys
from types import SimpleNamespace

from relay_meta.constants import GATEWAY_QUERY, PRODUCT_CONTEXT, SITE_URL, UA
from relay_meta.engine.wire import FrameError, dec

Schema = dict[str, dict[str, tuple[int, str]]]

PACKAGE = "clippy.ecto"
SCRIPT_TIMEOUT_S = 60.0
# message (or enum) -> field (or enum value) -> (number, type). Type: a scalar's name, `msg:<Message>` or
# `enum:<Enum>`; an enum value is `(number, "value")`. From fixtures/meta/http-261105/schema_261105.json, 05/11/2026.
PINNED: Schema = {
    "Request": {"metadata": (1, "msg:RequestMetadata"), "prompt": (2, "msg:Prompt")},
    "RequestMetadata": {
        "product_context": (1, "msg:ProductContext"), "user_ids": (2, "msg:UserIdentifiers"), "traffic_control":
        (3, "msg:TrafficControl"), "auth_context": (4, "msg:AuthContext"), "trace_context": (5,
        "msg:MetaAITraceContext"), "request_id": (6, "string"), "model_input_overrides": (7,
        "msg:ModelInputOverrides"), "locale_info": (9, "msg:LocaleInfo"), "conversation_context_ids": (10,
        "msg:ConversationContextIds"), "location_data": (15, "msg:LocationData"), "clippy_overrides": (16,
        "msg:ClippyOverrides"), "inline_capabilities": (18, "msg:RequestMetadata.InlineCapabilitiesEntry"),
        "client_capabilities": (20, "enum:ClientCapability"), "client_capability_configs": (23,
        "msg:ClientCapabilityConfig"), "meta_subs_client_signal": (26, "enum:MetaSubsClientSignal"),
    },
    "ProductContext": {
        "entry_point": (1, "string"), "app_id": (2, "string"), "config_key": (4, "string"), "product_data": (5,
        "msg:ProductData"), "app_type": (6, "enum:AppType"), "bot_chat_type": (7, "string"), "persona": (8,
        "msg:Persona"), "app_name": (10, "string"), "client_name": (11, "string"), "product_config": (12,
        "msg:ProductConfig"), "device_os": (13, "string"), "prompt_type": (14, "string"), "user_agent": (15,
        "string"), "client_interface": (16, "string"), "bloks_rendering_context": (19,
        "msg:BloksRenderingContext"),
    },
    "ProductData": {"meta_ai_product_data": (5, "msg:MetaAIProductData")},
    "MetaAIProductData": {"conversation_id": (1, "string")},
    "Persona": {"persona_id": (1, "string"), "persona_version": (2, "string")},
    "ProductConfig": {"agent_config": (3, "msg:AgentConfig"), "response_config": (4, "msg:ResponseConfig")},
    "AgentConfig": {"agent_type": (1, "enum:AgentType"), "agent_id": (2, "string")},
    "ResponseConfig": {"generate_conversation_title": (1, "bool")},
    "BloksRenderingContext": {"bloks_versioning_id": (1, "string"), "pixel_density": (2, "float")},
    "UserIdentifiers": {
        "account_id": (1, "int64"), "app_account_id": (2, "int64"), "user_type": (3, "enum:UserType"),
    },
    "TrafficControl": {"is_dgw_request": (4, "bool")},
    "MetaAITraceContext": {
        "clippy_payload_publish_timestamp_ms": (1, "int64"), "www_request_start_timestamp_ms": (3, "int64"),
    },
    "LocaleInfo": {"user_locale": (2, "string")},
    "ConversationContextIds": {
        "prompt_session_id": (1, "string"), "qpl_join_id": (3, "string"), "client_thread_id": (4, "string"),
    },
    "LocationData": {"client_timezone": (4, "string")},
    "ClippyOverrides": {"writes_enabled_via_node_api_for_cp": (22, "bool")},
    "RequestMetadata.InlineCapabilitiesEntry": {"key": (1, "string"), "value": (2, "msg:InlineCapability")},
    "InlineCapability": {"version": (1, "int32")},
    "ClientCapabilityConfig": {"capability": (1, "enum:ClientCapability"), "client_tool_name": (3, "string")},
    "Prompt": {
        "prompt_id": (1, "msg:PromptId"), "content": (2, "string"), "attachments": (3, "msg:Attachment"),
        "prompt_edit_config": (4, "msg:PromptEditConfig"),
    },
    "PromptId": {"identifier": (1, "string"), "mid": (2, "msg:MessageId"), "is_new_conversation": (5, "bool")},
    "MessageId": {"conversation_id": (1, "string"), "timestamp_ms": (2, "int64"), "unique_message_id": (3, "int64")},
    "Attachment": {
        "id": (1, "msg:AttachmentId"), "type": (2, "enum:AttachmentType"), "urls": (3,
        "msg:AttachmentURLBundle"), "mime_type": (6, "string"), "filename": (7, "string"),
    },
    "AttachmentId": {"fbid": (1, "int64")},
    "PromptEditConfig": {"current_branch_path": (1, "string")},
    "Response": {"piece": (1, "msg:ResponsePiece"), "is_final_chunk": (3, "bool")},
    "ResponsePiece": {"model_response": (4, "msg:ModelResponse"), "tool_output": (2, "msg:ToolOutputResponse")},
    "ModelResponse": {"tool_outputs": (6, "msg:ToolOutputResponse"), "pp_response_so_far": (2, "string")},
    "ToolOutputResponse": {"attachments": (2, "msg:Attachment"), "tool_result_serialized": (4, "bytes")},
    "AttachmentURLBundle": {"cdn_url_original": (1, "string")},
    "AttachmentType": {"ATTACHMENT_TYPE_IMAGE": (1, "value")},
}

_TYPES = {1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32", 8: "bool", 9: "string", 12: "bytes",
          13: "uint32"}
_FILE = re.compile(r'"(C[gh][A-Za-z0-9+/_-]{200,}=*)"')
_SCRIPT = re.compile(r'(?:src|href)="(/_next/static/[^"?#]+\.js)')


def _kv(raw: bytes) -> dict[int, object]:
    return {f: v for f, _, v in dec(raw, 0)}


def _enum(raw: bytes, name: str, out: Schema) -> None:
    out[name] = {_kv(v)[1].decode(): (_kv(v).get(2, 0), "value") for f, _, v in dec(raw, 0) if f == 2}


def _message(raw: bytes, prefix: str, package: str, out: Schema) -> None:
    parts = dec(raw, 0)
    name = prefix + _kv(raw)[1].decode()
    fields = {}
    for f, _, v in parts:
        if f != 2:
            continue
        d = _kv(v)
        kind, target = d.get(5), d.get(6, b"").decode().removeprefix("." + package + ".")
        fields[d[1].decode()] = (d[3], "msg:" + target if kind == 11 else "enum:" + target if kind == 14
                                 else _TYPES.get(kind, "type%s" % kind))
    out[name] = fields
    for f, _, v in parts:
        if f == 3:
            _message(v, name + ".", package, out)
        elif f == 4:
            _enum(v, name + "." + _kv(v)[1].decode(), out)


def decode_schema(js_text: str) -> Schema:
    """The `clippy.ecto` messages and enums of every FileDescriptorProto the bundle embeds (protobuf-es keeps each
    .proto as a base64 string), by full dotted name. A string that is not one is skipped."""
    out: Schema = {}
    for m in _FILE.finditer(js_text):
        b64 = m.group(1).replace("-", "+").replace("_", "/")
        try:
            parts = dec(base64.b64decode(b64 + "=" * (-len(b64) % 4)), 0)
            if not any(f == 2 and v == PACKAGE.encode() for f, _, v in parts):
                continue
            for f, _, v in parts:
                if f == 4:
                    _message(v, "", PACKAGE, out)
                elif f == 5:
                    _enum(v, _kv(v)[1].decode(), out)
        except (FrameError, binascii.Error, KeyError, AttributeError):
            continue
    return out


def diff(found: Schema, pinned: Schema) -> list[str]:
    """One line per pinned message, field or enum value the bundle lacks or has moved. Names and numbers only."""
    lines = []
    for message, fields in pinned.items():
        if message not in found:
            lines.append("%s: missing" % message)
            continue
        for field, (number, kind) in fields.items():
            now = found[message].get(field)
            if now is None:
                lines.append("%s.%s: missing" % (message, field))
            elif now != (number, kind):
                lines.append("%s.%s: pinned %d %s, found %d %s" % (message, field, number, kind, *now))
    return lines


def check_constants(js_text: str) -> list[str]:
    """The gateway app id and every non-empty string of `PRODUCT_CONTEXT` must appear in the bundle. By name (the
    query key, the field path) only."""
    lines = []
    if GATEWAY_QUERY["x-dgw-appid"] not in js_text:
        lines.append("gateway query x-dgw-appid: not in the bundle")
    lines += ["product_context %s: not in the bundle" % path for path, v in PRODUCT_CONTEXT
              if isinstance(v, str) and v and v not in js_text]
    return lines


def scripts_of(html: str) -> list[str]:
    """The `/_next/static/…js` paths a page names, once each, in order."""
    return list(dict.fromkeys(_SCRIPT.findall(html)))


def report(js_text: str) -> list[str]:
    return diff(decode_schema(js_text), PINNED) + check_constants(js_text)


class Unreadable(Exception):
    """The bundle could not be read; the text is safe to print (no value from the page, no proxy login)."""


async def fetch_bundle(account, settings, **web_kw) -> str:
    """One page read of `account` through its proxy, then each script the page names on a cookie-less session through
    the same proxy (no redirect followed). The scripts' text joined; `Unreadable` otherwise."""
    # imported here: the pure part above needs no curl_cffi
    from relay_meta.engine.web import Sessions, Web

    try:
        async with Web(account, settings, Sessions(), **web_kw) as web:      # no ctx: no cookie is written back
            page = await web.page()
            if page.state in ("vercel", "unreadable"):      # not meta's own page: its scripts are not the bundle
                raise Unreadable("the page is not meta's (page state: %s)" % page.state)
            names = scripts_of(page.html)
            if not names:
                raise Unreadable("the page names no scripts (page state: %s)" % page.state)
            bare = web.make_session(web.proxies)
            try:
                texts = []
                for name in names:
                    resp = await web._request(bare, "GET", SITE_URL + name, {"User-Agent": UA}, SCRIPT_TIMEOUT_S)
                    if resp.status_code == 200:
                        texts.append(resp.text or "")
            finally:
                await bare.close()
    except Unreadable:
        raise
    except Exception as e:      # noqa: BLE001 - only the kind is kept: curl's text may hold the proxy's login
        raise Unreadable("could not read the bundle (%s)" % type(e).__name__) from None
    if not texts:
        raise Unreadable("no script of the page answered 200")
    return "\n".join(texts)


async def _run(label: str) -> int:
    # imported here: the pure part above needs no database
    from relay_core import crypto, pool
    from relay_core.db.pool import open_pool, tx
    from relay_core.jobs.runctx import load_account_view
    from relay_core.jobs.worker import provider_settings
    from relay_meta.spec import SPEC

    defaults = {s.key: s.default for s in provider_settings(SPEC).specs.values()}   # stored overrides are not read
    keys = crypto.private_keys_from_env(os.environ)
    db = await open_pool(os.environ["DATABASE_URL"], name="drift-meta")
    try:
        async with tx(db) as conn:
            row = await (await conn.execute("SELECT id FROM accounts WHERE provider = 'meta' AND label = %s",
                                            (label,))).fetchone()
        if row is None:
            print("no meta account with that label")
            return 2
        account = await load_account_view(db, row[0], keys, pool.pool_configs(SPEC.name, SPEC.caps, defaults.get))
    finally:
        await db.close()
    try:
        js_text = await fetch_bundle(account, SimpleNamespace(get=defaults.__getitem__))
    except Unreadable as e:
        print(e)
        return 2
    lines = report(js_text)
    print("\n".join(lines) if lines else "OK")
    return 1 if lines else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m relay_meta.engine.drift", description=__doc__.splitlines()[0])
    ap.add_argument("--account", required=True, help="the label of a meta account (its proxy is used)")
    return asyncio.run(_run(ap.parse_args(argv).account))


if __name__ == "__main__":
    sys.exit(main())
