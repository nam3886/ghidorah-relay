"""The error registry: what a code means to the machine, kept apart from the text a person reads.

Every code has one `Rule`; the retry, refund and HTTP decisions are read from it, never made at the call site.
The registry holds platform codes only. A provider registers its own codes with `register()` when its package
loads (the core never names a provider, I18).

A client never sees an internal code (owner's ruling 26/10). Every rule names its `public_code`: a request-time code
of a client-facing domain (`input.*`, `auth.*`, `model.*`, `quota.*`, `job.*`, `resource.*`) is shown as itself, with
its message; every other code (what a provider, an account or our own machinery did) is shown as one of the few
neutral codes of `PUBLIC_CODES`, with the rule's `public_text`, else the neutral code's own sentence. The job row, the
request log and the admin keep the internal code and message.

Wire shape (OpenAI style): `{"error": {"code", "message", "type", "param"}}`. There is no `retryable` and no
`refunded` in it: whether a job was refunded is the job's own `refunded` field in GET status.

`python -m relay_core.errors` regenerates docs/error-codes.md; a test fails when the file is stale.
"""

import re
import sys
from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass
from pathlib import Path

INTERNAL = "internal.unexpected"
ERROR_TYPES = frozenset({"invalid_request_error", "authentication_error", "permission_error", "not_found_error",
                         "rate_limit_error", "api_error"})
_CODE = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
_TYPE_BY_STATUS = {401: "authentication_error", 403: "permission_error", 404: "not_found_error",
                   429: "rate_limit_error"}
DOCS_PATH = Path(__file__).resolve().parents[4] / "docs" / "error-codes.md"

# The neutral codes a client sees for anything that is not a request-time code of its own: {code: (does sending the
# request again help, the sentence shown when the rule has no `public_text`)}. Small and stable: a client acts on
# them, so a new one is an API change.
CONTENT_REFUSED = "content_refused"
GENERATION_FAILED = "generation_failed"
TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
TIMEOUT = "timeout"
INVALID_INPUT = "invalid_input"
INTERNAL_ERROR = "internal_error"
PUBLIC_CODES: dict[str, tuple[str, str]] = {
    CONTENT_REFUSED: ("No: change the prompt or the images first.",
                      "The provider refused to make this. Sending the same request again will not help; change the "
                      "prompt or the images."),
    GENERATION_FAILED: ("It may.", "The generation failed and nothing was delivered. Sending the request again may "
                                   "work."),
    TEMPORARILY_UNAVAILABLE: ("Later.", "There is no capacity for this request right now. Sending the request again "
                                        "later may work."),
    TIMEOUT: ("It may.", "The result did not arrive in time. Sending the request again may work."),
    INVALID_INPUT: ("No: change the input first.",
                    "An input of the request could not be used. Sending the same request again will not help; change "
                    "the input."),
    INTERNAL_ERROR: ("It may.", "An error on our side ended the request. Sending the request again may work."),
}
# A request-time code of these domains speaks of the client's own request: it is shown as itself.
CLIENT_DOMAINS = frozenset({"input", "auth", "model", "quota", "job", "resource"})


@dataclass(frozen=True)
class Rule:
    """`terminal`: the job dies now. `refund`: the key charge is returned when the job ends `failed`.
    `counts_attempt`: the failure uses one of the job's attempts. `job_scoped=False`: the error happens before a
    job exists (or is about an operation, not the job), so there is nothing to refund. `message`: the internal text;
    a client reads it only for a code shown as itself. `public_code`: what a client sees instead of the code (the
    module text). `public_text`: for a code a job can end with (`ends_job`) that is not ours to hide, the sentence a
    customer reads: what happened and whether sending the request again can help, with no internal word (account,
    proxy, cookie...). `free_retries`: a requeue for this code uses no attempt until the job has been requeued for
    codes of this kind that many times (`jobs.fault_retries`, never reset); the requeue after that uses one, so
    `max_attempts` ends a job that keeps meeting the fault."""

    code: str
    http_status: int
    error_type: str
    refund: bool
    terminal: bool
    counts_attempt: bool
    job_scoped: bool
    message: str
    _: KW_ONLY
    public_code: str
    public_text: str = ""
    free_retries: int = 0


