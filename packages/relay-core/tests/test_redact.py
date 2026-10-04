"""The one redactor (contracts §13, I24): a planted secret per pattern, each with a "pattern removed" mutation.

A pattern is switched off by putting a never-matching `(?!)` at its start. Every planted secret is short and has
one shape, so only the pattern under test can catch it (the long-token catch-all needs 40 characters).
"""

import json
import time

import pytest

from relay_core import redact as redact_module
from relay_core.redact import REDACTED, redact, redact_log, redact_text, register_cookie_names, register_pattern
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/redact.py"


def gone(text: str, *secrets: str) -> str:
    out = redact_text(text)
    for secret in secrets:
        assert secret not in out, "%r survived in %r" % (secret, out)
    assert REDACTED in out
    return out


@mutation(SRC, r'(?i)((?<![\w-])set-cookie', r'(?i)(?!)((?<![\w-])set-cookie', expect="'SECRETVAL1' survived in")
def test_set_cookie_header():
    gone("upstream said Set-Cookie: foo=SECRETVAL1; Path=/; HttpOnly", "SECRETVAL1")
    gone('{"Set-Cookie": "foo=SECRETVAL1; Path=/", "status": 200}', "SECRETVAL1")


@mutation(SRC, r'(?i)((?<![\w-])cookie', r'(?i)(?!)((?<![\w-])cookie', expect="'SECRETVAL2' survived in")
def test_cookie_header():
    gone("sent Cookie: foo=SECRETVAL2; bar=SECRETVAL3", "SECRETVAL2", "SECRETVAL3")
    out = gone('headers {"Cookie": "a=SECRETVAL4", "Accept": "*/*"}', "SECRETVAL4")
    assert '"Accept": "*/*"' in out                    # a quoted value ends at its quote, not the line


@mutation(SRC, r'(?i)(\bauthorization', r'(?i)(?!)(\bauthorization', expect="'c2VjcmV0cGFzcw' survived in")
def test_authorization_header():
    gone("Authorization: Basic c2VjcmV0cGFzcw==", "c2VjcmV0cGFzcw")
    out = gone('{"Authorization": "Digestish SECRETAUTH5", "x": 1}', "SECRETAUTH5")
    assert '"x": 1' in out


@mutation(SRC, r'(?i)\bbearer', r'(?i)(?!)\bbearer', expect="'SECRETBEARER9' survived in")
def test_bearer_token_without_a_header_label():
    gone("got 401 for Bearer SECRETBEARER9 on retry", "SECRETBEARER9")


@mutation(SRC, r'(?i)\b([a-z][a-z0-9+.-]{0,31}://)', r'(?i)(?!)\b([a-z][a-z0-9+.-]{0,31}://)',
           expect="'bob' survived in 'connect socks5")
def test_scheme_user_pass_at_host():
    out = gone("connect socks5://bob:hunter2@10.1.2.3:1080 failed", "bob", "hunter2")
    assert "socks5://" in out and "10.1.2.3:1080" in out         # the endpoint stays debuggable
    gone("GET https://SECRETUSER6@files.example.com/a", "SECRETUSER6")


@mutation(SRC, r'(?i)(?<![\w.:])(" + _HOST', r'(?i)(?!)(?<![\w.:])(" + _HOST', expect="'h51jttwsx1ie' survived in")
def test_ip_port_user_pass():
    out = gone("proxy 103.104.122.48:3129:h51jttwsx1ie:qry1d70u318ff1k failed", "h51jttwsx1ie", "qry1d70u318ff1k")
    assert "103.104.122.48:3129" in out
    gone("via px1.example.net:8000:alice:p:a:ss", "alice", "p:a:ss")


@mutation(SRC, r'(?i)(?<![\w.:/%@-])', r'(?i)(?!)(?<![\w.:/%@-])', expect="'user:pass' survived in")
def test_user_pass_at_host_port():
    out = gone("using user:pass@host.example.com:8080 now", "user:pass")
    assert "host.example.com:8080" in out
    gone("proxy bob:hunter2@10.0.0.9:8080 refused", "bob:hunter2")
    gone("tunnel u:SECRET7@[2001:db8::1]:1080", "SECRET7")


