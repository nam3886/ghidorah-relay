"""The one redactor (I24): everything stored or logged that may carry a secret passes through here.

Three layers, because a secret turns up in three shapes:
1. by field name, for dicts: the value of a sensitive-looking key is replaced whole;
2. structural: an object that is a cookie (a Cookie-Editor array item: its `name` is a known cookie name, or it also
   has a `domain`) has its `value` replaced, though neither key looks sensitive;
3. by pattern, for free text: secret-shaped substrings are found wherever they sit, even mid-sentence (an upstream
   error message that echoes a `Set-Cookie`).

The core knows generic shapes, plus the common session-cookie names of the upstreams we have met (a fail-safe: a
cookie missed there is a leaked session). A provider adds its own with `register_pattern` / `register_cookie_names`.
OpenAI token counts (`total_tokens`, ...) contain "token" but are not secrets.
"""

import logging
import re
from typing import Any

REDACTED = "[redacted]"

# A quoted string, or the rest of the line.
_VALUE = r"""(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\r\n]+)"""
_HOST = r"(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9-]+(?:\.[a-z0-9-]+)+|\[[0-9a-f:]+\])"
_SIGNED = "oh|oe|x-signature|signature|sig|x-amz-signature|x-amz-credential|x-amz-security-token|x-goog-signature"

# Every quantifier that can meet attacker-sized input is bounded or starts where the match must: a pattern that
# backtracks over a long run from every start position is quadratic (20k url-safe base64 took 20 s).
# Order matters: a labelled secret is replaced before the generic shapes, so the label survives; the long-token
# catch-all runs last. Each entry is (name, pattern, replacement).
PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = tuple((n, re.compile(p), r) for n, p, r in (
    ("set_cookie", r"(?i)((?<![\w-])set-cookie[\"']?\s*[:=]\s*)" + _VALUE, r"\1" + REDACTED),
    ("cookie_header", r"(?i)((?<![\w-])cookie[\"']?\s*[:=]\s*)" + _VALUE, r"\1" + REDACTED),
    ("authorization", r"(?i)(\bauthorization[\"']?\s*[:=]\s*[\"']?)(?:[a-z]+\s+)?[^\s\"',;}]+", r"\1" + REDACTED),
    ("bearer", r"(?i)\bbearer\s+[a-z0-9._~+/=-]+", REDACTED),
    ("url_userinfo", r"(?i)\b([a-z][a-z0-9+.-]{0,31}://)[^\s/@]+@", r"\1" + REDACTED + "@"),
    ("proxy_ip_port_user_pass", r"(?i)(?<![\w.:])(" + _HOST + r":\d{2,5}):[^\s:]+:\S+", r"\1:" + REDACTED),
    ("user_pass_at_host_port",
     r"(?i)(?<![\w.:/%@-])(?!mailto:)[^\s:@/]{1,128}:[^\s/]{1,256}@(?=" + _HOST + r"(?::\d{2,5})?\b)", REDACTED + "@"),
    ("signed_query", r"(?i)([?&;](?:" + _SIGNED + r")=)[^&\s\"'#]+", r"\1" + REDACTED),
    ("share_token_path", r"(?<=/m/)[A-Za-z0-9_-]{16,}", REDACTED),
    ("api_key_rk", r"\brk-[A-Za-z0-9_-]{16,}", REDACTED),
    ("secret_label",
     r"(?i)((?:token|password|passwd|secret|api[_-]?key|(?:secret|access|private|signing|auth)[_-]?key)"
     r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+", r"\1" + REDACTED),
    ("cookie_pair",
     r"(?i)\b(sessionid(?:_ss)?|sid_guard|sid_tt|uid_tt|sid_ucp_v1|ssid_ucp_v1|odin_tt|ttwid|__secure-[\w-]+)"
     r"([\"']?\s*[:=]\s*[\"']?)[^\s;,'\"}\]]+", r"\1\2" + REDACTED),
))

# A cookie object written out as text (a Cookie-Editor array in an error message or a dump): `{...}`, no nesting.
_COOKIE_OBJECT = re.compile(r"\{[^{}]{0,4000}\}")
_OBJECT_NAME = re.compile(r"""["']name["']\s*:\s*["']([^"']*)["']""")
_OBJECT_VALUE = re.compile(r"""(["']value["']\s*:\s*)(["'])[^"']*\2""")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}")

_COOKIE_NAMES = {"sessionid", "sessionid_ss", "sid_guard", "sid_tt", "uid_tt", "sid_ucp_v1", "ssid_ucp_v1",
                 "odin_tt", "ttwid"}
_SENSITIVE_NAMES = ("sessionid", "sid_guard", "sid_tt", "uid_tt", "sid_ucp", "ssid_ucp", "odin_tt", "ttwid", "cookie",
                    "authorization", "api_key", "apikey", "api-key", "token", "password", "passwd", "secret", "proxy",
                    "credential", "signature", "private_key", "access_key")
