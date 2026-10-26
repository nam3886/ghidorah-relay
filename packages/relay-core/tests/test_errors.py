"""The error registry (I21, I26) and its generated documentation."""

import ast
import importlib
import re
from dataclasses import replace
from pathlib import Path

import pytest

from relay_core import errors
from relay_core.errors import RULES, Rule
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/errors.py"
REPO = Path(__file__).resolve().parents[3]


def load_provider_specs() -> list[str]:
    """Import the spec module of every provider package, so the provider codes are in the registry whatever order the
    tests run in. Providers are found by their place in the repo, so a new one is counted without editing this."""
    names = sorted(p.parent.name + ".spec" for p in (REPO / "packages").glob("provider-*/src/relay_*/spec.py"))
    for name in names:
        importlib.import_module(name)
    return names


PROVIDER_SPECS = load_provider_specs()      # at import: every test below sees the same registry


def good(code="x.thing", **kw) -> Rule:
    base = dict(code=code, http_status=502, error_type="api_error", refund=True, terminal=True, counts_attempt=False,
                job_scoped=True, message="a thing went wrong", public_code=errors.GENERATION_FAILED)
    return Rule(**{**base, **kw})


def broken(rule: Rule) -> list[str]:
    return errors.violations({**RULES, rule.code: rule})


def test_platform_registry_is_sound():
    assert errors.violations(RULES) == []
    assert len(RULES) >= 42      # the platform list
    # nothing in this registry or its envelope speaks of retry
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


CLIENT_CODE_CHECK = "        if r.job_scoped or r.code.split(\".\")[0] not in CLIENT_DOMAINS:\n"
NEUTRAL_CHECK = "    elif r.public_code not in PUBLIC_CODES:\n"


@mutation(SRC, CLIENT_CODE_CHECK, "        if False:\n", expect="no public code")
@mutation(SRC, NEUTRAL_CHECK, "    elif False:\n", expect="no public code")
def test_a_code_without_a_neutral_public_code_is_refused():
    """Only a request-time code of a client domain is shown as itself; every other code names a neutral code."""
    for rule in (good(public_code=""), good(public_code="made_up"), good(public_code="x.thing"),
                 good("pool.thing", refund=False, terminal=False, public_code="pool.thing"),
                 good("internal.thing", job_scoped=False, refund=False, public_code="internal.thing"),
                 good("input.thing", refund=False, public_code="input.thing")):          # job-scoped: not request-time
        assert any("no public code" in v for v in broken(rule)), "no public code: %r" % (rule,)
    assert broken(good("input.thing", job_scoped=False, refund=False, public_code="input.thing")) == []
    assert broken(good("input.thing", job_scoped=False, refund=False, public_code="input.thing", public_text="x."))
    for code in errors.PUBLIC_CODES:
        assert broken(good(public_code=code)) == [], code


@mutation(SRC, "    return r.public_code == INTERNAL_ERROR and not r.public_text\n", "    return False\n",
          expect="hidden codes")
def test_every_registered_code_maps_to_a_neutral_code_or_is_a_client_code():
    """The whole registry, every provider loaded: a new code without a mapping fails at import, and here."""
    assert PROVIDER_SPECS, "no provider spec was found to import"
    assert errors.violations(RULES) == []
    for code, r in RULES.items():
        assert r.public_code in errors.PUBLIC_CODES or (
            r.public_code == code and not r.job_scoped and code.split(".")[0] in errors.CLIENT_DOMAINS), code
    by_code = {p: sorted(c for c, r in RULES.items() if r.public_code == p) for p in errors.PUBLIC_CODES}
    assert all(by_code.values()), "a neutral code nothing maps to: %s" % [p for p, cs in by_code.items() if not cs]
    hidden = {c for c, r in RULES.items() if errors.hidden(r)}
    assert {"internal.frame_unusable", "dola.ui_send_failed", "flow.captcha_key_invalid"} <= hidden, "hidden codes"
    assert errors.INTERNAL not in hidden, "the fallback has its own customer sentence"


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
    assert errors.violations({**RULES, errors.INTERNAL: replace(RULES[errors.INTERNAL],
                                                                public_code=errors.GENERATION_FAILED)})


@mutation(SRC, "return RULES.get(code) or RULES[INTERNAL]", "return RULES[code]",
           expect="KeyError: 'nobody.registered'")
def test_unknown_code_resolves_to_internal():
    assert errors.rule("nobody.registered") is RULES[errors.INTERNAL]
    assert errors.http_status("nobody.registered") == 500
    assert errors.envelope("nobody.registered")["error"]["code"] == "internal_error"


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


SHOWN_MAPPED = "    return r.public_code, r.public_text or PUBLIC_CODES[r.public_code][1]\n"
SHOWN_ITSELF = "    if r.public_code == r.code:\n        return r.code, r.message\n"