def ends_job(r: Rule) -> bool:
    """A code a job can end `failed` with: one that ends it now, or one that uses an attempt (at once, or after its
    free retries) so the last attempt ends it. Every failed job is refunded (`jobs.fail.fail_in_tx`)."""
    return r.job_scoped and (r.terminal or r.counts_attempt or r.free_retries > 0)


def hidden(r: Rule) -> bool:
    """Ours to fix, not the client's to act on: shown as `internal_error` with the generic sentence."""
    return r.public_code == INTERNAL_ERROR and not r.public_text


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
    if r.public_code == r.code:
        if r.job_scoped or r.code.split(".")[0] not in CLIENT_DOMAINS:
            bad.append("no public code: only a request-time code of %s is shown as itself; map it to one of %s"
                       % ("/".join(sorted(CLIENT_DOMAINS)), ", ".join(PUBLIC_CODES)))
        elif r.public_text:
            bad.append("a code shown as itself shows its message, not a public_text")
    elif r.public_code not in PUBLIC_CODES:
        bad.append("no public code: %r is not one of %s" % (r.public_code, ", ".join(PUBLIC_CODES)))
    if r.free_retries < 0 or (r.free_retries and (r.terminal or r.counts_attempt or not r.job_scoped)):
        bad.append("free_retries is for a requeued, job-scoped code that uses no attempt by itself")
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
    if INTERNAL not in rules or rules[INTERNAL].public_code != INTERNAL_ERROR:
        bad.append("%s: the fallback code must be registered and shown as %s" % (INTERNAL, INTERNAL_ERROR))
    return bad


def _r(code: str, status: int, kind: str, *, message: str, public: str = "", terminal: bool = False,
       refund: bool = False, counts: bool = False, scoped: bool = True, text: str = "") -> Rule:
    """A platform rule. `public` is its public code; left out, the code is shown as itself (a client code)."""
    return Rule(code, status, kind, refund, terminal, counts, scoped, message, public_code=public or code,
                public_text=text)


