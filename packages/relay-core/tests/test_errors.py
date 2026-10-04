"""The error registry (contracts §11, I21, I26, D19) and its generated documentation."""

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from relay_core import errors
from relay_core.errors import RULES, Rule
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/errors.py"
REPO = Path(__file__).resolve().parents[3]


def good(code="x.thing", **kw) -> Rule:
    base = dict(code=code, http_status=502, error_type="api_error", refund=True, terminal=True, counts_attempt=False,
                public=True, job_scoped=True, message="a thing went wrong")
    return Rule(**{**base, **kw})


def broken(rule: Rule) -> list[str]:
    return errors.violations({**RULES, rule.code: rule})


def test_platform_registry_is_sound():
    assert errors.violations(RULES) == []
    assert len(RULES) >= 42      # the §11 platform list
    # D19: nothing in this registry or its envelope speaks of retry
    assert not hasattr(Rule, "retryable")


@mutation(SRC, "if r.terminal and r.counts_attempt:", "if False and r.counts_attempt:",
           expect="any(\"terminal and counts_attempt\" in v for v in broken(good(terminal=True, counts_attempt=True)))")
def test_terminal_and_counts_attempt_refused():
    assert any("terminal and counts_attempt" in v for v in broken(good(terminal=True, counts_attempt=True)))


@mutation(SRC, "elif r.refund != (r.terminal or r.counts_attempt):", "elif False:",
           expect="any(\"exactly when it is refunded\" in v for v in broken(good(refund=False)))")
def test_refund_equals_job_ends_failed():
    assert any("exactly when it is refunded" in v for v in broken(good(refund=False)))           # dies, not refunded
    assert any("exactly when it is refunded" in v
               for v in broken(good(terminal=False, counts_attempt=False, refund=True)))         # requeued, refunded


def test_no_job_means_nothing_to_refund():
    assert any("nothing to refund" in v for v in broken(good(job_scoped=False, refund=True)))


@mutation(SRC, 'if r.code.startswith("pool.") and', 'if False and',
           expect="any(\"pool.* is our fault\" in v for v in broken(rule)), flag")
def test_pool_codes_never_end_or_refund_a_job():
    for flag in ("terminal", "counts_attempt"):
        rule = good("pool.thing", refund=True, terminal=flag == "terminal", counts_attempt=flag == "counts_attempt")
        assert any("pool.* is our fault" in v for v in broken(rule)), flag


def test_hidden_message_exactly_when_not_public():
    assert broken(good(public=False, hidden_message=""))
    assert broken(good(public=True, hidden_message="x"))
    assert broken(good(public=False, hidden_message="the request could not be submitted")) == []


def test_status_and_type_agree():
    assert broken(good(http_status=200))
    assert broken(good(error_type="made_up"))
    assert broken(good(http_status=429, error_type="api_error"))
    assert broken(good(http_status=502, error_type="invalid_request_error"))


def test_malformed_code_and_missing_message_refused():
    assert broken(good("NoDot"))
    assert broken(good(message=""))


def test_fallback_must_exist_and_be_public():
    without = {k: v for k, v in RULES.items() if k != errors.INTERNAL}
    assert errors.violations(without)
    assert errors.violations({**RULES, errors.INTERNAL: replace(RULES[errors.INTERNAL], public=False,
                                                                hidden_message="x")})


@mutation(SRC, "return RULES.get(code) or RULES[INTERNAL]", "return RULES[code]",
           expect="KeyError: 'nobody.registered'")
def test_unknown_code_resolves_to_internal():
    assert errors.rule("nobody.registered") is RULES[errors.INTERNAL]
    assert errors.http_status("nobody.registered") == 500
    assert errors.envelope("nobody.registered")["error"]["code"] == "internal.unexpected"


def test_every_job_ending_code_has_an_http_status():
    for r in RULES.values():
        if r.job_scoped and (r.terminal or r.counts_attempt):
            assert 400 <= errors.http_status(r.code) <= 599, r.code
    assert errors.http_status("render.deadline") == 504 and errors.http_status("quota.key_exceeded") == 429


def test_envelope_is_openai_style_with_no_retryable_or_refunded():
    body = errors.envelope("input.bad_model", param="model")
    assert body == {"error": {"code": "input.bad_model", "message": RULES["input.bad_model"].message,
                              "type": "invalid_request_error", "param": "model"}}
    assert set(body["error"]) == {"code", "message", "type", "param"}
    assert errors.envelope("auth.invalid_key")["error"]["param"] is None
    assert errors.envelope("auth.invalid_key")["error"]["type"] == "authentication_error"


