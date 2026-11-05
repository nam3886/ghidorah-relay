"""`/llms.txt` and `/llms-full.txt`: the public docs as Markdown, for AI tools that cannot run the `/reference` page.

Both come from the same text as `/openapi.json` (`openapi_docs`): the introduction, the poll rule and, for the full
file, the live public schema itself. Plain string building; no admin path and no internal code can appear, because
the schema holds none and the code table is `errors.PUBLIC_CODES`.
"""

from relay_core import errors
from relay_core.api.openapi_docs import _POLL_TEXT, INTRO

SUMMARY = INTRO.split("\n", 1)[0]
AGENT_RULES = (
    "Never send a create call again while its job is `queued` or `running`: it may already be accepted and charged. "
    "To retry safely, send the same `Idempotency-Key`.",
    "Poll the status call every 10 to 30 seconds, never in a tight loop.",
    "Branch on `error.code`, never on the message text.",
    "Take model names from `GET /v1/models`: the list depends on the key and changes.",
    "A chat or Gemini request without streaming waits at most %s seconds; then the answer is a poll hint and the job "
    "runs on. Take its `job_id` (Gemini `responseId`) and poll.",
)


def llms_txt(base: str, hold_s: int) -> str:
    rules = "\n".join("- " + r % hold_s if "%s" in r else "- " + r for r in AGENT_RULES)
    return f"""# Ghidorah API

> {SUMMARY} Image and video generation over HTTP, in a native, an OpenAI-chat and a Gemini shape.

Base URL: {base}

## Authentication
Send the API key as `Authorization: Bearer <key>`, `X-API-Key: <key>` or `x-goog-api-key: <key>`. Every route except
`/healthz` and `/m/{{token}}` needs one.

## Job flow
1. Create: `POST /v1/video/generations` or `POST /v1/images/generations`. The answer holds the job id at once.
2. Poll: `GET /v1/video/generations/{{job_id}}` or `GET /v1/images/generations/{{job_id}}`. {_POLL_TEXT}
3. Download: `GET /v1/video/generations/{{job_id}}/content` (images: `/content/{{idx}}`), or use the result links.

## Instructions for AI agents
{rules}

## Links
- [Full documentation in Markdown]({base}/llms-full.txt)
- [OpenAPI schema]({base}/openapi.json)
- [Interactive reference]({base}/reference)
"""


def _type(s: dict) -> str:
    if "$ref" in s:
        return s["$ref"].rsplit("/", 1)[-1]
    if "enum" in s:
        return "enum(%s)" % ", ".join(map(str, s["enum"]))
    for key in ("anyOf", "oneOf"):
        if key in s:
            return " | ".join(_type(x) for x in s[key])
    if s.get("type") == "array":
        return "array of " + _type(s.get("items", {}))
    t = s.get("type", "any")
    return "|".join(t) if isinstance(t, list) else t


def _fields(schema: dict, comps: dict) -> list[str]:
    s = comps.get(_type(schema), schema) if "$ref" in schema else schema
    need = set(s.get("required", ()))
    return ["- `%s` (%s%s)%s" % (name, _type(p), ", required" if name in need else "",
                                ": " + p["description"].replace("\n", " ") if p.get("description") else "")
            for name, p in s.get("properties", {}).items()]


def llms_full(schema: dict, base: str) -> str:
    """`schema` is the public schema with the live hold written in (`openapi_docs.live`)."""
    comps = schema.get("components", {}).get("schemas", {})
    out = ["# Ghidorah API (full documentation)", "", "Base URL: " + base, "",
           schema["info"]["description"].strip(), ""]
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            out += ["## %s %s" % (method.upper(), path), "", op.get("description", "").strip(), ""]
            auth = op.get("security")
            out.append("Auth: " + ("API key" if auth else "none (keyless)"))
            params = [p for p in op.get("parameters", ()) if p.get("name")]
            if params:
                out += ["", "Parameters:"] + ["- `%s` (%s%s)%s" % (
                    p["name"], p["in"], ", required" if p.get("required") else "",
                    ": " + p["description"] if p.get("description") else "") for p in params]
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                out += ["", "Request body (JSON):"] + _fields(body, comps)
            out += ["", "Responses:"]
            for status, r in op.get("responses", {}).items():
                out.append("- %s: %s" % (status, r.get("description", "").replace("\n", " ")))
            out.append("")
    out += ["## Public error codes", "", "A job ends `failed` with one of these in `error_code`.", ""]
    out += ["- `%s`: %s Sending again helps: %s" % (code, text, helps)
            for code, (helps, text) in errors.PUBLIC_CODES.items()]
    return "\n".join(out) + "\n"
