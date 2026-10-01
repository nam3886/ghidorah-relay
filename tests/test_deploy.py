"""The compose file and env templates keep credentials apart (contracts §14.8 static half, I20, v1.3 #1).

The real stack is checked again on the box at deploy time; this is the part that needs no box.
"""

import re
import textwrap
from pathlib import Path

import pytest
import yaml

from relay_core.testing.mutation import mutation

import guards

G = "tests/guards.py"
DEPLOY = guards.REPO / "deploy"
COMPOSE = yaml.safe_load((DEPLOY / "compose.yml").read_text(encoding="utf-8"))["services"]
PRIVATE = guards.PRIVATE_KEY_VAR


def test_repo_deploy_files_are_isolated_and_pinned():
    assert guards.deploy_violations() == []


def test_only_workers_can_see_a_private_key():
    for name, svc in COMPOSE.items():
        names = set(guards._service_env_names(svc))
        for f in guards._service_env_files(svc):
            names |= guards._declared_names(DEPLOY / (f + ".example"))
        assert any(guards.is_private_key_var(v) for v in names) == name.startswith("worker-"), name
    assert "CREDENTIAL_SEAL_PUBLIC_KEY" in guards._declared_names(DEPLOY / "api.env.example")
    for p in ("dola", "vibes", "meta"):
        assert PRIVATE in guards._declared_names(DEPLOY / ("worker-%s.env.example" % p))


def test_every_service_has_its_own_env_file_and_services_sit_in_their_profiles():
    files = [f for svc in COMPOSE.values() for f in guards._service_env_files(svc)]
    assert sorted(files) == sorted(set(files)) == sorted(
        ["api.env", "migrate.env", "worker-dola.env", "worker-vibes.env", "worker-meta.env"])
    for p in ("dola", "vibes", "meta"):
        assert COMPOSE["worker-" + p]["profiles"] == [p]
    # a bare `docker compose up` starts only Postgres and MinIO; the app services start on purpose
    assert COMPOSE["api"]["profiles"] == COMPOSE["migrate"]["profiles"] == ["app"]
    assert "profiles" not in COMPOSE["postgres"] and "profiles" not in COMPOSE["minio"]


def test_images_are_named_per_commit_and_browser_workers_get_a_stop_grace():
    for name, svc in COMPOSE.items():
        if "build" in svc:
            assert svc["image"] == "relay-%s:${GIT_SHA:?set GIT_SHA}" % name
    for name in ("worker-dola", "worker-meta"):
        assert int(COMPOSE[name]["stop_grace_period"].rstrip("s")) >= 720
        assert COMPOSE[name]["shm_size"] == "1g"
    assert "healthcheck" in COMPOSE["minio"]


def test_dockerfile_configuration_for_stop_and_chrome():
    """Configuration only. That a stop reaches the worker alone is proven by tests/browser/test_graceful_stop.py."""
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    entrypoints = re.findall(r"^ENTRYPOINT (.*)$", text, re.M)
    # never `-g`, never xvfb-run as tini's child
    assert entrypoints == ['["tini", "--", "/usr/local/bin/xvfb-exec"]', '["tini", "--"]']
    assert "xvfb-run" not in "\n".join(ln for ln in text.splitlines() if not ln.startswith("#"))
    assert re.search(r"ARG CHROME_SHA256=[0-9a-f]{64}\n", text) and "sha256sum -c" in text
    assert "patchright install" not in text


def test_the_chrome_layer_comes_before_the_code():
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert text.index("FROM xvfb AS chrome") < text.index("FROM chrome AS browser")
    chrome = text[text.index("FROM xvfb AS chrome"):text.index("FROM base AS http")]
    assert "COPY packages" not in chrome and "COPY --from=build" not in chrome and "deploy/vendor" in chrome


def test_the_entry_script_signals_the_worker_only():
    text = (DEPLOY / "xvfb-exec").read_text(encoding="utf-8")
    assert "setsid Xvfb" in text and 'kill -TERM "$worker"' in text and "kill -TERM -" not in text
    assert 'rm -f "$lock" "$sock"' in text and "Xvfb did not come up" in text


GOOD = {
    "compose.yml": """
        services:
          api: {image: "relay-api:1", env_file: [api.env]}
          migrate: {image: "relay-migrate:1", env_file: [migrate.env]}
          worker-dola: {image: "relay-worker-dola:1", env_file: [worker-dola.env]}
          postgres: {image: "postgres:16@sha256:aa"}
        """,
    "compose.check.yml": "services:\n  postgres: {image: 'postgres:16@sha256:aa'}\n",
    "Dockerfile": "FROM python:3@sha256:aa AS base\nCOPY --from=uv:1@sha256:bb /uv /uv\nFROM base AS http\n",
    "api.env.example": "# names only\nCREDENTIAL_SEAL_PUBLIC_KEY=\n",
    "migrate.env.example": "DATABASE_URL=\n",
    "worker-dola.env.example": f"{PRIVATE}=\n",
}


def plant_deploy(root: Path, **changes: str | None) -> None:
    (root / "deploy").mkdir(parents=True, exist_ok=True)
    for name, text in {**GOOD, **changes}.items():
        if text is not None:
            (root / "deploy" / name).write_text(textwrap.dedent(text), encoding="utf-8")


def test_a_clean_planted_deploy_passes(tmp_path):
    plant_deploy(tmp_path)
    assert guards.deploy_violations(tmp_path) == []