def test_user_pass_pattern_leaves_ordinary_text_alone():
    for text in ("note:see@home", "mail me at alice@example.com:", "ratio 16:9 at 10:30:45", "id a:b", "x@y.z:80"):
        assert redact_text(text) == text, text


@mutation(SRC, r'(?i)([?&;]', r'(?i)(?!)([?&;]', expect="'abc123def' survived in")
def test_signed_query_values():
    out = gone("https://cdn.example.com/v.mp4?oh=abc123def&oe=69D7B3C0&x-signature=Zm9vYmFy&w=720",
               "abc123def", "69D7B3C0", "Zm9vYmFy")
    assert "w=720" in out and "oh=" in out and "cdn.example.com/v.mp4" in out
    gone("<a href='/v?x=1&amp;oh=SECRETSIG8'>", "SECRETSIG8")


@mutation(SRC, r'(?<=/m/)', r'(?!)(?<=/m/)', expect="'AbCdEfGhIjKlMnOpQrStUv' survived in")
def test_share_token_in_path():
    out = gone("GET /m/AbCdEfGhIjKlMnOpQrStUv HTTP/1.1", "AbCdEfGhIjKlMnOpQrStUv")
    assert "/m/" in out
    assert redact_text("GET /m/short") == "GET /m/short"


@mutation(SRC, r'\brk-', r'(?!)\brk-', expect="'AbCdEfGhIjKlMnOpQrStUvWx' survived in")
def test_api_key():
    gone("key rk-AbCdEfGhIjKlMnOpQrStUvWx used", "AbCdEfGhIjKlMnOpQrStUvWx")


@mutation(SRC, r'(?i)((?:token', r'(?i)(?!)((?:token', expect="'SECRETTOK10' survived in")
def test_secret_labels():
    gone('{"access_token": "SECRETTOK10", "n": 1}', "SECRETTOK10")
    gone("password=hunter2&user=bob", "hunter2")
    gone("x-api-key: SECRETKEY11", "SECRETKEY11")
    gone("share_token=SECRETSHARE12", "SECRETSHARE12")
    assert redact_text("total_tokens=7 token_count: 3") == "total_tokens=7 token_count: 3"


@mutation(SRC, r'(?i)\b(sessionid(?:_ss)?', r'(?i)(?!)\b(sessionid(?:_ss)?', expect="'a2e3SECRET13' survived in")
def test_known_cookie_pairs():
    out = gone("jar had sid_guard=a2e3SECRET13; other=x", "a2e3SECRET13")
    assert "sid_guard=" in out and "other=x" in out
    gone("x-amz-meta-sessionid=SECRET14", "SECRET14")


@mutation(SRC, r're.compile(r"\b[A-Za-z0-9+/_-]{40,}', r're.compile(r"(?!)\b[A-Za-z0-9+/_-]{40,}',
           expect="'Zk9wQ3RyZXRTZWNyZXRPZkFib3V0Rm9ydHlDaGFyc09yTW9yZQ' survived in")
def test_long_token_catch_all():
    secret = "Zk9wQ3RyZXRTZWNyZXRPZkFib3V0Rm9ydHlDaGFyc09yTW9yZQ"
    gone("opaque value %s here" % secret, secret)
    sentence = "a normal sentence of about forty characters or so"
    assert redact_text(sentence) == sentence


# ---- structural, field-name and provider layers ---------------------------------------------------------------------
@mutation(SRC, 'if (cookie and k == "value")', 'if (False and k == "value")', expect="assert 'SECRETVAL15' not in")
def test_cookie_editor_array_without_any_sensitive_key():
    jar = [{"domain": ".example.com", "name": "sessionid", "value": "SECRETVAL15", "path": "/", "httpOnly": True},
           {"domain": ".example.com", "name": "theme", "value": "SECRETVAL16", "path": "/"},
           {"name": "ttwid", "value": "SECRETVAL17"}]
    out = json.dumps(redact(jar))
    for secret in ("SECRETVAL15", "SECRETVAL16", "SECRETVAL17"):
        assert secret not in out
    assert "sessionid" in out and ".example.com" in out        # the shape stays readable
    assert redact({"name": "Alice", "value": "fine"}) == {"name": "Alice", "value": "fine"}


