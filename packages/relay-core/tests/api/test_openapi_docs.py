"""The public OpenAPI schema: valid, public routes only, the hand-parsed bodies and the error
envelope declared."""

import json
import re

import pytest
from apikit import run_app
from doorkit import IMAGE, VIDEO, finish_failed
from fastapi.openapi.models import OpenAPI
from test_status_progress import NEW_KEYS, plant, samples

from relay_core.api import doors, progress
from relay_core.api.schemas import JobCreated, JobStatus
from relay_core.testing.mutation import mutation

DOCS = "packages/relay-core/src/relay_core/api/openapi_docs.py"
SCHEMAS = "packages/relay-core/src/relay_core/api/schemas.py"
NATIVE = "packages/relay-core/src/relay_core/api/routes_native.py"
JOB_CALLS = {
    ("post", "/v1/video/generations"): "JobCreated",
    ("post", "/v1/images/generations"): "JobCreated",
    ("get", "/v1/video/generations/{job_id}"): "JobStatus",
    ("get", "/v1/images/generations/{job_id}"): "JobStatus",
}
CREATE_BODIES = {
    ("post", "/v1/video/generations"): "VideoBody",
    ("post", "/v1/images/generations"): "ImageBody",
    ("post", "/v1/chat/completions"): "ChatBody",
    ("post", "/v1beta/models/{model}:generateContent"): "GenerateBody",
    ("post", "/v1beta/models/{model}:streamGenerateContent"): "GenerateBody",
    ("post", "/models/{model}:generateContent"): "GenerateBody",
    ("post", "/models/{model}:streamGenerateContent"): "GenerateBody",
}