@mutation(SRC, SHOWN_MAPPED, "    return r.code, r.message\n", expect="'ours.secret_detail' == 'internal_error'")
@mutation(SRC, SHOWN_ITSELF, "    if True:\n        return r.code, r.message\n",
          expect="'ours.secret_detail' == 'internal_error'")
def test_a_mapped_code_is_shown_as_its_neutral_code_with_its_sentence(monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(RULES))
    errors.register(good("ours.secret_detail", public_code=errors.INTERNAL_ERROR,
                         message="selector #send-button not found"),
                    good("ours.refusal", http_status=422, error_type="invalid_request_error",
                         public_code=errors.CONTENT_REFUSED, message="the account was refused by the waf",
                         public_text="It was refused. Sending it again will not help."),
                    good("ours.lost", public_code=errors.GENERATION_FAILED, message="the session died"))
    body = errors.envelope("ours.secret_detail")["error"]
    assert body["code"] == "internal_error" and body["message"] == errors.PUBLIC_CODES["internal_error"][1]
    assert "selector" not in str(body) and body["type"] == "api_error"
    assert errors.http_status("ours.secret_detail") == 502       # the status comes from the real code
    assert errors.shown("ours.refusal") == ("content_refused", "It was refused. Sending it again will not help.")
    assert errors.envelope("ours.refusal")["error"]["type"] == "invalid_request_error", "the type is the real code's"
    assert errors.shown("ours.lost") == ("generation_failed", errors.PUBLIC_CODES["generation_failed"][1])
    assert errors.shown("input.bad_model") == ("input.bad_model", RULES["input.bad_model"].message)


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


@mutation("docs/error-codes.md", "| `dola.no_image` | `generation_failed` | 422 |",
          "| `dola.no_image` | `content_refused` | 422 |", expect="docs/error-codes.md is stale")
def test_the_committed_docs_hold_the_provider_codes_and_a_stale_file_fails():
    text = errors.DOCS_PATH.read_text(encoding="utf-8")
    assert text == errors.render_docs(), "docs/error-codes.md is stale: run `python -m relay_core.errors`"
    assert PROVIDER_SPECS, "no provider spec was found to import"
    for code in ("dola.content_refused", "dola.likeness_refused", "dola.temporary_error", "dola.ui_send_failed"):
        assert "| `%s` |" % code in text, code
    assert "## `dola.*`" in text


def test_docs_show_hidden_codes_with_what_the_client_sees(monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(RULES))
    errors.register(good("ours.secret_detail", public_code=errors.INTERNAL_ERROR))
    page = errors.render_docs()
    assert "| `ours.secret_detail` | `internal_error` | 502 |" in page
    assert "| `input.bad_model` | itself | 400 |" in page
    for code, (helps, text) in errors.PUBLIC_CODES.items():
        assert "| `%s` | %s | %s |" % (code, helps, text) in page, code


# ---- "every code is assigned somewhere in running code" (I26) --------------------------------------------------
CORE_REGISTRY = "packages/relay-core/src/relay_core/errors.py"


def _definitions(tree, codes) -> dict[str, set[str]]:
    """Module-level `NAME = "<code>"` assignments of one file: {NAME: {code}}."""
    out: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value in codes:
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.setdefault(t.id, set()).add(node.value.value)
    return out


def assigned_codes(root: Path, codes) -> set[str]:
    """Codes that running code USES: a string literal, or a reference to a constant that holds the code, in a source
    file that is not an error registry. The core registry and every provider's `errors.py` list the codes they own, so
    they prove nothing; a constant's own definition is not a use either."""
    trees = {}
    for path in (p for base in ("packages", "apps") for p in (root / base).rglob("src/**/*.py")):
        if path.relative_to(root).as_posix() == CORE_REGISTRY or path.name.startswith("._"):
            continue
        trees[path] = ast.parse(path.read_text(encoding="utf-8"))
    names: dict[str, set[str]] = {}
    for tree in trees.values():
        for name, owned in _definitions(tree, codes).items():
            names.setdefault(name, set()).update(owned)
    found = set()
    for path, tree in trees.items():
        if path.name == "errors.py":
            continue
        definitions = {id(n.value) for n in tree.body if isinstance(n, ast.Assign)}
        found |= {n.value for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and n.value in codes and id(n) not in definitions}
        own, aliases, imported = _definitions(tree, codes), set(), {}
        for n in ast.walk(tree):                                  # what this file binds to an errors module
            if isinstance(n, ast.ImportFrom):
                for a in n.names:
                    if a.name == "errors":
                        aliases.add(a.asname or a.name)
                    elif (n.module or "").split(".")[-1] == "errors":
                        imported[a.asname or a.name] = a.name
            elif isinstance(n, ast.Import):
                aliases |= {a.asname for a in n.names if a.asname and a.name.split(".")[-1] == "errors"}
        for n in ast.walk(tree):
            if isinstance(getattr(n, "ctx", None), ast.Store):
                continue
            if isinstance(n, ast.Attribute):                      # `errors.X`, `pkg.errors.X`: base is an errors module
                base = n.value
                ours = (isinstance(base, ast.Name) and base.id in aliases) or (
                    isinstance(base, ast.Attribute) and base.attr == "errors")
                if ours and n.attr in names:
                    found |= names[n.attr]
            elif isinstance(n, ast.Name):                         # imported from an errors module, or defined here
                real = imported.get(n.id) or (n.id if n.id in own else None)
                if real in names:
                    found |= names[real]
    return found