@mutation(SRC, "or any(needle in low for needle in _SENSITIVE_NAMES)", "or False", expect="other=x' == '[redacted]'")
def test_field_names_v4_samples():
    secret_cookie = "sid_guard=6bf70a8161121010e391a11e6cc47c42abcdef0123456789; other=x"
    d = redact({"cookie": secret_cookie, "x-amz-meta-sessionid": "SECRET", "prompt": "sóng biển",
                "proxy": "1.2.3.4:8000:u:p", "nested": {"authorization": "Bearer xyz", "raw_proxy": "5.6.7.8:8000:u:p"},
                "key_id": "k1", "api_key": "k", "idempotency": "ok"})
    assert d["cookie"] == REDACTED and d["x-amz-meta-sessionid"] == REDACTED
    assert d["prompt"] == "sóng biển" and d["key_id"] == "k1" and d["idempotency"] == "ok"
    assert d["proxy"] == REDACTED and d["nested"]["authorization"] == REDACTED
    assert d["nested"]["raw_proxy"] == REDACTED and d["api_key"] == REDACTED


@mutation(SRC, "if low in _SAFE_NAMES:", "if False:", expect="assert ('[redacted]' == 1024)")
def test_openai_token_counts_are_not_secrets():
    safe = redact({"max_tokens": 1024, "total_tokens": 7, "prompt_tokens": 3, "completion_tokens": 4,
                   "access_token": "abc", "api_key": "k"})
    assert safe["max_tokens"] == 1024 and safe["total_tokens"] == 7
    assert safe["prompt_tokens"] == 3 and safe["completion_tokens"] == 4
    assert safe["access_token"] == REDACTED and safe["api_key"] == REDACTED


def test_v4_free_text_samples():
    secret_cookie = "sid_guard=6bf70a8161121010e391a11e6cc47c42abcdef0123456789; other=x"
    secret_bearer = "Authorization: Bearer dk-qRUlBoILoTbzNL5Xh77gJmNCrnzbAu2krCPbt1Gabcd"
    secret_proxy = "proxy 103.104.122.48:3129:h51jttwsx1ie:qry1d70u318ff1k failed"
    setcookie = "Upstream said Set-Cookie: sessionid=deadbeefdeadbeefdeadbeef0123456789abcd; Path=/"
    for text in (secret_cookie, secret_bearer, secret_proxy, setcookie):
        out = redact_text(text)
        for secret in ("6bf70a8161121010e391a11e6cc47c42", "dk-qRUlBoILoTbzNL5Xh77gJmNCrnzbAu2k",
                       "h51jttwsx1ie", "qry1d70u318ff1k", "deadbeefdeadbeefdeadbeef0123456789abcd"):
            assert secret not in out, (secret, out)
    assert "103.104.122.48:3129" in redact_text(secret_proxy)


def test_nested_structures_and_non_strings():
    out = redact({"events": [{"text": "Cookie: a=SECRET18"}, ("Bearer SECRET19",)], "n": 5, "ok": None, "flag": True})
    assert "SECRET18" not in json.dumps(out) and "SECRET19" not in json.dumps(out)
    assert out["n"] == 5 and out["ok"] is None and out["flag"] is True
    assert redact(None) is None and redact_text(None) == ""


def test_redaction_is_idempotent_and_leaves_normal_text():
    text = "Cookie: a=SECRET20; GET /m/AbCdEfGhIjKlMnOpQrStUv oh=1 https://u:p@h.example/x?oh=zz"
    once = redact_text(text)
    assert redact_text(once) == once
    plain = "a dog on a beach at 5pm, 16:9, duration 30s, job 9f0c2c58-1d2e-4a5b-8c7d-0123456789ab, rk-short"
    assert redact_text(plain) == plain


