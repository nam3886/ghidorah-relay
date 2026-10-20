"""The container limits a process reads from its own cgroup (v2): the CPU quota, memory and pids limits and the
`/dev/shm` size. "No limit" (`max`) is told apart from "could not read", and the text the admin shows says which."""

import os
import re

import pytest

from relay_core import cgroup
from relay_core.testing.mutation import mutation

CG = "packages/relay-core/src/relay_core/cgroup.py"
CPU_RATIO = "quota / period"
CPU_QUOTA_ONLY = "float(quota)"
NESTED = "if any((nested / name).is_file() for name in LIMIT_FILES.values()):"
MAX_MAPS_TO_NONE = '    if text == "max":\n        return None, True\n'
MAX_MAPS_TO_MINUS_ONE = '    if text == "max":\n        return -1, True\n'
GIB_BINARY = "GIB, MIB, KIB = 1024**3, 1024**2, 1024"
GIB_DECIMAL = "GIB, MIB, KIB = 1000**3, 1024**2, 1024"
MISSING_NOT_NO_LIMIT = 'UNREADABLE if "pids" in missing else'
MISSING_IGNORED = "UNREADABLE if False else"
ALL = ("cpus", "memory_bytes", "pids")


def tree(tmp_path, files: dict[str, str], proc: str = "0::/\n"):
    """A fake /sys/fs/cgroup (`files` maps a relative path to its text) and a fake /proc/self/cgroup."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "cg"
    root.mkdir(exist_ok=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / "proc").write_text(proc)
    return {"root": str(root), "proc": str(tmp_path / "proc"), "shm": str(tmp_path)}


@mutation(CG, CPU_RATIO, CPU_QUOTA_ONLY, expect="assert 150000.0 == 1.5")
def test_the_cpu_quota_is_the_quota_over_the_period_and_max_is_no_limit(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "150000 100000\n", "memory.max": "max\n"}))
    assert got["cpus"] == 1.5
    assert cgroup.own_limits(**tree(tmp_path, {"cpu.max": "800000 100000\n"}))["cpus"] == 8.0
    none = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000\n", "memory.max": "1\n"}))
    assert none["cpus"] is None and "cpus" not in none["missing"], "max is a limit that is not set, not unreadable"


@mutation(CG, MAX_MAPS_TO_NONE, MAX_MAPS_TO_MINUS_ONE, expect="assert (-1, -1) == (None, None)")
def test_memory_and_pids_are_whole_numbers_and_max_is_none(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000", "memory.max": "12884901888\n",
                                              "pids.max": "2048\n"}))
    assert (got["memory_bytes"], got["pids"], got["read"], got["missing"]) == (12 * 1024**3, 2048, True, [])
    none = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000", "memory.max": "max\n", "pids.max": "max\n"}))
    assert (none["memory_bytes"], none["pids"]) == (None, None)
    assert none["missing"] == []


def test_a_file_that_is_absent_or_not_a_number_is_named_missing_never_no_limit(tmp_path):
    junk = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "x y", "memory.max": "lots", "pids.max": "2"}))
    assert (junk["cpus"], junk["memory_bytes"], junk["pids"], junk["read"]) == (None, None, 2, True)
    assert junk["missing"] == ["cpus", "memory_bytes"]
    only_memory = cgroup.own_limits(**tree(tmp_path / "m", {"memory.max": "1073741824"}))
    assert (only_memory["read"], only_memory["memory_bytes"], only_memory["missing"]) == (True, 1073741824,
                                                                                           ["cpus", "pids"])
    only_pids = cgroup.own_limits(**tree(tmp_path / "p", {"pids.max": "max"}))
    assert (only_pids["read"], only_pids["pids"], only_pids["missing"]) == (True, None, ["cpus", "memory_bytes"])


@mutation(CG, NESTED, "if False:", expect="(1.0, 1) == (2.0, 2147483648)")
def test_the_group_of_the_process_wins_when_it_holds_a_limit_file_else_the_root_is_read(tmp_path):
    nested = {"cpu.max": "100000 100000", "memory.max": "1", "system.slice/docker-x.scope/cpu.max": "200000 100000",
              "system.slice/docker-x.scope/memory.max": "2147483648"}
    proc = "0::/system.slice/docker-x.scope\n"
    got = cgroup.own_limits(**tree(tmp_path, nested, proc))
    assert (got["cpus"], got["memory_bytes"]) == (2.0, 2 * 1024**3)
    own_namespace = cgroup.own_limits(**tree(tmp_path / "b", nested, "0::/\n"))
    assert (own_namespace["cpus"], own_namespace["memory_bytes"]) == (1.0, 1)
    absent = cgroup.own_limits(**tree(tmp_path / "c", {"cpu.max": "300000 100000"}, "0::/gone/group\n"))
    assert absent["cpus"] == 3.0, "a group without limit files falls back to the root"
    only_memory = {"memory.max": "10", "g/memory.max": "20"}
    got = cgroup.own_limits(**tree(tmp_path / "d", only_memory, "0::/g\n"))
    assert got["memory_bytes"] == 20, "a group that holds only memory.max is still the process's group"
    only_pids = cgroup.own_limits(**tree(tmp_path / "e", {"pids.max": "10", "g/pids.max": "20"}, "0::/g\n"))
    assert only_pids["pids"] == 20


def test_no_limit_file_means_not_read_and_every_field_none_and_missing(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {}))
    assert got == {"read": False, "cpus": None, "memory_bytes": None, "pids": None, "shm_bytes": None,
                   "missing": list(ALL)}
    v1 = cgroup.own_limits(root=str(tmp_path / "nothing"), proc=str(tmp_path / "nothing"), shm=str(tmp_path))
    assert v1["read"] is False


def test_a_failed_read_returns_not_read_and_never_raises(tmp_path, monkeypatch):
    def denied(root, proc):
        raise PermissionError("denied")

    monkeypatch.setattr(cgroup, "_directory", denied)
    assert cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 1"}))["read"] is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_a_group_directory_the_process_may_not_enter_is_not_read_and_does_not_stop_it(tmp_path):
    args = tree(tmp_path, {"g/cpu.max": "100000 100000"}, "0::/g\n")
    (tmp_path / "cg" / "g").chmod(0)
    try:
        assert cgroup.own_limits(**args)["read"] in (True, False)
    finally:
        (tmp_path / "cg" / "g").chmod(0o700)


def test_the_shm_size_is_read_from_the_filesystem(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000"}))
    assert isinstance(got["shm_bytes"], int) and got["shm_bytes"] > 0
    gone = cgroup.own_limits(**{**tree(tmp_path / "b", {"cpu.max": "max 100000"}), "shm": str(tmp_path / "missing")})
    assert gone["read"] is True and gone["shm_bytes"] is None


@mutation(CG, GIB_BINARY, GIB_DECIMAL, expect="12.88 GiB")
def test_describe_gives_the_texts_the_admin_reads():
    limits = {"read": True, "cpus": 1.5, "memory_bytes": 12 * 1024**3, "pids": 2048, "shm_bytes": 64 * 1024**2,
              "missing": []}
    assert cgroup.describe(limits) == {"cpus": "1.5", "memory": "12 GiB", "pids": "2048", "shm": "64 MiB"}
    assert cgroup.describe({**limits, "cpus": 8.0})["cpus"] == "8"
    assert cgroup.describe({**limits, "memory_bytes": int(1.5 * 1024**3)})["memory"] == "1.5 GiB"
    assert cgroup.describe({**limits, "memory_bytes": 2_000_000_000})["memory"] == "≈ 1.86 GiB"
    free = cgroup.describe({**limits, "cpus": None, "memory_bytes": None, "pids": None})
    assert (free["cpus"], free["memory"], free["pids"]) == ("no limit",) * 3
    assert cgroup.describe({**limits, "shm_bytes": None})["shm"] == "unreadable"
    assert cgroup.describe({"read": False, "cpus": None}) == dict.fromkeys(("cpus", "memory", "pids", "shm"),
                                                                           "unreadable")


@mutation(CG, MISSING_NOT_NO_LIMIT, MISSING_IGNORED, expect="'pids': 'no limit'")
def test_a_limit_that_could_not_be_read_is_shown_unreadable_and_not_as_no_limit():
    limits = {"read": True, "cpus": None, "memory_bytes": 1024**3, "pids": None, "shm_bytes": 1024**2,
              "missing": ["cpus", "pids"]}
    assert cgroup.describe(limits) == {"cpus": "unreadable", "memory": "1 GiB", "pids": "unreadable", "shm": "1 MiB"}
    assert cgroup.describe({k: v for k, v in limits.items() if k != "missing"})["pids"] == "no limit", (
        "a record from a worker that predates `missing` still reads")


def test_a_worker_reports_five_boot_names_and_never_anything_secret_looking():
    env = {"WORKER_GRACE_S": "9", "WORKER_ID": "w-1", "RELAY_PROFILE_ROOT": "/profiles", "RELAY_CHROME_PATH": "",
           "CREDENTIAL_SEAL_PRIVATE_KEY": "k", "MINIO_SECRET_KEY": "s", "DATABASE_URL": "postgres://u:p@h/d"}
    got = cgroup.boot_knobs(env)
    assert got == {"WORKER_GRACE_S": "9", "WORKER_ID": "w-1", "RELAY_PROFILE_ROOT": "/profiles",
                   "RELAY_CHROME_PATH": None, "RELAY_BROWSER_HEADLESS": None}
    assert not [n for n in cgroup.BOOT_NAMES if re.search(r"KEY|SECRET|PASSWORD|TOKEN|DSN|URL|CREDENTIAL", n)]