@mutation(G, "if len(s) > 1]", "if len(s) > 99]")
def test_a_shared_env_file_is_caught(tmp_path):
    plant_deploy(tmp_path, **{"compose.yml": GOOD["compose.yml"].replace("[api.env]", "[shared.env]")
                              .replace("[worker-dola.env]", "[shared.env]"),
                              "shared.env.example": "DATABASE_URL=\n"})
    found = guards.deploy_violations(tmp_path)
    assert any("shared.env is shared by api, worker-dola" in v for v in found), found


@mutation(G, 'bad += ["service %s reads', 'bad += [] and ["service %s reads')
def test_the_api_reading_an_env_file_with_the_private_key_is_caught(tmp_path):
    plant_deploy(tmp_path, **{"api.env.example": f"{PRIVATE}=\n"})
    found = guards.deploy_violations(tmp_path)
    assert any("service api reads api.env, which declares" in v for v in found), found


@mutation(G, "for var in sorted(v for v in _service_env_names(svc) if is_private_key_var(v)):", "for var in []:")
@mutation(G, 'return "PRIVATE_KEY" in name.upper()', "return name == PRIVATE_KEY_VAR")
@pytest.mark.parametrize("env", ["{%s: x}" % PRIVATE, "[%s=x]" % PRIVATE, "[%s]" % PRIVATE,
                                 "{%s_OLD: x}" % PRIVATE, "[seal_private_key=x]"])
def test_the_private_key_in_the_environment_of_api_or_migrate_is_caught(tmp_path, env):
    compose = GOOD["compose.yml"].replace('env_file: [api.env]}', "env_file: [api.env], environment: %s}" % env)
    plant_deploy(tmp_path, **{"compose.yml": compose})
    found = guards.deploy_violations(tmp_path)
    assert any("service api sets" in v for v in found), found


@mutation(G, "if not (deploy / (f + \".example\")).exists():", "if False:")
def test_an_env_file_without_a_template_is_caught(tmp_path):
    plant_deploy(tmp_path, **{"migrate.env.example": None})
    (tmp_path / "deploy" / "migrate.env.example").unlink(missing_ok=True)
    found = guards.deploy_violations(tmp_path)
    assert any("migrate.env has no migrate.env.example" in v for v in found), found


@mutation(G, 'not re.fullmatch(r"[A-Z][A-Z0-9_]*=", line)', "False")
@pytest.mark.parametrize("line", ["DATABASE_URL=postgresql://x", "database_url=", "NO_EQUALS"])
def test_a_template_with_a_value_is_caught(tmp_path, line):
    plant_deploy(tmp_path, **{"migrate.env.example": line + "\n"})
    found = guards.deploy_violations(tmp_path)
    assert any("is not a bare variable name" in v for v in found), found


@mutation(G, 'and not ex.name.startswith("worker-")', "and False")
def test_the_private_key_in_a_non_worker_template_is_caught(tmp_path):
    plant_deploy(tmp_path, **{"admin.env.example": f"{PRIVATE}=\n"})
    found = guards.deploy_violations(tmp_path)
    assert any("admin.env.example declares" in v for v in found), found


@mutation(G, 'if image and "@sha256:" not in image and not image.startswith("relay-"):', "if False:")
@mutation(G, 'if ref and ref.group(1) not in stages and "@sha256:" not in ref.group(1):', "if False:")
@pytest.mark.parametrize("changes, why", [
    ({"compose.yml": GOOD["compose.yml"].replace("postgres:16@sha256:aa", "postgres:16")}, "postgres:16 is not pinned"),
    ({"compose.check.yml": "services:\n  minio: {image: 'pgsty/minio:RELEASE.x'}\n"}, "image pgsty/minio:RELEASE.x"),
    ({"Dockerfile": "FROM python:3.13-slim AS base\n"}, "python:3.13-slim is not pinned"),
    ({"Dockerfile": GOOD["Dockerfile"] + "COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uv\n"}, "uv:0.9 is not pinned"),
])
def test_an_image_without_a_digest_is_caught(tmp_path, changes, why):
    plant_deploy(tmp_path, **changes)
    found = guards.deploy_violations(tmp_path)
    assert any(why in v for v in found), found


@mutation(G, 'return "PRIVATE_KEY" in name.upper()', "return name == PRIVATE_KEY_VAR")
def test_a_second_private_key_variable_in_an_api_template_is_caught(tmp_path):
    plant_deploy(tmp_path, **{"api.env.example": "CREDENTIAL_SEAL_PUBLIC_KEY=\n%s_OLD=\n" % PRIVATE})
    found = guards.deploy_violations(tmp_path)
    assert any("service api reads api.env, which declares %s_OLD" % PRIVATE in v for v in found), found
    assert any("api.env.example declares %s_OLD" % PRIVATE in v for v in found), found


@mutation(G, 'if name.startswith("worker-"):', 'if name not in ("api", "migrate"):')
def test_a_non_worker_service_reading_a_worker_env_file_is_caught(tmp_path):
    reporter = "          reporter: {image: 'relay-reporter:1', env_file: [worker-dola.env]}\n"
    compose = GOOD["compose.yml"].rstrip(" ") + reporter
    plant_deploy(tmp_path, **{"compose.yml": compose})
    found = guards.deploy_violations(tmp_path)
    assert any("service reporter reads worker-dola.env, which declares" in v for v in found), found