@mutation(SRC, "for pattern, replacement in _provider_patterns.values():", "for pattern, replacement in ():",
           expect="assert 'SECRET21' not in")
def test_provider_pattern_and_cookie_name_hooks(monkeypatch):
    monkeypatch.setattr(redact_module, "_provider_patterns", {})
    monkeypatch.setattr(redact_module, "_COOKIE_NAMES", set(redact_module._COOKIE_NAMES))
    text = "state widget_session=\"SECRET21\" end"
    assert "SECRET21" in redact_text(text)                       # unknown to the core
    register_pattern("widget_session", r'(?<=widget_session=")[^"]+')
    assert "SECRET21" not in redact_text(text)
    register_pattern("widget_session", r'(?<=widget_session=")[^"]+', "<x>")     # same name replaces, no duplicate
    assert redact_text(text) == 'state widget_session="<x>" end'
    assert redact({"name": "widget_session", "value": "SECRET22"})["value"] == "SECRET22"
    register_cookie_names("Widget_Session")
    assert redact({"name": "widget_session", "value": "SECRET22"})["value"] == REDACTED
    assert redact({"widget_session": "SECRET23"})["widget_session"] == REDACTED


@pytest.mark.parametrize("planted", [
    "Set-Cookie: foo=SECRET24", "Cookie: foo=SECRET24", "Authorization: Basic SECRET24",
    "Bearer SECRET24", "https://u:SECRET24@h.example/", "9.9.9.9:80:u:SECRET24", "u:SECRET24@h.example:80",
    "/x?oh=SECRET24", "/m/SECRET24SECRET24SECRET24", "rk-SECRET24SECRET24SECRET24", "token=SECRET24",
    "sid_guard=SECRET24", "SECRET24" * 6,
])
def test_every_shape_in_one_place(planted):
    assert "SECRET24" not in redact_text(planted)


SESSION = "0123456789abcdef0123456789abcdef"          # 32 hex: under the long-token catch-all


@mutation(SRC, r'''r"([\"']?\s*[:=]\s*[\"']?)''', r'''r"(\s*=\s*)''',
           expect="'0123456789abcdef0123456789abcdef' survived in '{\"sessionid\"")
def test_known_cookies_as_json_and_colon_text():
    out = gone('{"sessionid": "%s", "n": 1}' % SESSION, SESSION)
    assert '"n": 1' in out and out.count('"') == 6          # the quotes around the value stay balanced
    gone("sessionid: %s" % SESSION, SESSION)
    gone("sid_guard='%s'" % SESSION, SESSION)


@mutation(SRC, "out = _COOKIE_OBJECT.sub(_redact_cookie_object, out)", "out = out",
           expect="'0123456789abcdef0123456789abcdef' survived in '[{\"name\"")
def test_cookie_editor_array_written_as_text():
    text = ('[{"name":"sessionid","value":"%s","domain":".example.com"},'
            '{"name":"theme","value":"SECRET25","domain":".example.com","path":"/"},'
            '{"name":"note","value":"keepme"}]' % SESSION)
    out = gone(text, SESSION, "SECRET25")
    assert "keepme" in out and '"name":"sessionid"' in out and ".example.com" in out
    gone("{'value': 'SECRET26', 'name': 'ttwid'}", "SECRET26")                        # a dict repr, value first


@mutation(SRC, r"(?::\d{2,5})?\b)", r"(?::\d{2,5})\b)", expect="'bob' survived in 'bad proxy")
def test_user_pass_at_host_without_a_port():
    out = gone("bad proxy bob:hunter2@proxy.example.com refused", "bob", "hunter2")
    assert "proxy.example.com" in out
    gone("dial user:pass@10.0.0.1 failed", "user:pass")
    assert redact_text("write to mailto:bob@example.com") == "write to mailto:bob@example.com"