_BAD = "invalid_request_error"
_API = "api_error"
# Before a job exists: terminal for the caller, nothing to refund.
_pre = dict(terminal=True, scoped=False)
# Paid upstream and the caller got nothing: dead now, refunded.
_paid_dead = dict(terminal=True, refund=True)
_BUSY = dict(public=TEMPORARILY_UNAVAILABLE)

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
    _r("input.unsupported", 400, _BAD, message="this model does not take this kind of input yet", **_pre),
    _r("input.prompt_too_long", 400, _BAD, message="the prompt is too long", **_pre),
    _r("input.too_costly", 400, _BAD, message="this job costs more than can ever be served", **_pre),
    _r("input.frame_placeholder", 400, _BAD,
       message="a prompt frame has a missing, repeated or unknown placeholder", **_pre),
    # A request that works on an earlier job's output (`source_job_id`): the job must be a finished job of this API key
    # made by the same provider, and the model must work on a source at all.
    _r("input.bad_source", 400, _BAD,
       message="the source job is not a finished job of this API key that this model can work on", **_pre),
    _r("input.source_unsupported", 400, _BAD, message="this model does not work on a source job", **_pre),

    _r("auth.invalid_key", 401, "authentication_error", message="the API key is not valid", **_pre),
    _r("auth.key_disabled", 403, "permission_error", message="the API key is disabled", **_pre),
    _r("auth.model_forbidden", 403, "permission_error", message="the API key may not use this model", **_pre),
    # The admin paused the model for every key (`<p>.disabled_models`): refused at the door, nothing charged.
    _r("model.disabled", 403, "permission_error", message="this model is paused", **_pre),
    # The core prices a job from `<p>.prices` and `<p>.costs`: an option with no sale price or no known upstream cost is
    # refused at the door, nothing charged (no fallback price, no guessed cost).
    _r("model.unpriced", 403, "permission_error", message="this model option has no price yet", **_pre),
    _r("model.uncosted", 403, "permission_error", message="this model option cannot be ordered yet", **_pre),
    _r("auth.forbidden", 403, "permission_error", message="the request is not allowed", **_pre),
    # The admin door: a wrong login, and a request without a live admin session.
    _r("auth.invalid_login", 401, "authentication_error", message="the username or password is wrong", **_pre),
    _r("auth.invalid_session", 401, "authentication_error", message="the admin login is missing or has expired",
       **_pre),
    _r("auth.too_many_attempts", 429, "rate_limit_error", message="too many failed attempts; try again later", **_pre),
    # Fail closed: when the key's permissions cannot be read the request is refused.
    _r("auth.permission_unknown", 503, _API, message="the key's permissions could not be checked; try again", **_pre),

    _r("quota.key_exceeded", 429, "rate_limit_error", message="the API key's limit is reached", **_pre),
    _r("quota.too_many_queued", 429, "rate_limit_error", message="too many unfinished jobs for this API key", **_pre),

    # Our side: the job goes back to the queue, no attempt used, no refund.
    _r("pool.account_cannot_afford", 503, _API, message="no account has enough points for this job", **_BUSY),
    _r("pool.account_out_of_points", 503, _API, message="the account is out of points", **_BUSY),
    _r("pool.account_logged_out", 503, _API, message="the account is logged out", **_BUSY),
    _r("pool.account_throttled", 503, _API, message="the account is rate limited upstream", **_BUSY),
    _r("pool.account_region_blocked", 503, _API, message="the account's region is blocked upstream", **_BUSY),
    _r("pool.account_busy", 409, _BAD, message="the account is busy with another job", scoped=False, **_BUSY),
    _r("pool.proxy_failed", 503, _API, message="the account's proxy failed", **_BUSY),

    _r("account.dead_session", 502, _API, message="the account session died before the result arrived",
       **_paid_dead, public=GENERATION_FAILED,
       text="The render was lost before its result arrived. Sending the request again may work."),
    _r("account.wrong_conversation", 502, _API, message="the upstream conversation does not match this job",
       **_paid_dead, public=GENERATION_FAILED,
       text="The provider's answer did not match this job, so it was not delivered. Sending the request again may "
            "work."),
    _r("account.prompt_mismatch", 502, _API, message="the upstream order does not match this job", **_paid_dead,
       public=GENERATION_FAILED,
       text="The provider's order did not match this job, so it was not delivered. Sending the request again may "
            "work."),

    _r("render.deadline", 504, _API, message="the result did not arrive before the deadline", **_paid_dead,
       public=TIMEOUT,
       text="The result did not arrive in time. Sending the request again may work."),
    _r("render.download_failed", 502, _API, message="the result could not be downloaded", refund=True, counts=True,
       public=GENERATION_FAILED,
       text="The result could not be downloaded after several tries. Sending the request again may work."),
    _r("render.file_unreadable", 502, _API, message="the result file is unreadable", **_paid_dead,
       public=GENERATION_FAILED,
       text="The result file could not be read. Sending the request again may work."),
    _r("render.watermarked_only", 502, _API, message="only a watermarked result was delivered", **_paid_dead,
       public=GENERATION_FAILED,
       text="Only a watermarked result was made, so none was delivered. Sending the request again may work."),

    _r("render.unclear_send", 502, _API,
       message="the provider's answer to the order was unclear and cannot be checked, so the job ended",
       **_paid_dead, public=GENERATION_FAILED,
       text="The provider's answer to the order was unclear and could not be checked, so the job ended rather than "
            "risk a second order. Sending the request again may work."),

    # A pinned job (it works on an earlier job's output) runs only on the account that made that output: when that
    # account can no longer run it, the job ends at once with its refund instead of waiting.
    _r("source.account_unavailable", 409, _BAD,
       message="the account that made the source job is no longer available", **_paid_dead, public=INVALID_INPUT,
       text="The source job's result can no longer be worked on. Sending the request again with this source job will "
            "not help."),
    _r("source.account_exhausted", 429, "rate_limit_error",
       message="the account that made the source job has no quota left for now", **_paid_dead,
       public=TEMPORARILY_UNAVAILABLE,
       text="The capacity that can work on the source job is used up for now. Sending the request again later may "
            "work."),

    _r("queue.never_started", 503, _API, message="the job never started", **_paid_dead, **_BUSY,
       text="The job could not start in time. Sending the request again later may work."),
    _r("job.not_submitted", 409, _BAD,
       message="the job was never sent to the provider, so there is nothing to re-check", **_pre),
    _r("job.busy", 409, _BAD, message="the job is being checked right now", **_pre),
    _r("job.already_finished", 409, _BAD, message="the job already finished", **_pre),
    _r("resource.not_found", 404, "not_found_error", message="the resource does not exist", **_pre),
    # A provider with frames and no usable frame for the model: ours to fix, the client sees the generic error.
    _r("internal.frame_unusable", 500, _API, message="the prompt frame for this model is missing or unusable",
       public=INTERNAL_ERROR, **_pre),
    # The net, not a destination: an unclassified failure is treated as transient, then given up on and refunded.
    _r(INTERNAL, 500, _API, message="an internal error occurred", refund=True, counts=True, public=INTERNAL_ERROR,
       text="An error on our side ended the job. Sending the request again may work."),
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
    """(code, message) a client may see: a client code as itself with its message, any other as its neutral public
    code with the rule's customer sentence, else the neutral code's own. Never the internal code or message."""
    r = rule(code)
    if r.public_code == r.code:
        return r.code, r.message
    return r.public_code, r.public_text or PUBLIC_CODES[r.public_code][1]


