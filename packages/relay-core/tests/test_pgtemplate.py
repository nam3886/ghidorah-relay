"""The template database of a test run is never reused for a different schema (the mutation stage edits migrations)."""

import pgtemplate

import relay_core.db
from relay_core.testing.mutation import mutation

PGT = "packages/relay-core/tests/pgtemplate.py"


@mutation(PGT, "digest.update(path.name.encode() + path.read_bytes())", "digest.update(path.name.encode())",
          expect="an edited migration kept the same fingerprint")
def test_the_fingerprint_follows_every_byte_of_the_migrations_and_nothing_else(tmp_path, monkeypatch):
    pkg = tmp_path / "db"
    (pkg / "migrations").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    sql = pkg / "migrations" / "0001.sql"
    sql.write_text("CREATE TABLE a (x int);\n", encoding="utf-8")
    monkeypatch.setattr(relay_core.db, "__file__", str(pkg / "__init__.py"))
    before = pgtemplate.schema_fingerprint()
    (pkg / "notes.txt").write_text("not part of the schema", encoding="utf-8")
    assert pgtemplate.schema_fingerprint() == before
    sql.write_text("CREATE TABLE a (x bigint);\n", encoding="utf-8")
    assert pgtemplate.schema_fingerprint() != before, "an edited migration kept the same fingerprint"