@mutation(SRC, r'|\[[0-9a-f:]+\])"', r')"', expect="'alice' survived in")
def test_bracketed_ipv6_proxy_in_the_four_part_form():
    out = gone("proxy [2001:db8::1]:1080:alice:SECRET27 failed", "alice", "SECRET27")
    assert "[2001:db8::1]:1080" in out


@mutation(SRC, "|(?:secret|access|private|signing|auth)[_-]?key)", ")", expect="'SECRET28' survived in")
def test_secret_and_access_key_labels():
    gone("secret_key=SECRET28 and access_key: SECRET29", "SECRET28", "SECRET29")
    assert redact_text("key_id=k1 idempotency_key=abc") == "key_id=k1 idempotency_key=abc"


# ---- field names ending in "key", the size cap and linear time --------------------------------------------------
@mutation(SRC, 'low.endswith("key")', 'low.endswith(("_key", "-key"))', expect="At index 0 diff: 'S1' != '[redacted]'")
def test_field_names_ending_in_key_are_secrets_unless_known_ids():
    d = redact({"accesskey": "S1", "sessionkey": "S2", "signing_key": "S3", "x-api-key": "S4", "key": "S5",
                "id_key": "ok1", "idempotency_key": "ok2", "object_key": "1/output/0.mp4", "key_id": "ok3",
                "monkeys": "ok4"})
    assert [d[k] for k in ("accesskey", "sessionkey", "signing_key", "x-api-key", "key")] == [REDACTED] * 5
    assert [d[k] for k in ("id_key", "idempotency_key", "object_key", "key_id", "monkeys")] == [
        "ok1", "ok2", "1/output/0.mp4", "ok3", "ok4"]


@mutation(SRC, "return \"[omitted len=%d]\" % len(value)", "return value",
           expect="'[redacted]' == '[omitted len=5000]'")
def test_log_values_are_trimmed_before_redaction():
    exact = "z " * 1000
    out = redact_log({"body": "x" * 5000, "n": 1, "items": ["y" * 2001, "short Cookie: a=SECRET30"], "t": (exact,)})
    assert out["body"] == "[omitted len=5000]" and out["n"] == 1 and out["items"][0] == "[omitted len=2001]"
    assert "SECRET30" not in str(out) and out["t"] == [exact]      # exactly the limit is kept


def timed(text: str) -> float:
    started = time.perf_counter()
    redact_text(text)
    return time.perf_counter() - started


def adversarial(n: int) -> dict[str, str]:
    return {
        "base64url": ("Ab-_09" * n)[:n],
        "base64 with plus and slash": ("Ab+/09" * n)[:n],
        "minified json": ('{"a":"b","c":[1,2,{"d":"e"}]},' * n)[:n],
        "open braces": "{" * n,
        "dotted run": ("a." * n)[:n],
        "scheme-like run": ("a+" * n)[:n],
        "labels": ("token" * n)[:n],
        "colons and ats": ("a:b@" * n)[:n],
    }


@mutation(SRC, r'(?i)((?:token', r'(?i)([\w-]*(?:token', expect="AssertionError: base64url")
@mutation(SRC, "[^\\s:@/]{1,128}:[^\\s/]{1,256}@", "[^\\s:@/]+:[^\\s/]+@", expect="AssertionError: minified json")
def test_redaction_time_grows_linearly_not_quadratically():
    """Quadratic patterns took 20 s on 20k characters. The bounds are generous (a loaded machine stays far under
    them); what they separate is seconds from minutes. The small size goes first so a regression fails fast."""
    for name, text in adversarial(10_000).items():
        assert timed(text) < 2.0, name
    for name, text in adversarial(200_000).items():
        assert timed(text) < 5.0, name


def test_planted_secrets_are_still_found_inside_long_text():
    padding = "lorem ipsum, dolor sit. " * 4000
    planted = "%s Cookie: a=SECRET31 %s token=SECRET32 %s bob:hunter2@10.0.0.1:80"
    out = redact_text(planted % (padding, padding, padding))
    assert "SECRET31" not in out and "SECRET32" not in out and "hunter2" not in out


