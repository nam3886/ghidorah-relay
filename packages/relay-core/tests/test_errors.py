"""The error registry (contracts §11, I21, I26, D19) and its generated documentation."""

import ast
import importlib
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


@mutation("docs/error-codes.md", "| `dola.no_image` | 422 |", "| `dola.no_image` | 423 |",
          expect="docs/error-codes.md is stale")
def test_the_committed_docs_hold_the_provider_codes_and_a_stale_file_fails():
    text = errors.DOCS_PATH.read_text(encoding="utf-8")
    assert text == errors.render_docs(), "docs/error-codes.md is stale: run `python -m relay_core.errors`"
    assert PROVIDER_SPECS, "no provider spec was found to import"
    for code in ("dola.content_refused", "dola.likeness_refused", "dola.temporary_error", "dola.ui_send_failed"):
        assert "| `%s` |" % code in text, code
    assert "## `dola.*`" in text


def test_docs_show_hidden_codes_with_what_the_client_sees(monkeypatch):
    monkeypatch.setattr(errors, "RULES", dict(RULES))
    errors.register(good("ours.secret_detail", public=False, hidden_message="the request could not be submitted"))
    page = errors.render_docs()
    assert "shown as `internal.unexpected`: the request could not be submitted" in page


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
