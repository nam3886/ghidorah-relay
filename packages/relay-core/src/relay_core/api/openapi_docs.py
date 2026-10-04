"""The public OpenAPI schema (contracts §8): what a client may call, and nothing of the admin tree.

FastAPI's own schema lists every route (admin included) and cannot see the create bodies, which the doors parse by
hand so that an unknown field is always `input.invalid`. `public_schema(app)` starts from FastAPI's schema, drops the
admin paths and the schemas only they used, declares the four request bodies explicitly, the error bodies, the
security schemes, a tag and an English description per operation. The result is served at `/openapi.json`.
"""

import copy
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from relay_core.api.routes_gemini import GenerateBody
from relay_core.api.routes_native import ImageBody, VideoBody
from relay_core.api.routes_openai import ChatBody

ADMIN_PREFIX = "/admin"
_REF = "#/components/schemas/"

_ERROR = {"type": "object", "required": ["error"], "properties": {"error": {
    "type": "object", "required": ["code", "message", "type", "param"],
    "properties": {"code": {"type": "string"}, "message": {"type": "string"}, "type": {"type": "string"},
                   "param": {"type": ["string", "null"]}}}}}
_GEMINI_ERROR = {"type": "object", "required": ["error"], "properties": {"error": {
    "type": "object", "required": ["code", "message", "status", "details"],
    "properties": {"code": {"type": "integer"}, "message": {"type": "string"}, "status": {"type": "string"},
                   "details": {"type": "array", "items": {"type": "object"}}}}}}
_SECURITY = {
    "BearerAuth": {"type": "http", "scheme": "bearer", "description": "Authorization: Bearer <API key>"},
    "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
    "GoogleApiKeyHeader": {"type": "apiKey", "in": "header", "name": "x-goog-api-key"},
}
_KEYLESS = {"/healthz", "/m/{token}"}

# request bodies the handlers parse themselves: (method, path) -> model
_BODIES: dict[tuple[str, str], type[BaseModel]] = {
    ("post", "/v1/video/generations"): VideoBody,
    ("post", "/v1/images/generations"): ImageBody,
    ("post", "/v1/chat/completions"): ChatBody,
    **{("post", "%s/models/{model}:%s" % (pre, verb)): GenerateBody
       for pre in ("/v1beta", "") for verb in ("generateContent", "streamGenerateContent")},
}

_DOCS: dict[tuple[str, str], tuple[str, str]] = {
    ("post", "/v1/video/generations"): ("Native", "Create a video job. Charged at creation; returns the job id."),
    ("get", "/v1/video/generations/{job_id}"): ("Native", "Read a video job (only the key that created it)."),
    ("post", "/v1/images/generations"): ("Native", "Create an image job. Charged at creation; returns the job id."),
    ("get", "/v1/images/generations/{job_id}"): ("Native", "Read an image job (only the key that created it)."),
    ("get", "/v1/video/generations/{job_id}/content"): (
        "Native", "Download the job's first video (only the key that created it). Supports Range."),
    ("get", "/v1/video/generations/{job_id}/content/{idx}"): (
        "Native", "Download the job's video number idx, from 0 (only the key that created it). Supports Range."),
    ("get", "/v1/images/generations/{job_id}/content/{idx}"): (
        "Native", "Download the job's image number idx, from 0 (only the key that created it). Supports Range."),
    ("post", "/v1/chat/completions"): (
        "OpenAI", "OpenAI-style chat completion: creates a job, then holds or streams until it ends. "
                  "Only model, messages and stream are accepted."),
    ("get", "/v1/models"): ("Catalog", "Models this key may use, with prices."),
    ("get", "/v1/models/aliases"): ("Catalog", "Model aliases this key may use."),
    ("get", "/v1beta/models"): ("Catalog", "Models in the Gemini list shape."),
    ("get", "/models"): ("Catalog", "Models in the Gemini list shape."),
    ("get", "/v1beta/models/{model}"): ("Catalog", "One model in the Gemini shape."),
    ("get", "/models/{model}"): ("Catalog", "One model in the Gemini shape."),
    ("get", "/v1/providers/{provider}/options"): ("Catalog", "Options a provider offers for creation requests."),
    ("get", "/v1/points"): ("Account", "This key's remaining limits per provider and kind, and its unfinished jobs."),
    ("get", "/m/{token}"): ("Media", "Download an output or input file by its share token. Supports Range."),
    ("get", "/healthz"): ("Service", "Liveness probe."),
}
for _pre in ("/v1beta", ""):
    _DOCS[("post", "%s/models/{model}:generateContent" % _pre)] = (
        "Gemini", "Gemini-style generateContent: creates a job and holds until it ends.")
    _DOCS[("post", "%s/models/{model}:streamGenerateContent" % _pre)] = (
        "Gemini", "Gemini-style streaming generateContent (server-sent events).")


def _refs(node: Any, found: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_REF):
            found.add(ref[len(_REF):])
        for v in node.values():
            _refs(v, found)
    elif isinstance(node, list):
        for v in node:
            _refs(v, found)


def _prune(schema: dict) -> None:
    """Drop component schemas no remaining path reaches (the admin bodies)."""
    comps = schema.get("components", {}).get("schemas", {})
    keep: set[str] = set()
    _refs(schema["paths"], keep)
    todo = list(keep)
    while todo:
        more: set[str] = set()
        _refs(comps.get(todo.pop(), {}), more)
        todo.extend(more - keep)
        keep |= more
    for name in [n for n in comps if n not in keep]:
        del comps[name]


def public_schema(app: FastAPI) -> dict:
    """The public-only schema of `app`. A new dict on every call."""
    schema = copy.deepcopy(app.openapi())
    schema["paths"] = {p: ops for p, ops in schema["paths"].items()
                       if p != ADMIN_PREFIX and not p.startswith(ADMIN_PREFIX + "/")}
    comps = schema.setdefault("components", {})
    schemas = comps.setdefault("schemas", {})
    schemas["Error"], schemas["GeminiError"] = copy.deepcopy(_ERROR), copy.deepcopy(_GEMINI_ERROR)
    for model in set(_BODIES.values()):
        body = model.model_json_schema(ref_template=_REF + "{model}")
        schemas.update(body.pop("$defs", {}))
        schemas[model.__name__] = body
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            if not isinstance(op, dict):
                continue
            tag, text = _DOCS.get((method, path), ("Other", ""))
            op["tags"], op["description"] = [tag], text or op.get("description", "")
            if (method, path) in _BODIES:
                op["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": {"$ref": _REF + _BODIES[(method, path)].__name__}}}}
            gemini = path.startswith(("/v1beta", "/models"))
            op.setdefault("responses", {}).pop("422", None)       # a bad request is a 400 with the error envelope
            op["responses"]["default"] = {
                "description": "Error", "content": {"application/json": {"schema": {
                    "$ref": _REF + ("GeminiError" if gemini else "Error")}}}}
            if path not in _KEYLESS:
                op["security"] = [{"BearerAuth": []}, {"ApiKeyHeader": []}] + (
                    [{"GoogleApiKeyHeader": []}] if gemini else [])
    comps["securitySchemes"] = copy.deepcopy(_SECURITY)
    _prune(schema)
    return schema