# ---- names ending in "key" (R6) ---------------------------------------------------------------------------------
# Decision: no name is added to the safe list. A name ends in "key" and stays redacted unless it is a name relay
# itself writes (the safe list above). `public_key`, `storage_key` and `cache_key` are not written by any relay
# writer, and a client or provider field with such a name holds either a harmless value (losing it costs a log line)
# or a secret that must not be stored (a mistaken `storage_key` is an access key): redacting is the safe side.
# What must stay readable is what relay writes: the settings keys and the fields of the request bodies the doors
# accept (what a request log stores).
@mutation(SRC, '"id_key", "idempotency_key",', '"id_key", "api_key", "private_key", "secret_key", "access_key", '
                                               '"idempotency_key",', expect="a real secret named *_key stays redacted")
def test_a_real_secret_named_key_stays_redacted_and_the_unknown_key_names_stay_redacted_too():
    secrets = ("api_key", "private_key", "secret_key", "access_key", "signing_key", "x-api-key", "apiKey", "API_KEY")
    out = redact({name: "S-%s" % name for name in secrets})
    assert [v for v in out.values() if v != REDACTED] == [], "a real secret named *_key stays redacted"
    unknown = redact({"public_key": "p", "storage_key": "s", "cache_key": "c"})
    assert set(unknown.values()) == {REDACTED}, "names relay never writes stay redacted (fail closed)"


def test_every_field_name_relay_writes_stays_readable():
    from relay_core.api.routes_gemini import GenerateBody
    from relay_core.api.routes_native import ImageBody, VideoBody
    from relay_core.api.routes_openai import ChatBody
    from relay_core.settings import REGISTRY

    names = set(REGISTRY.specs) | {"key_id", "job_id", "idempotency_key", "object_key", "media_key"}
    for model in (VideoBody, ImageBody, ChatBody, GenerateBody):
        for name, field in model.model_fields.items():
            names |= {name, field.validation_alias if isinstance(field.validation_alias, str) else name}
    shown = redact({n: "v" for n in names})
    assert [n for n, v in shown.items() if v == REDACTED] == [], "relay's own field names stay readable"


@mutation(SRC, "return {trim_log_value(k, limit): trim_log_value(v, limit)", "return {k: trim_log_value(v, limit)",
          expect="a long key is cut too")
def test_a_long_dict_key_is_cut_like_a_long_value():
    out = redact_log({"lorem ipsum " * 300 + "PLANTED31": {"x " * 1500 + "PLANTED32": 1}, "short": 2})
    assert "PLANTED31" not in str(out) and "PLANTED32" not in str(out), "a long key is cut too"
    assert out["short"] == 2


@mutation(SRC, "        record.msg, record.args = redact_log(message), None\n", "        pass\n",
          expect="a secret in a log line")
@mutation(SRC, "record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))",
          "record.exc_text = logging.Formatter().formatException(record.exc_info)", expect="a secret in a traceback")
def test_every_line_and_traceback_a_process_logs_goes_through_the_redactor():
    """A crash or an upstream answer written to the process log must not carry a cookie or a token (review B5)."""
    import io
    import logging

    from relay_core.redact import install_log_redaction

    stream = io.StringIO()
    logger = logging.getLogger("relay.test.redaction")
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    logger.propagate = False
    try:
        install_log_redaction("relay.test.redaction")
        install_log_redaction("relay.test.redaction")                  # twice: still one filter
        assert len(handler.filters) == 1
        logger.warning("upstream said %s", "Set-Cookie: sessionid=PLANTEDcookie0123456789; Path=/")
        try:
            raise RuntimeError("got 401 for Authorization: Bearer PLANTEDbearer0123456789")
        except RuntimeError:
            logger.exception("the request crashed")
    finally:
        logger.removeHandler(handler)
    out = stream.getvalue()
    assert "PLANTEDcookie0123456789" not in out, "a secret in a log line: %s" % out
    assert "PLANTEDbearer0123456789" not in out, "a secret in a traceback: %s" % out
    assert "Traceback" in out and "the request crashed" in out and "RuntimeError" in out, out

