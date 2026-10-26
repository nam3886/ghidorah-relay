"""The public OpenAPI schema: valid, public routes only, the hand-parsed bodies and the error
envelope declared."""

import importlib
import json
import re
from pathlib import Path

import pytest
from apikit import run_app
from doorkit import IMAGE, VIDEO, finish_failed
from fastapi.openapi.models import OpenAPI
from test_status_progress import NEW_KEYS, plant, samples

from relay_core import errors
from relay_core.api import doors, openapi_docs, progress
from relay_core.api.schemas import JobCreated, JobStatus
from relay_core.testing.mutation import mutation

# as `relay serve` runs: every provider's codes are registered before the schema is built
_REPO = Path(__file__).resolve().parents[4]
for _spec in sorted(_REPO.glob("packages/provider-*/src/relay_*/spec.py")):
    importlib.import_module(_spec.parent.name + ".spec")

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
                      r"\bpool\b|\bworker\b|\baccount|\bproxy|cookie|captcha|\bbrowser\b|\bsession\b|upstream|solver",
                      re.IGNORECASE)


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


@mutation(DOCS, '_CHARGE = "Charged at creation', '_CHARGE = "Charged (D27) at creation',
          expect="an internal note in the public schema")
@mutation(SCHEMAS, "your place in the line while queued", "your place in the worker pool while queued",
          expect="an internal note in the public schema")
@mutation("packages/relay-core/src/relay_core/errors.py", 'text="The render was lost before its result arrived.',
          'text="The account was lost before its result arrived.', expect="an internal note in the public schema")
def test_the_public_schema_text_is_english_and_carries_no_internal_note(db):
    async def scenario(env):
        return await fetch(env)

    texts = list(strings(run_app(db, scenario)))
    assert texts
    for text in texts:
        prose = re.sub(r"`[a-z0-9_]+\.[a-z0-9_]+`", "", text)      # a code's name is fixed: only the prose counts
        assert not INTERNAL.search(prose), "an internal note in the public schema: %r" % text[:200]
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


GEMINI = "packages/relay-core/src/relay_core/api/routes_gemini.py"


def fields_of(schemas: dict, name: str, seen: set[str]):
    """(component, field, property) of every field a body reaches: its own, and those of the parts it refers to."""
    if name in seen:
        return
    seen.add(name)
    for field, prop in schemas[name].get("properties", {}).items():
        yield name, field, prop
        refs: set[str] = set()
        _refs_in(prop, refs)
        for ref in sorted(refs):
            yield from fields_of(schemas, ref, seen)


def _refs_in(node, found: set[str]) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            found.add(node["$ref"].rsplit("/", 1)[-1])
        for v in node.values():
            _refs_in(v, found)
    elif isinstance(node, list):
        for v in node:
            _refs_in(v, found)


@mutation(NATIVE, "title: str | None = Field(default=None, description=(",
          "title: str | None = Field(default=None, title=(", expect="every body field is documented")
@mutation(GEMINI, 'description="An input image sent by URL."', 'title="An input image sent by URL."',
          expect="every body field is documented")
def test_every_field_of_every_request_body_is_documented_and_each_body_has_an_example(db):
    async def scenario(env):
        return await fetch(env)

    schema = run_app(db, scenario)
    schemas, seen, found = schema["components"]["schemas"], set(), []
    for name in sorted(set(CREATE_BODIES.values())):
        assert schemas[name].get("examples"), "%s has an example" % name
        for component, field, prop in fields_of(schemas, name, seen):
            found.append((component, field))
            assert len(prop.get("description", "").split()) >= 3, "every body field is documented: %s.%s" % (
                component, field)
    assert {("VideoBody", "source_job_id"), ("ImageBody", "source_index"), ("_Inline", "mimeType"),
            ("_ImageUrl", "url")} <= set(found), "the walk reaches the nested parts: %s" % sorted(found)


def test_every_documented_refusal_is_a_registered_code_under_its_own_status(db):
    async def scenario(env):
        return await fetch(env)

    from relay_core import errors

    schema = run_app(db, scenario)
    listed = 0
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            for status, answer in op["responses"].items():
                for code in re.findall(r"`([a-z_]+\.[a-z_]+)`", answer.get("description", "")):
                    listed += 1
                    assert errors.rule(code).code == code, "%s %s names an unknown code %s" % (method, path, code)
                    assert str(errors.http_status(code)) == status, (method, path, code, status)
    assert listed > 50, "the create calls list their refusals"
    intro = schema["info"]["description"]
    for topic in ("## Authentication", "## Job lifecycle", "## Polling and webhooks", "not signed", "## Points",
                  "## Errors", "GET /v1/providers/{provider}/options"):
        assert topic in intro, "the introduction covers %r" % topic
    assert schema["webhooks"]["jobEnded"]["post"]["requestBody"]