@mutation(SRC, "return (r.code, r.message) if r.public else (INTERNAL, r.hidden_message)",
          "return (r.code, r.message)", expect="'ours.secret_detail' == 'internal.unexpected'")
def test_hidden_code_is_replaced_with_its_text(monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(RULES))
    errors.register(good("ours.secret_detail", public=False, hidden_message="the request could not be submitted",
                         message="selector #send-button not found"))
    body = errors.envelope("ours.secret_detail")["error"]
    assert body["code"] == "internal.unexpected" and body["message"] == "the request could not be submitted"
    assert "selector" not in str(body) and body["type"] == "api_error"
    assert errors.http_status("ours.secret_detail") == 502       # the status comes from the real code


def test_register_adds_valid_rules_and_refuses_the_rest_atomically(monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(RULES))
    before = dict(errors.RULES)
    errors.register(good("ours.first"))
    assert "ours.first" in errors.RULES
    with pytest.raises(ValueError, match="already registered"):
        errors.register(good("ours.first"))
    with pytest.raises(ValueError, match="already registered"):
        errors.register(good("input.invalid"))
    with pytest.raises(ValueError, match="terminal and counts_attempt"):
        errors.register(good("ours.ok"), good("ours.bad", counts_attempt=True))
    assert set(errors.RULES) == set(before) | {"ours.first"}      # the valid half of a refused call is not kept


def test_docs_are_generated_and_current():
    text = errors.DOCS_PATH.read_text(encoding="utf-8")
    assert text == errors.render_docs(), "docs/error-codes.md is stale: run `python -m relay_core.errors`"
    undocumented = sorted(c for c in RULES if "`%s`" % c not in text)
    assert not undocumented, undocumented
    assert "retryable" not in text


def test_docs_show_hidden_codes_with_what_the_client_sees(monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(RULES))
    errors.register(good("ours.secret_detail", public=False, hidden_message="the request could not be submitted"))
    page = errors.render_docs()
    assert "shown as `internal.unexpected`: the request could not be submitted" in page


# ---- "every code is assigned somewhere in running code" (I26) --------------------------------------------------
def assigned_codes(root: Path, codes) -> set[str]:
    """Codes that appear as a string literal in any source file other than the registry itself."""
    found = set()
    for path in (p for base in ("packages", "apps") for p in (root / base).rglob("src/**/*.py")):
        if path.name == "errors.py" or path.name.startswith("._"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found |= {n.value for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and n.value in codes}
    return found


def test_assignment_scan_sees_a_code_used_and_one_forgotten(tmp_path):
    src = tmp_path / "packages" / "p" / "src" / "m"
    src.mkdir(parents=True)
    (src / "use.py").write_text('def f():\n    raise Fail("input.bad_ratio")\n')
    (src / "errors.py").write_text('X = "input.bad_model"\n')          # the registry itself does not count
    assert assigned_codes(tmp_path, {"input.bad_ratio", "input.bad_model"}) == {"input.bad_ratio"}


# Platform codes that no running code assigns yet; each phase that assigns one removes it here. The test fails both
# ways: an unassigned code missing from this list (a code nobody uses, or a provider code from its own phase on), and
# an entry that is assigned now (a stale entry), so the list can only be exactly the unassigned codes.
PENDING = frozenset("""
input.prompt_ratio_conflict input.prompt_duration_conflict
input.template_not_found
pool.no_account pool.account_cannot_afford pool.account_out_of_points pool.account_logged_out
pool.account_throttled pool.account_region_blocked pool.proxy_failed
account.dead_session account.wrong_conversation account.prompt_mismatch
render.watermarked_only
""".split())


@mutation("packages/relay-core/tests/test_errors.py", "ast.walk(tree)\n                  if isinstance",
          "ast.walk(tree)\n                  if False and isinstance", expect="unassigned but not listed: [")
def test_every_registered_code_is_assigned_or_pending():
    unassigned = set(RULES) - assigned_codes(REPO, set(RULES))
    assert unassigned == PENDING, (
        "unassigned but not listed: %s; listed but assigned now (remove): %s"
        % (sorted(unassigned - PENDING), sorted(PENDING - unassigned)))
