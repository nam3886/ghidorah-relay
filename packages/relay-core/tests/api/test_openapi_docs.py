"""The public OpenAPI schema (contracts §8): valid, public routes only, the hand-parsed bodies and the error
envelope declared."""

import json
import re

from apikit import run_app
from fastapi.openapi.models import OpenAPI

from relay_core.testing.mutation import mutation

DOCS = "packages/relay-core/src/relay_core/api/openapi_docs.py"
CREATE_BODIES = {
    ("post", "/v1/video/generations"): "VideoBody",
    ("post", "/v1/images/generations"): "ImageBody",
    ("post", "/v1/chat/completions"): "ChatBody",
    ("post", "/v1beta/models/{model}:generateContent"): "GenerateBody",
    ("post", "/v1beta/models/{model}:streamGenerateContent"): "GenerateBody",
    ("post", "/models/{model}:generateContent"): "GenerateBody",
    ("post", "/models/{model}:streamGenerateContent"): "GenerateBody",
}


async def fetch(env) -> dict:
    r = await env.client.get("/openapi.json")          # no key: the schema is public
    assert r.status_code == 200
    return r.json()


@mutation(DOCS, 'if p != ADMIN_PREFIX and not p.startswith(ADMIN_PREFIX + "/")}', "}",
          expect="no admin path in the public schema")
@mutation(DOCS, "    _prune(schema)\n", "    pass\n", expect="no schema only the admin routes used")
def test_the_schema_is_valid_and_lists_no_admin_route(db):
    async def scenario(env):
        schema = await fetch(env)
        OpenAPI.model_validate(schema)
        assert not [p for p in schema["paths"] if p.startswith("/admin")], "no admin path in the public schema"
        assert "X-Admin-Session" not in json.dumps(schema)
        used = set(schema["components"]["schemas"])
        assert not used & {"KeyCreate", "KeyPatch", "ImportBody", "SettingPut", "LoginBody", "Session",
                           "HTTPValidationError"}, "no schema only the admin routes used: %s" % sorted(used)
        # the full schema still has them: only the served one is cut
        assert any(p.startswith("/admin") for p in env.app.openapi()["paths"])

    run_app(db, scenario)


@mutation(DOCS, 'op["requestBody"] = {', 'op["requestBodyX"] = {', expect="has a declared request body")
def test_the_hand_parsed_bodies_of_every_door_are_declared(db):
    async def scenario(env):
        schema = await fetch(env)
        for (method, path), name in CREATE_BODIES.items():
            op = schema["paths"][path][method]
            ref = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            assert ref.get("$ref") == "#/components/schemas/" + name, "%s %s has a declared request body" % (
                method, path)
            body = schema["components"]["schemas"][name]
            assert body.get("additionalProperties") is False, "unknown fields are refused (D27)"
        fields = {n: set(schema["components"]["schemas"][n]["properties"]) for n in ("VideoBody", "ImageBody")}
        assert {"model", "prompt", "duration", "ratio", "n", "images", "callback_url"} <= fields["VideoBody"]
        assert {"style", "template"} <= fields["ImageBody"] and "duration" not in fields["ImageBody"]
        assert set(schema["components"]["schemas"]["ChatBody"]["properties"]) == {"model", "messages", "stream"}
        assert set(schema["components"]["schemas"]["GenerateBody"]["properties"]) == {
            "contents", "systemInstruction", "generationConfig"}, "the Gemini names are the wire names"

    run_app(db, scenario)


@mutation(DOCS, '"GeminiError" if gemini else "Error"', '"Error"', expect="Gemini routes declare the Gemini error")
@mutation(DOCS, 'op.setdefault("responses", {}).pop("422", None)', 'op.setdefault("responses", {})',
          expect="a bad request is a 400, not a 422")
def test_every_operation_declares_the_error_envelope_and_a_key(db):
    async def scenario(env):
        schema = await fetch(env)
        error = schema["components"]["schemas"]["Error"]
        assert set(error["properties"]["error"]["properties"]) == {"code", "message", "type", "param"}
        assert set(schema["components"]["securitySchemes"]) == {"BearerAuth", "ApiKeyHeader", "GoogleApiKeyHeader"}
        for path, ops in schema["paths"].items():
            for method, op in ops.items():
                gemini = path.startswith(("/v1beta", "/models"))
                want = "#/components/schemas/" + ("GeminiError" if gemini else "Error")
                got = op["responses"]["default"]["content"]["application/json"]["schema"]["$ref"]
                assert got == want, "Gemini routes declare the Gemini error: %s %s" % (method, path)
                assert "422" not in op["responses"], "a bad request is a 400, not a 422: %s" % path
                assert op["description"] and op["tags"], (method, path)
                assert bool(op.get("security")) == (path not in ("/healthz", "/m/{token}")), (method, path)

    run_app(db, scenario)


APP = "packages/relay-core/src/relay_core/api/app.py"


@mutation(APP, "    app.include_router(health)\n",
          "    for _p in ('/docs', '/redoc'):\n        health.add_api_route(_p, openapi_json)\n"
          "    app.include_router(health)\n", expect="is not served")
def test_there_is_no_docs_or_redoc_page_only_the_schema(db):
    """The customer API page is `/reference` (Scalar, vendored); Swagger and ReDoc loaded CDN scripts on the admin's
    origin, so they are gone (D57)."""
    async def scenario(env):
        for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
            r = await env.client.get(path)
            assert r.status_code == 404, "%s is not served: %d" % (path, r.status_code)
        assert (await env.client.get("/openapi.json")).status_code == 200

    run_app(db, scenario)


@mutation(APP, "        if schema is None:\n", "        if True:\n", expect="the schema is built once")
def test_the_public_schema_is_built_once_per_app_and_served_from_the_copy(db, monkeypatch):
    from relay_core.api import app as app_module

    built = []
    real = app_module.public_schema

    def counting(app):
        built.append(1)
        return real(app)

    monkeypatch.setattr(app_module, "public_schema", counting)

    async def scenario(env):
        first = await fetch(env)
        for _ in range(3):
            assert await fetch(env) == first
        assert len(built) == 1, "the schema is built once: %d builds" % len(built)
        assert not [p for p in first["paths"] if p.startswith("/admin")], "the cached copy is still public-only"

    run_app(db, scenario)


# plan and decision labels, old-system names and code notes that must never reach a client's reading
INTERNAL = re.compile(r"\bD\d{1,3}\b|\bv4\b|\bv2\b|contracts|\blane\b|\bphase\b|TODO|FIXME|XXX|ponytail|\[P9\]|"
                      r"dola2api|meta2api|vibesai2api", re.IGNORECASE)


def strings(node):
    """Every key and string value of a JSON document."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)
    elif isinstance(node, str):
        yield node


@mutation(DOCS, "Create a video job. Charged at creation", "Create a video job (D27). Charged at creation",
          expect="an internal note in the public schema")
def test_the_public_schema_text_is_english_and_carries_no_internal_note(db):
    async def scenario(env):
        return await fetch(env)

    texts = list(strings(run_app(db, scenario)))
    assert texts
    for text in texts:
        assert not INTERNAL.search(text), "an internal note in the public schema: %r" % text[:200]
        assert text.isascii() or not re.search(r"[\u00c0-\u1ef9]", text), "not English: %r" % text[:200]