@mutation(DOCS, '("get", "/v1/points"): ("Usage",','("get", "/v1/points"): ("Account",', expect="points tag is Usage")
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


# ---- the reference: failure codes, servers, operationIds, version, limits, headers ----------------------------------
APP_SCHEMA_LINE = '        return {**schema, "servers": [{"url": public_base(request)}]}\n'


@mutation(DOCS, "%s Sending again helps: %s Points refunded.", "%s %s Points refunded.", expect="is listed")
@mutation(DOCS, '    error_code["description"] += ', '    error_code["descriptionX"] = ', expect="is listed")
def test_the_status_lists_the_neutral_codes_a_job_can_end_with_and_no_internal_code(db):
    async def scenario(env):
        return await fetch(env)

    schema = run_app(db, scenario)
    text = schema["components"]["schemas"]["JobStatus"]["properties"]["error_code"]["description"]
    listed = set(re.findall(r"^- `([a-z0-9_.]+)`: ", text, re.MULTILINE))
    ending = {r.public_code for r in errors.RULES.values() if errors.ends_job(r)}
    assert ending == set(errors.PUBLIC_CODES), "every neutral code is one a job can end with: %s" % sorted(ending)
    for code, (helps, sentence) in errors.PUBLIC_CODES.items():
        assert "- `%s`: %s Sending again helps: %s Points refunded." % (code, sentence, helps) in text, \
            "%s is listed" % code
    assert listed == ending, "a code that never ends a job is listed: %s" % sorted(listed ^ ending)
    served = json.dumps(schema)
    inner = sorted(c for c in errors.RULES if c.split(".")[0] not in errors.CLIENT_DOMAINS and c in served)
    assert not inner, "an internal code is in the served reference: %s" % inner
    shown = sorted(r.public_text for r in errors.RULES.values() if r.public_text and r.public_text in served)
    assert not shown, "the reference lists the neutral codes' sentences, not each internal code's: %s" % shown


@mutation(DOCS, "at the price of its model option (`prices` in `GET /v1/models`). A job",
          "at the price of its model option (`prices` in `GET /v1/models`: the provider's own cost). A job",
          expect="never says a price is the provider's cost")
def test_the_reference_never_says_a_price_is_the_provider_cost_and_documents_the_server_and_the_empty_range(db):
    async def scenario(env):
        return await fetch(env)

    schema = run_app(db, scenario)
    served = json.dumps(schema)
    assert "own cost" not in served, "the reference never says a price is the provider's cost"
    assert "`public_address`" in schema["info"]["description"], "servers comes from public_address"
    answer = schema["paths"]["/m/{token}"]["get"]["responses"]["416"]["description"]
    assert "bytes=-0" in answer, answer


@mutation(APP, APP_SCHEMA_LINE, "        return schema\n", expect="servers")
def test_the_server_address_is_the_public_address_setting_else_the_request_base(db):
    async def scenario(env):
        first = await fetch(env)
        assert first.get("servers") == [{"url": "http://relay.test"}], "servers: %s" % first.get("servers")
        await env.settings.set("public_address", "https://api.example.com")
        second = await fetch(env)
        assert second["servers"] == [{"url": "https://api.example.com"}], "servers follow the setting"
        assert {k: v for k, v in second.items() if k != "servers"} == {
            k: v for k, v in first.items() if k != "servers"}

    run_app(db, scenario)


@mutation(DOCS, '    return _camel(names[(method, path)]) + ("Short" if path.startswith("/models") else "")',
          '    return _camel(names[(method, path)])', expect="operationIds are unique")
@mutation(DOCS, '    return head + "".join(w[:1].upper() + w[1:] for w in rest)', '    return name',
          expect="camelCase")