def test_assignment_scan_sees_a_code_used_and_one_forgotten(tmp_path):
    src = tmp_path / "packages" / "p" / "src" / "m"
    src.mkdir(parents=True)
    (src / "use.py").write_text('def f():\n    raise Fail("input.bad_ratio")\n')
    core = tmp_path / "packages" / "relay-core" / "src" / "relay_core"
    core.mkdir(parents=True)
    (core / "errors.py").write_text('X = "input.bad_model"\n')         # the core registry itself does not count
    (src / "errors.py").write_text('Y = "dola.content_refused"\nZ = "dola.never_used"\nW = "dola.by_name"\n'
                                   'R = [Y, Z, W]\n')                 # defined and listed: not a use
    (src / "engine.py").write_text('from m import errors\n\ndef g():\n    return errors.W\n')
    found = assigned_codes(tmp_path, {"input.bad_ratio", "input.bad_model", "dola.content_refused", "dola.never_used",
                                      "dola.by_name", "x.never"})
    assert found == {"input.bad_ratio", "dola.by_name"}, "a provider's own errors.py must not count as a use"


@mutation("packages/relay-core/tests/test_errors.py", "base.id in aliases) or (\n", "True) or (\n",
          expect="a same-named attribute counted as a use")
def test_a_same_named_attribute_of_another_object_is_not_a_use(tmp_path):
    src = tmp_path / "packages" / "p" / "src" / "m"
    src.mkdir(parents=True)
    core = tmp_path / "packages" / "relay-core" / "src" / "relay_core"
    core.mkdir(parents=True)
    (core / "errors.py").write_text("")
    (src / "errors.py").write_text('CONTENT_REFUSED = "dola.content_refused"\nBY_ALIAS = "dola.by_alias"\n'
                                   'BY_NAME = "dola.by_name"\n')
    (src / "classify.py").write_text("class Outcome:\n    CONTENT_REFUSED = 1\n\n\ndef f(v):\n"
                                     "    return Outcome.CONTENT_REFUSED\n")        # an enum member, not the code
    (src / "uses.py").write_text("from m import errors as codes\nfrom m.errors import BY_NAME\n\n\n"
                                 "def g():\n    return codes.BY_ALIAS, BY_NAME\n")
    found = assigned_codes(tmp_path, {"dola.content_refused", "dola.by_alias", "dola.by_name"})
    assert found == {"dola.by_alias", "dola.by_name"}, "a same-named attribute counted as a use: %s" % sorted(found)


@mutation("packages/relay-core/tests/test_errors.py", "ast.walk(tree)\n                  if isinstance",
          "ast.walk(tree)\n                  if False and isinstance", expect="unassigned codes: [")
def test_every_registered_code_is_assigned():
    unassigned = set(RULES) - assigned_codes(REPO, set(RULES))
    assert not unassigned, "unassigned codes: %s (a code nobody uses is removed from the registry)" % sorted(unassigned)


MAIN_PROBE = (
    "import runpy, sys\n"
    "import relay_core.errors as e\n"
    "e.write_docs = lambda *a: sys.stdout.write(e.render_docs())\n"
    "runpy.run_module('relay_core.errors', run_name='__main__')\n")


@mutation("packages/relay-core/src/relay_core/errors.py",
          '    for spec in sorted((DOCS_PATH.parents[1] / "packages").glob("provider-*/src/relay_*/spec.py")):',
          "    for spec in []:", expect="the documented regenerate command left out the provider codes")
def test_the_documented_regenerate_command_renders_the_provider_sections_and_the_committed_file():
    """Runs the `python -m relay_core.errors` path in a fresh process (no provider imported beforehand) with the file
    write swapped for stdout, so the tree is not touched."""
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-c", MAIN_PROBE], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
    assert "## `dola.*`" in done.stdout, "the documented regenerate command left out the provider codes"
    assert done.stdout == errors.DOCS_PATH.read_text(encoding="utf-8"), "the command's output is not the committed docs"


FREE_RULE_CHECK = ("    if r.free_retries < 0 or (r.free_retries and (r.terminal or r.counts_attempt or "
                   "not r.job_scoped)):\n")