_SAFE_NAMES = frozenset({"id_key", "idempotency_key", "object_key", "media_key", "primary_key", "sort_key",
                         "max_tokens", "max_completion_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
                         "input_tokens", "output_tokens", "reasoning_tokens", "cached_tokens", "token_count"})

_provider_patterns: dict[str, tuple[re.Pattern[str], str]] = {}


def register_pattern(name: str, pattern: str, replacement: str = REDACTED) -> None:
    """A provider's own secret shape (for text). Registering a name again replaces it."""
    _provider_patterns[name] = (re.compile(pattern), replacement)


def register_cookie_names(*names: str) -> None:
    """A provider's own cookie names: structural cookie objects with these names and dict keys of these names are
    redacted."""
    _COOKIE_NAMES.update(n.lower() for n in names)


def _sensitive_name(name: str) -> bool:
    low = str(name).lower()
    if low in _SAFE_NAMES:
        return False
    return (low in _COOKIE_NAMES or low.endswith("key") or any(needle in low for needle in _SENSITIVE_NAMES))


def _is_cookie_object(d: dict) -> bool:
    name = d.get("name")
    if not isinstance(name, str) or "value" not in d:
        return False
    return _sensitive_name(name) or name.lower().startswith("__secure-") or "domain" in d


def _redact_cookie_object(match: re.Match[str]) -> str:
    text = match.group(0)
    name = _OBJECT_NAME.search(text)
    if name is None or not (_sensitive_name(name.group(1)) or name.group(1).lower().startswith("__secure-")
                            or re.search(r"""["']domain["']\s*:""", text)):
        return text
    return _OBJECT_VALUE.sub(lambda m: m.group(1) + m.group(2) + REDACTED + m.group(2), text)


def redact_text(text: str | None) -> str:
    out = text or ""
    out = _COOKIE_OBJECT.sub(_redact_cookie_object, out)
    for _, pattern, replacement in PATTERNS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _provider_patterns.values():
        out = pattern.sub(replacement, out)
    return _LONG_TOKEN.sub(REDACTED, out)


def redact(value: Any) -> Any:
    """Redact recursively: dicts by field name and structure, strings by pattern, lists and tuples item by item."""
    if isinstance(value, dict):
        cookie = _is_cookie_object(value)
        return {k: REDACTED if (cookie and k == "value") or _sensitive_name(k) else redact(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


LOG_VALUE_LIMIT = 2000


def trim_log_value(value: Any, limit: int = LOG_VALUE_LIMIT) -> Any:
    """Replace every string longer than `limit` (an inline base64 image, a whole page) with `[omitted len=N]`,
    recursively. For logs and events: it keeps them readable and bounds what `redact` has to scan, and a secret
    inside a dropped string is dropped with it."""
    if isinstance(value, dict):
        return {trim_log_value(k, limit): trim_log_value(v, limit) for k, v in value.items()}  # a key is text too
    if isinstance(value, (list, tuple)):
        return [trim_log_value(v, limit) for v in value]
    if isinstance(value, str) and len(value) > limit:
        return "[omitted len=%d]" % len(value)
    return value


class LogRedactor(logging.Filter):
    """On a log handler: the message goes through `redact_log` and a traceback through `redact_text` (never cut,
    so it stays whole). Installed on every handler of the api and worker processes (`install_log_redaction`)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:                      # a broken format string: leave the record to the handler
            return True
        record.msg, record.args = redact_log(message), None
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


def install_log_redaction(*logger_names: str) -> None:
    """Put a `LogRedactor` on every handler of the root logger and of the named loggers, once."""
    for name in (None, *logger_names):
        for handler in logging.getLogger(name).handlers:
            if not any(isinstance(f, LogRedactor) for f in handler.filters):
                handler.addFilter(LogRedactor())


def redact_log(value: Any) -> Any:
    """What a log line or event stores: trimmed first, then redacted."""
    return redact(trim_log_value(value))


# The query of a URL: from its `?` to the first character a URL cannot hold unquoted (or a fragment). Bounded by the
# trim before it (no string over LOG_VALUE_LIMIT reaches here), and linear: one pass, no nested quantifier.
_URL_QUERY = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]{0,31}://[^\s\"'<>?#]*\?)([^\s\"'<>#]*)")
_QUERY_PAIR = re.compile(r"([^&;=]*)(=?)([^&;]*)")


def _blank_query(match: re.Match[str]) -> str:
    def pair(m: re.Match[str]) -> str:
        name, eq, value = m.groups()
        if not (name or value):
            return m.group(0)
        return name + "=" + REDACTED if eq else REDACTED
    return match.group(1) + _QUERY_PAIR.sub(pair, match.group(2))


def _blank_urls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _blank_urls(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_blank_urls(v) for v in value]
    if isinstance(value, str):
        return _URL_QUERY.sub(_blank_query, value)
    return value


def redact_shown(value: Any) -> Any:
    """What the admin is shown of a stored request, answer or provider state: `redact_log`, then the value of every
    query parameter of every URL in any string at any depth (JSON held in a string included) is replaced, its name
    kept so the URL still reads. A signed result URL loses its signature: the admin has its own media links."""
    return _blank_urls(redact_log(value))