@pytest.fixture(autouse=True)
def _fresh_estimate_state():
    for state in (progress._SHAPES, progress._PINNED, progress._INFLIGHT):
        state.clear()


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
            assert body.get("additionalProperties") is False, "unknown fields are refused"
        fields = {n: set(schema["components"]["schemas"][n]["properties"]) for n in ("VideoBody", "ImageBody")}
        assert {"model", "prompt", "duration", "ratio", "n", "images", "callback_url"} <= fields["VideoBody"]
        assert "style" in fields["ImageBody"] and "duration" not in fields["ImageBody"]
        assert not {"template", "template_category"} & fields["ImageBody"], "the door advertises a template field"
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
    origin, so they are gone."""
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


# internal labels and code notes that must never reach a client's reading
INTERNAL = re.compile(r"\bD\d{1,3}\b|contracts|\blane\b|\bphase\b|TODO|FIXME|XXX|ponytail|\[P9\]|"
                      r"\bpool\b|\bworker\b|\baccount\b", re.IGNORECASE)


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
@mutation(SCHEMAS, "your place in the line while queued", "your place in the worker pool while queued",
          expect="an internal note in the public schema")
def test_the_public_schema_text_is_english_and_carries_no_internal_note(db):
    async def scenario(env):
        return await fetch(env)

    texts = list(strings(run_app(db, scenario)))
    assert texts
    for text in texts:
        assert not INTERNAL.search(text), "an internal note in the public schema: %r" % text[:200]
        assert text.isascii() or not re.search(r"[\u00c0-\u1ef9]", text), "not English: %r" % text[:200]


# ---- typed answers of the four native job calls ---------------------------------------------------------------------
@mutation(DOCS, 'op["responses"]["200"] = {', 'op["responses"]["200x"] = {', expect="typed response")
@mutation(DOCS, "set(_BODIES.values()) | set(_RESPONSES.values())", "set(_BODIES.values())", expect="component schema")
def test_the_native_job_calls_declare_typed_responses(db):
    async def scenario(env):
        schema = await fetch(env)
        for (method, path), name in JOB_CALLS.items():
            ref = schema["paths"][path][method]["responses"].get("200", {}).get("content", {}).get(
                "application/json", {}).get("schema", {})
            assert ref.get("$ref") == "#/components/schemas/" + name, "%s %s has a typed response" % (method, path)
            assert name in schema["components"]["schemas"], "%s is a component schema" % name
        assert "JobWarning" in schema["components"]["schemas"], "JobWarning is a component schema"
        OpenAPI.model_validate(schema)

    run_app(db, scenario)


@mutation(SCHEMAS, "    estimated_finish_at: Moment | None = Field(", "    finish_estimate: Moment | None = Field(",
          expect="fields match the live answer")
@mutation(SCHEMAS, '    id: str = Field(description="The id of this job.")',
          '    id: str = Field(default="", description="The id of this job.")',
          expect="every field is required")
def test_the_status_schema_matches_the_live_answer(db):
    async def scenario(env):
        schema = (await fetch(env))["components"]["schemas"]
        key = await env.new_key()
        made = (await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "a"},
                                      headers=key.headers)).json()
        body = (await env.client.get("/v1/video/generations/" + made["id"], headers=key.headers)).json()
        assert list(body) == list(schema["JobStatus"]["properties"]), "fields match the live answer, in order"
        assert list(made) == list(schema["JobCreated"]["properties"]), "fields match the live answer, in order"
        for name in ("JobStatus", "JobCreated", "JobWarning"):
            assert schema[name]["required"] == list(schema[name]["properties"]), "every field is required: " + name
            assert schema[name].get("additionalProperties") is not False, "a client must tolerate added fields"

    run_app(db, scenario)


@mutation(SCHEMAS, "    render_started_at: Moment | None = Field(", "    render_started_at: Moment = Field(",
          expect="real answer validates")
def test_real_answers_validate_against_the_models(db):
    async def scenario(env):
        schema = (await fetch(env))["components"]["schemas"]["JobStatus"]["properties"]
        for name in ("stage", "error", "error_code", "duration", "queue_position", "finished_at", "progress",
                     "render_started_at", "estimated_finish_at"):
            assert {"type": "null"} in schema[name]["anyOf"], "real answer validates: %s is explicitly nullable" % name
        key = await env.new_key()
        bodies = []

        async def read(job_id, path="/v1/video/generations"):
            body = (await env.client.get("%s/%s" % (path, job_id), headers=key.headers)).json()
            bodies.append(JobStatus.model_validate(body))       # real answer validates
            return body

        made = await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "a", "title": "t"},
                                     headers=key.headers)
        JobCreated.model_validate(made.json())
        again = await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "a", "title": "t"},
                                      headers={**key.headers, "Idempotency-Key": "k"})
        replay = await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "a", "title": "t"},
                                       headers={**key.headers, "Idempotency-Key": "k"})
        for answer in (again, replay):
            JobCreated.model_validate(answer.json())
        await read(made.json()["id"])                                                      # queued
        below = await read(plant(db, key.id, "waiting", clock_s=300))                      # below the minimum
        assert below["estimated_finish_at"] is None
        samples(db, key.id, 5, 600)
        progress._SHAPES.clear()
        measured = await read(plant(db, key.id, "waiting", clock_s=300))                   # on a measured shape
        assert isinstance(measured["estimated_finish_at"], str)
        await read(plant(db, key.id, "fetching", clock_s=60))
        await read(plant(db, key.id, "done", took_s=600))
        failed = (await env.client.post("/v1/video/generations", json={"model": VIDEO, "prompt": "b"},
                                        headers=key.headers)).json()["id"]
        await finish_failed(db, failed)
        gone = await read(failed)
        assert gone["refunded"] is True and [gone[k] for k in NEW_KEYS] == [None, None, None]
        warned = plant(db, key.id, "queued")
        db.run("UPDATE jobs SET warnings = '[{\"code\": \"w\", \"text\": \"t\"}]'::jsonb WHERE id = %s", warned)
        assert (await read(warned))["warnings"] == [{"code": "w", "text": "t"}]
        image = plant(db, key.id, "waiting", model=IMAGE, kind="image", clock_s=30)
        await read(image, "/v1/images/generations")
        assert len(bodies) == 8

    run_app(db, scenario)


@mutation(SCHEMAS, '''    stage: Literal["submitting", "waiting", "fetching", "unknown"] | None = Field(''',
          '''    stage: Literal["submitting", "waiting", "fetching"] | None = Field(''', expect="stage enum")
@mutation(SCHEMAS, '''    status: Literal["queued", "running", "done", "failed"] = Field(
        description="queued: waiting''', '''    status: Literal["queued", "done", "failed"] = Field(
        description="queued: waiting''', expect="status enum")
def test_the_enums_are_the_served_values(db):
    async def scenario(env):
        schema = (await fetch(env))["components"]["schemas"]
        public = doors._PUBLIC_STATUS.values()
        assert set(schema["JobStatus"]["properties"]["status"]["enum"]) == {v[0] for v in public}, "status enum"
        assert set(schema["JobCreated"]["properties"]["status"]["enum"]) == {v[0] for v in public}, "status enum"
        stage = schema["JobStatus"]["properties"]["stage"]["anyOf"]
        assert {v for part in stage for v in part.get("enum", [])} == {v[1] for v in public if v[1]}, "stage enum"
        assert {"type": "null"} in stage, "stage allows null"
        for name in ("status", "stage"):
            text = schema["JobStatus"]["properties"][name]["description"]
            assert "More values may be added" in text, "%s enum says more values may be added" % name

    run_app(db, scenario)


@mutation(SCHEMAS, "Annotated[int, Field(ge=0, le=100)] | None = Field(", "int | None = Field(",
          expect="progress bounds")
@mutation(SCHEMAS, '''description="Short machine-readable code of the note."''', '''description=""''',
          expect="every field is documented")
@mutation(DOCS, '_POLL_TEXT = "Poll every 10 to 30 seconds', '_POLL_TEXT = "Check back later', expect="poll guidance")
def test_every_field_is_documented_the_progress_is_bounded_and_no_formula_is_public(db):
    async def scenario(env):
        schema = await fetch(env)
        texts = []
        for name in ("JobCreated", "JobStatus", "JobWarning"):
            for field, prop in schema["components"]["schemas"][name]["properties"].items():
                assert len(prop.get("description", "").split()) >= 3, "every field is documented: %s.%s" % (name, field)
                texts.append(prop["description"])
        progress = schema["components"]["schemas"]["JobStatus"]["properties"]["progress"]
        bounded = next(part for part in progress["anyOf"] if part.get("type") == "integer")
        assert (bounded.get("minimum"), bounded.get("maximum")) == (0, 100) and {"type": "null"} in progress["anyOf"], (
            "progress bounds: 0 to 100 or null")
        for (method, path) in JOB_CALLS:
            if method == "get":
                text = schema["paths"][path][method]["description"]
                assert "Poll every" in text and "estimates" in text, "poll guidance: " + text
            texts.append(schema["paths"][path][method]["description"])
        for text in texts:      # the window, the minimum, the fallbacks, the cache and the formula stay internal
            assert not re.search(r"\b(20|30 days|3900|9000|65 minutes|90 seconds|300|500|formula|median|window)\b",
                                 text, re.IGNORECASE), "no formula in public text: %r" % text

    run_app(db, scenario)


@mutation(DOCS, '("get", "/v1/points"): ("Usage",', '("get", "/v1/points"): ("Account",', expect="points tag is Usage")
def test_the_points_route_is_tagged_usage_and_still_answers_the_same(db):
    async def scenario(env):
        schema = await fetch(env)
        assert schema["paths"]["/v1/points"]["get"]["tags"] == ["Usage"], "points tag is Usage"
        tags = {t for ops in schema["paths"].values() for op in ops.values() for t in op["tags"]}
        assert "Account" not in tags, "points tag is Usage: no Account tag remains"
        key = await env.new_key()
        r = await env.client.get("/v1/points", headers=key.headers)
        assert r.status_code == 200 and {"unfinished_jobs", "max_unfinished_jobs"} <= set(r.json()), r.text

    run_app(db, scenario)


@mutation(NATIVE, '@router.get("/v1/video/generations/{job_id}")',
          '@router.get("/v1/video/generations/{job_id}", response_model=dict)', expect="no response model")
def test_the_job_routes_have_no_response_model(db):
    async def scenario(env):
        full = env.app.openapi()["paths"]
        for (method, path) in JOB_CALLS:
            schema = full[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
            assert schema == {}, "no response model: %s %s would filter and reorder the answer: %s" % (
                method, path, schema)

    run_app(db, scenario)