ERRORS = "packages/relay-core/src/relay_core/errors.py"


@mutation(ERRORS, FREE_RULE_CHECK, "    if False:\n", expect="a free-retry rule that makes no sense was accepted")
def test_free_retries_belong_to_a_requeued_job_scoped_code_that_uses_no_attempt_by_itself():
    ok = good(refund=False, terminal=False, free_retries=3)
    assert broken(ok) == [], broken(ok)
    for bad in (dict(refund=True, terminal=True, free_retries=1), dict(refund=True, terminal=False,
                counts_attempt=True, free_retries=1), dict(refund=False, terminal=False, job_scoped=False,
                free_retries=1), dict(refund=False, terminal=False, free_retries=-1)):
        rule = good(**{"refund": False, "terminal": False, **bad})
        assert broken(rule), "a free-retry rule that makes no sense was accepted: %r" % (bad,)


# what a customer reads (a public code, a `public_text`, a neutral sentence, the message of a code shown as itself):
# no internal word
INTERNAL_WORDS = re.compile(r"\baccount|\bpool\b|\bworker\b|\bproxy|cookie|captcha|\bbrowser\b|\bsession|"
                            r"upstream|solver|\blane\b|(?-i:\bD\d{1,3}\b)|\bwaf\b|firewall|\bmint|"
                            r"security (check|token)|\bsign(er|ature)|\btoken|throttl|\bcredits?\b|checkpoint|"
                            r"conversation|selector|\bpage\b|"
                            r"headless|chrome|recaptcha|\bcdp\b|\b7100\d{5}\b|internal\.|\bunexpected\b", re.IGNORECASE)


@mutation("packages/provider-dola/src/relay_dola/errors.py", "TEMPORARILY_UNAVAILABLE, public_text=_TRANSIENT_TEXT)",
          "TEMPORARILY_UNAVAILABLE)",
          expect="has no public sentence")
@mutation(ERRORS, "    return r.job_scoped and (r.terminal or r.counts_attempt or r.free_retries > 0)\n",
          "    return r.job_scoped and (r.terminal or r.counts_attempt)\n", expect="flow.tier_required ends a job")
def test_every_public_code_a_job_can_end_with_has_a_customer_sentence_that_says_whether_sending_again_helps():
    """A new job-ending code without `public_text` fails here, whatever provider registers it."""
    assert PROVIDER_SPECS, "no provider spec was found to import"
    ending = {c for c, r in RULES.items() if not errors.hidden(r) and errors.ends_job(r)}
    assert {"render.deadline", "internal.unexpected"} <= ending
    assert {"flow.tier_required", "qwen.waf_refused"} <= ending, \
        "flow.tier_required ends a job (after its free retries)"
    assert not {c for c in ending if c.startswith("pool.")} and "meta.checkpoint" not in ending, \
        "a code that only requeues never ends a job"
    for code in sorted(ending):
        text = RULES[code].public_text
        assert text, "%s has no public sentence (Rule.public_text)" % code
        assert text.isascii() and text.endswith("."), "%s: one plain English sentence: %r" % (code, text)
        assert "again" in text, "%s: say whether sending again can help: %r" % (code, text)
        assert not INTERNAL_WORDS.search(text), "%s leaks an internal word: %r" % (code, text)


@mutation(ERRORS, 'message="this model option cannot be ordered yet"', 'message="its upstream cost is not known yet"',
          expect="leaks an internal word")
@mutation("packages/provider-qwen/src/relay_qwen/errors.py", '_CHECK_TEXT = "qwen could not take the order',
          '_CHECK_TEXT = "qwen\'s firewall could not take the order', expect="leaks an internal word")
def test_nothing_a_client_can_read_holds_an_internal_word():
    """Every code a client can see, and every text shown with it, across the core and every provider."""
    assert PROVIDER_SPECS, "no provider spec was found to import"
    for code, (helps, text) in errors.PUBLIC_CODES.items():
        assert re.fullmatch(r"[a-z_]+", code) and not INTERNAL_WORDS.search(code), code
        assert not INTERNAL_WORDS.search(text) and not INTERNAL_WORDS.search(helps), "%s leaks: %r" % (code, text)
    for code in RULES:
        shown_code, text = errors.shown(code)
        assert shown_code in errors.PUBLIC_CODES or shown_code == code, "%s is shown as %s" % (code, shown_code)
        assert not INTERNAL_WORDS.search(shown_code), "%s is shown as %s" % (code, shown_code)
        assert not INTERNAL_WORDS.search(text), "%s leaks an internal word: %r" % (code, text)
    shown = {errors.shown(c)[0] for c in RULES}
    assert not {c for c in shown if c not in errors.PUBLIC_CODES and c.split(".")[0] not in errors.CLIENT_DOMAINS}
