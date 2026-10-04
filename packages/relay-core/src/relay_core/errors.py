"""The error registry (contracts §11, D19): what a code means to the machine, kept apart from the text a person reads.

Every code has one `Rule`; the retry, refund and HTTP decisions are read from it, never made at the call site.
The registry holds platform codes only. A provider registers its own codes with `register()` when its package
loads (the core never names a provider, I18).

Wire shape (D19, OpenAI style): `{"error": {"code", "message", "type", "param"}}`. There is no `retryable` and no
`refunded` in it: whether a job was refunded is the job's own `refunded` field in GET status.

`python -m relay_core.errors` regenerates docs/error-codes.md; a test fails when the file is stale.
"""

import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

INTERNAL = "internal.unexpected"
ERROR_TYPES = frozenset({"invalid_request_error", "authentication_error", "permission_error", "not_found_error",
                         "rate_limit_error", "api_error"})
_CODE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_TYPE_BY_STATUS = {401: "authentication_error", 403: "permission_error", 404: "not_found_error",
                   429: "rate_limit_error"}
DOCS_PATH = Path(__file__).resolve().parents[4] / "docs" / "error-codes.md"


@dataclass(frozen=True)
class Rule:
    """`terminal`: the job dies now. `refund`: the key charge is returned when the job ends `failed`.
    `counts_attempt`: the failure uses one of the job's attempts. `job_scoped=False`: the error happens before a
    job exists (or is about an operation, not the job), so there is nothing to refund. `public=False`: the client
    sees `internal.unexpected` with `hidden_message`, because the detail is ours to fix and not theirs to act on."""

    code: str
    http_status: int
    error_type: str
    refund: bool
    terminal: bool
    counts_attempt: bool
    public: bool
    job_scoped: bool
    message: str
    hidden_message: str = ""


def _problems(key: str, r: Rule) -> list[str]:
    bad = []
    if key != r.code:
        bad.append("registered under %r" % key)
    if not _CODE.fullmatch(r.code):
        bad.append("a code is `domain.reason` in lower case")
    if not 400 <= r.http_status <= 599:
        bad.append("http_status %r is not an error status" % r.http_status)
    if r.error_type not in ERROR_TYPES:
        bad.append("unknown error_type %r" % r.error_type)
    elif r.error_type != (_TYPE_BY_STATUS.get(r.http_status) or ("api_error" if r.http_status >= 500
                                                                  else "invalid_request_error")):
        bad.append("error_type %s does not match status %d" % (r.error_type, r.http_status))
    if not r.message:
        bad.append("no message")
    if r.public == bool(r.hidden_message):
        bad.append("hidden_message is required exactly when public is False")
    if r.terminal and r.counts_attempt:
        bad.append("terminal and counts_attempt: a job that dies now has no attempt left to count")
    if not r.job_scoped:
        if r.refund:
            bad.append("no job exists, so there is nothing to refund")
    elif r.refund != (r.terminal or r.counts_attempt):
        bad.append("a job ends failed exactly when it is refunded (a requeued job must not be)")
    if r.code.startswith("pool.") and (r.terminal or r.refund or r.counts_attempt):
        bad.append("pool.* is our fault: the job lives on, uncharged for an attempt and not refunded")
    return bad


def violations(rules: Mapping[str, Rule]) -> list[str]:
    """Every registry invariant (I21, I26) as a list of readable problems; empty means the registry is sound."""
    bad = ["%s: %s" % (key, why) for key, r in rules.items() for why in _problems(key, r)]
    if INTERNAL not in rules or not rules[INTERNAL].public:
        bad.append("%s: the fallback code must be registered and public" % INTERNAL)
    return bad


def _r(code: str, status: int, kind: str, *, message: str, terminal: bool = False, refund: bool = False,
       counts: bool = False, scoped: bool = True) -> Rule:
    return Rule(code, status, kind, refund, terminal, counts, True, scoped, message)