def test_every_operation_has_a_unique_camel_case_operation_id_named_after_its_route(db):
    async def scenario(env):
        return await fetch(env)

    schema = run_app(db, scenario)
    ids = [(op.get("operationId"), method, path) for path, ops in schema["paths"].items()
           for method, op in ops.items()]
    assert all(i for i, _, _ in ids), "every operation has an operationId: %s" % [x for x in ids if not x[0]]
    names = [i for i, _, _ in ids]
    assert len(names) == len(set(names)), "operationIds are unique: %s" % sorted(n for n in names if names.count(n) > 1)
    for name, method, path in ids:
        assert re.fullmatch(r"[a-z][a-zA-Z0-9]*", name), "camelCase: %s (%s %s)" % (name, method, path)
    paths = schema["paths"]
    assert paths["/v1beta/models/{model}:generateContent"]["post"]["operationId"] == "generateContent"
    assert paths["/models/{model}:generateContent"]["post"]["operationId"] == "generateContentShort"
    assert paths["/v1/video/generations"]["post"]["operationId"] == "createVideo"
    for path, ops in paths.items():
        if path.startswith("/models"):
            for method, op in ops.items():
                assert "Identical to %s /v1beta%s." % (method.upper(), path) in op["description"], path


@mutation(DOCS, "    schema[\"info\"].update(title=API_TITLE, version=API_VERSION, description=INTRO)\n",
          "    schema[\"info\"].update(description=INTRO)\n", expect="title and version")
def test_the_title_version_changes_and_limits_are_documented(db):
    async def scenario(env):
        return await fetch(env)

    schema = run_app(db, scenario)
    info = schema["info"]
    assert (info["title"], info["version"]) == ("Ghidorah Relay API", openapi_docs.API_VERSION), "title and version"
    assert "## Changes" in info["description"] and "2026-10-26" in info["description"]
    for word in ("## Limits", "`quota.too_many_queued`", "`quota.key_exceeded`", "max_unfinished_jobs",
                 "Retry-After", "x-should-retry"):
        assert word in info["description"], word
    for code in ("quota.too_many_queued", "quota.key_exceeded"):
        assert errors.http_status(code) == 429, code
    created = schema["paths"]["/v1/video/generations"]["post"]["responses"]["429"]["description"]
    assert "quota.too_many_queued" in created and "Retry-After" in created


MEDIA = ("/m/{token}", "/v1/video/generations/{job_id}/content", "/v1/video/generations/{job_id}/content/{idx}",
         "/v1/images/generations/{job_id}/content/{idx}")


@mutation(DOCS, '                op["responses"].update(copy.deepcopy(_MEDIA_RESPONSES))\n',
          "                pass\n", expect="file headers")
@mutation(DOCS, "            headers = copy.deepcopy(_NO_RETRY) if is_facade_path(path) else None\n",
          "            headers = None\n", expect="x-should-retry on the chat errors")
def test_the_downloads_and_the_facade_errors_document_the_headers_they_send(db):
    async def scenario(env):
        schema = await fetch(env)
        key = await env.new_key()
        live = await env.client.post("/v1/chat/completions", json={"model": "nope", "messages": []},
                                     headers=key.headers)
        assert live.status_code == 400 and live.headers.get("x-should-retry") == "false", live.headers
        return schema

    schema = run_app(db, scenario)
    for path in MEDIA:
        op = schema["paths"][path]["get"]
        answers = op["responses"]
        assert {"Content-Type", "Content-Length", "Accept-Ranges"} <= set(answers["200"].get("headers", {})), \
            "file headers: %s" % path
        assert "Content-Range" in answers["206"]["headers"] and "Content-Range" in answers["416"]["headers"], path
        assert any(p["name"] == "Range" and p["in"] == "header" for p in op["parameters"]), path
    chat = schema["paths"]["/v1/chat/completions"]["post"]["responses"]
    assert chat["400"].get("headers", {}).get("x-should-retry"), "x-should-retry on the chat errors"
    assert "text/event-stream" in chat["200"]["content"]
    assert "headers" not in schema["paths"]["/v1/video/generations"]["post"]["responses"]["400"], \
        "the native routes send no x-should-retry"


@mutation(DOCS, 'The range starts past the end of the file, or is bytes=-0. No body.", "headers": {',
          'The range starts past the end of the file, or is bytes=-0. No body.", "headerz": {',
          expect="not a valid OpenAPI document")
def test_the_served_schema_is_a_valid_openapi_document(db):
    """The whole served document, servers and webhooks included, against the OpenAPI 3.1 specification."""
    from openapi_spec_validator import validate

    async def scenario(env):
        return await fetch(env)

    schema = run_app(db, scenario)
    assert schema["openapi"].startswith("3.1") and schema["servers"] and schema["webhooks"]
    try:
        validate(schema)
    except Exception as e:      # the validator's own error class differs between its versions
        raise AssertionError("not a valid OpenAPI document: %s" % e) from None