def envelope(code: str, param: str | None = None) -> dict:
    """The error body. The text always comes from the rule; `param` names the offending request field. The type, as
    the HTTP status, is the real code's."""
    shown_code, message = shown(code)
    return {"error": {"code": shown_code, "message": message, "type": rule(code).error_type, "param": param}}


def render_docs(rules: Mapping[str, Rule] | None = None) -> str:
    """docs/error-codes.md, the internal reference. Provider codes appear once their package registers them (run with
    them imported)."""
    rules = RULES if rules is None else rules
    out = ["# Error codes", "",
           "Generated by `python -m relay_core.errors`; do not edit. Source: `packages/relay-core/src/relay_core/"
           "errors.py`.", "",
           "Body: `{\"error\": {\"code\", \"message\", \"type\", \"param\"}}`. A job's refund state is the `refunded` "
           "field of its status, not part of the error.", "",
           "The internal reference: a client never sees these codes nor their messages. It sees the **Public** code "
           "instead: a request-time code of a client domain as itself, any other as one of the neutral codes below, "
           "with the code's own customer sentence (not listed here) or the neutral one.", "",
           "| Public code | Sending again helps | Sentence |", "|---|---|---|"]
    out += ["| `%s` | %s | %s |" % (code, helps, text) for code, (helps, text) in PUBLIC_CODES.items()]
    domain = ""
    for code in sorted(rules):
        r = rules[code]
        if code.split(".")[0] != domain:
            domain = code.split(".")[0]
            out += ["", "## `%s.*`" % domain, "",
                    "| Code | Public | HTTP | Type | Terminal | Refund | Counts attempt | Free retries | Job "
                    "| Message |",
                    "|---|---|---|---|---|---|---|---|---|---|"]
        public = "itself" if r.public_code == code else "`%s`" % r.public_code
        flags = ["yes" if v else "no" for v in (r.terminal, r.refund, r.counts_attempt)]
        out.append("| `%s` | %s | %d | %s | %s | %s | %s | %d | %s | %s |" % (
            code, public, r.http_status, r.error_type, *flags, r.free_retries, "yes" if r.job_scoped else "no",
            r.message))
    return "\n".join(out) + "\n"


def write_docs(path: Path = DOCS_PATH) -> None:
    path.write_text(render_docs(), encoding="utf-8")


if __name__ == "__main__":
    # Run as a script this module is `__main__`, a second copy: a provider registers its codes on the importable
    # `relay_core.errors`, so the docs must be rendered from that one.
    import importlib

    registry = importlib.import_module("relay_core.errors")
    for spec in sorted((DOCS_PATH.parents[1] / "packages").glob("provider-*/src/relay_*/spec.py")):
        importlib.import_module("%s.spec" % spec.parent.name)
    registry.write_docs()
    print("wrote %s" % DOCS_PATH, file=sys.stderr)