_BAD = "invalid_request_error"
_API = "api_error"
# Before a job exists: terminal for the caller, nothing to refund.
_pre = dict(terminal=True, scoped=False)
# Paid upstream and the caller got nothing: dead now, refunded.
_paid_dead = dict(terminal=True, refund=True)

RULES: dict[str, Rule] = {r.code: r for r in (
    _r("input.empty_prompt", 400, _BAD, message="the prompt is empty", **_pre),
    _r("input.bad_duration", 400, _BAD, message="the duration is not supported by this model", **_pre),
    _r("input.bad_ratio", 400, _BAD, message="the aspect ratio is not supported by this model", **_pre),
    _r("input.bad_model", 400, _BAD, message="the model does not exist or is not available", **_pre),
    _r("input.bad_style", 400, _BAD, message="the style is not supported", **_pre),
    _r("input.image_unreadable", 400, _BAD, message="an input image could not be read", **_pre),
    _r("input.prompt_ratio_conflict", 400, _BAD,
       message="the prompt asks for a different aspect ratio than the request", **_pre),
    _r("input.prompt_duration_conflict", 400, _BAD,
       message="the prompt asks for a different duration than the request", **_pre),
    _r("input.invalid", 400, _BAD, message="the request is not valid", **_pre),
    _r("input.idempotency_conflict", 409, _BAD,
       message="this idempotency key was already used with a different request", **_pre),
    _r("input.too_many_images", 400, _BAD, message="too many input images", **_pre),
    _r("input.prompt_too_long", 400, _BAD, message="the prompt is too long", **_pre),
    _r("input.template_not_found", 400, _BAD, message="the template does not exist", **_pre),

    _r("auth.invalid_key", 401, "authentication_error", message="the API key is not valid", **_pre),
    _r("auth.key_disabled", 403, "permission_error", message="the API key is disabled", **_pre),
    _r("auth.model_forbidden", 403, "permission_error", message="the API key may not use this model", **_pre),
    _r("auth.forbidden", 403, "permission_error", message="the request is not allowed", **_pre),
    # The admin door (D26): a wrong login, and a request without a live admin session.
    _r("auth.invalid_login", 401, "authentication_error", message="the username or password is wrong", **_pre),
    _r("auth.invalid_session", 401, "authentication_error", message="the admin session is missing or has expired",
       **_pre),
    _r("auth.too_many_attempts", 429, "rate_limit_error", message="too many failed attempts; try again later", **_pre),
    # Fail closed: when the key's permissions cannot be read the request is refused.
    _r("auth.permission_unknown", 503, _API, message="the key's permissions could not be checked; try again", **_pre),

    _r("quota.key_exceeded", 429, "rate_limit_error", message="the API key's limit is reached", **_pre),
    _r("quota.too_many_queued", 429, "rate_limit_error", message="too many unfinished jobs for this API key", **_pre),

    # Our side: the job goes back to the queue, no attempt used, no refund.
    _r("pool.no_account", 503, _API, message="no account is available right now"),
    _r("pool.account_cannot_afford", 503, _API, message="no account has enough points for this job"),
    _r("pool.account_out_of_points", 503, _API, message="the account is out of points"),
    _r("pool.account_logged_out", 503, _API, message="the account is logged out"),
    _r("pool.account_throttled", 503, _API, message="the account is rate limited upstream"),
    _r("pool.account_region_blocked", 503, _API, message="the account's region is blocked upstream"),
    _r("pool.account_busy", 409, _BAD, message="the account is busy with another job", scoped=False),
    _r("pool.proxy_failed", 503, _API, message="the account's proxy failed"),

    _r("account.dead_session", 502, _API, message="the account session died before the result arrived",
       **_paid_dead),
    _r("account.wrong_conversation", 502, _API, message="the upstream conversation does not match this job",
       **_paid_dead),
    _r("account.prompt_mismatch", 502, _API, message="the upstream order does not match this job", **_paid_dead),

    _r("render.deadline", 504, _API, message="the result did not arrive before the deadline", **_paid_dead),
    _r("render.download_failed", 502, _API, message="the result could not be downloaded", refund=True, counts=True),
    _r("render.file_unreadable", 502, _API, message="the result file is unreadable", **_paid_dead),
    _r("render.watermarked_only", 502, _API, message="only a watermarked result was delivered", **_paid_dead),

    _r("queue.never_started", 503, _API, message="the job never started", **_paid_dead),
    _r("job.not_submitted", 409, _BAD, message="the job was never sent upstream, so there is nothing to re-check",
       **_pre),
    _r("job.busy", 409, _BAD, message="the job is being checked right now", **_pre),
    _r("job.already_finished", 409, _BAD, message="the job already finished", **_pre),
    _r("resource.not_found", 404, "not_found_error", message="the resource does not exist", **_pre),
    # The net, not a destination: an unclassified failure is treated as transient, then given up on and refunded.
    _r(INTERNAL, 500, _API, message="an internal error occurred", refund=True, counts=True),
)}


