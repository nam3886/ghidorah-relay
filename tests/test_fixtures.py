"""The committed fixtures stay redacted and documented."""

import importlib.util
import json

from guards import REPO

FIXTURES = REPO / "fixtures"
_spec = importlib.util.spec_from_file_location("redact_fixtures", REPO / "scripts" / "redact-fixtures.py")
redact = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redact)


def test_redactor_selftest():
    redact.selftest()


def test_no_signed_query_value_or_credential_header():
    assert redact.leaks(str(FIXTURES)) == []


def test_every_fixture_is_listed_in_the_readme():
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    files = sorted(p.relative_to(FIXTURES).as_posix() for p in FIXTURES.rglob("*.json"))
    assert files and [f for f in files if "`%s`" % f not in readme] == []


def test_fixtures_are_json():
    for p in FIXTURES.rglob("*.json"):
        json.loads(p.read_text(encoding="utf-8"))


def test_readme_lists_every_gap_the_generator_writes():
    readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
    assert redact.GAPS and [g for g in redact.GAPS if "- " + g not in readme] == []