def register(*rules: Rule) -> None:
    """Add a provider's codes. A duplicate code or any broken invariant raises, so a bad rule fails at import."""
    clash = [r.code for r in rules if r.code in RULES]
    if clash:
        raise ValueError("error codes already registered: %s" % ", ".join(clash))
    merged = {**RULES, **{r.code: r for r in rules}}
    bad = violations(merged)
    if bad:
        raise ValueError("; ".join(bad))
    RULES.update(merged)


def rule(code: str) -> Rule:
    """The rule of a code; a code nobody registered is `internal.unexpected`."""
    return RULES.get(code) or RULES[INTERNAL]


def http_status(code: str) -> int:
    """From the real code, not the one shown to the client."""
    return rule(code).http_status


def shown(code: str) -> tuple[str, str]:
    """(code, message) the client may see. A non-public code is replaced as a pair: hiding the code but keeping its
    text would still tell the client what broke."""
    r = rule(code)
    return (r.code, r.message) if r.public else (INTERNAL, r.hidden_message)


def envelope(code: str, param: str | None = None) -> dict:
    """The error body. The text always comes from the rule; `param` names the offending request field."""
    shown_code, message = shown(code)
    return {"error": {"code": shown_code, "message": message, "type": rule(shown_code).error_type, "param": param}}


def render_docs(rules: Mapping[str, Rule] | None = None) -> str:
    """docs/error-codes.md. Provider codes appear once their package registers them (run with them imported)."""
    rules = RULES if rules is None else rules
    out = ["# Error codes", "",
           "Generated by `python -m relay_core.errors`; do not edit. Source: `packages/relay-core/src/relay_core/"
           "errors.py` and contracts §11.", "",
           "Body: `{\"error\": {\"code\", \"message\", \"type\", \"param\"}}`. A job's refund state is the `refunded` "
           "field of its status, not part of the error.", ""]
    domain = ""
    for code in sorted(rules):
        r = rules[code]
        if code.split(".")[0] != domain:
            domain = code.split(".")[0]
            out += ["", "## `%s.*`" % domain, "",
                    "| Code | HTTP | Type | Terminal | Refund | Counts attempt | Job | Message |",
                    "|---|---|---|---|---|---|---|---|"]
        text = r.message if r.public else "(shown as `%s`: %s) %s" % (INTERNAL, r.hidden_message, r.message)
        out.append("| `%s` | %d | %s | %s | %s | %s | %s | %s |" % (
            code, r.http_status, r.error_type, *("yes" if v else "no" for v in (
                r.terminal, r.refund, r.counts_attempt, r.job_scoped)), text))
    return "\n".join(out) + "\n"


def write_docs(path: Path = DOCS_PATH) -> None:
    path.write_text(render_docs(), encoding="utf-8")


if __name__ == "__main__":
    write_docs()
    print("wrote %s" % DOCS_PATH, file=sys.stderr)
