"""The container limits a process reads from its own cgroup (v2): the CPU quota, memory and pids limits and the
`/dev/shm` size, `None` where unlimited or unreadable, and the text the admin shows for each."""

import re

from relay_core import cgroup
from relay_core.testing.mutation import mutation

CG = "packages/relay-core/src/relay_core/cgroup.py"
CPU_RATIO = "quota / period"
CPU_QUOTA_ONLY = "float(quota)"
NESTED = 'if (nested / "cpu.max").is_file():'
MAX_MAPS_TO_NONE = 'None if text is None or text == "max" else int(text)'
MAX_MAPS_TO_MINUS_ONE = '-1 if text == "max" else int(text)'
GIB_BINARY = "GIB, MIB, KIB = 1024**3, 1024**2, 1024"
GIB_DECIMAL = "GIB, MIB, KIB = 1000**3, 1024**2, 1024"


def tree(tmp_path, files: dict[str, str], proc: str = "0::/\n"):
    """A fake /sys/fs/cgroup (`files` maps a relative path to its text) and a fake /proc/self/cgroup."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "cg"
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / "proc").write_text(proc)
    return {"root": str(root), "proc": str(tmp_path / "proc"), "shm": str(tmp_path)}


@mutation(CG, CPU_RATIO, CPU_QUOTA_ONLY, expect="assert 150000.0 == 1.5")
def test_the_cpu_quota_is_the_quota_over_the_period_and_max_is_no_limit(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "150000 100000\n", "memory.max": "max\n"}))
    assert got["cpus"] == 1.5, "cpus == 1.5"
    assert cgroup.own_limits(**tree(tmp_path, {"cpu.max": "800000 100000\n"}))["cpus"] == 8.0
    assert cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000\n", "memory.max": "1\n"}))["cpus"] is None


@mutation(CG, MAX_MAPS_TO_NONE, MAX_MAPS_TO_MINUS_ONE, expect="assert (-1, -1) == (None, None)")
def test_memory_and_pids_are_whole_numbers_and_max_is_none(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000", "memory.max": "12884901888\n",
                                              "pids.max": "2048\n"}))
    assert (got["memory_bytes"], got["pids"], got["read"]) == (12 * 1024**3, 2048, True)
    none = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000", "memory.max": "max\n", "pids.max": "max\n"}))
    assert (none["memory_bytes"], none["pids"]) == (None, None)
    junk = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "x y", "memory.max": "lots", "pids.max": ""}))
    assert (junk["cpus"], junk["memory_bytes"], junk["pids"]) == (None, None, None)


@mutation(CG, NESTED, "if False:", expect="(1.0, 1) == (2.0, 2147483648)")
def test_the_group_of_the_process_wins_when_it_holds_the_limit_files_else_the_root_is_read(tmp_path):
    nested = {"cpu.max": "100000 100000", "memory.max": "1", "system.slice/docker-x.scope/cpu.max": "200000 100000",
              "system.slice/docker-x.scope/memory.max": "2147483648"}
    proc = "0::/system.slice/docker-x.scope\n"
    got = cgroup.own_limits(**tree(tmp_path, nested, proc))
    assert (got["cpus"], got["memory_bytes"]) == (2.0, 2 * 1024**3), "the nested group wins"
    own_namespace = cgroup.own_limits(**tree(tmp_path / "b", nested, "0::/\n"))
    assert (own_namespace["cpus"], own_namespace["memory_bytes"]) == (1.0, 1)
    absent = cgroup.own_limits(**tree(tmp_path / "c", {"cpu.max": "300000 100000"}, "0::/gone/group\n"))
    assert absent["cpus"] == 3.0, "a group without limit files falls back to the root"


def test_no_limit_file_means_not_read_and_every_field_none(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {}))
    assert got == {"read": False, "cpus": None, "memory_bytes": None, "pids": None, "shm_bytes": None}
    v1 = cgroup.own_limits(root=str(tmp_path / "nothing"), proc=str(tmp_path / "nothing"), shm=str(tmp_path))
    assert v1["read"] is False


def test_the_shm_size_is_read_from_the_filesystem(tmp_path):
    got = cgroup.own_limits(**tree(tmp_path, {"cpu.max": "max 100000"}))
    assert isinstance(got["shm_bytes"], int) and got["shm_bytes"] > 0
    gone = cgroup.own_limits(**{**tree(tmp_path / "b", {"cpu.max": "max 100000"}), "shm": str(tmp_path / "missing")})
    assert gone["read"] is True and gone["shm_bytes"] is None


@mutation(CG, GIB_BINARY, GIB_DECIMAL, expect="12.88 GiB")
def test_describe_gives_the_texts_the_admin_reads():
    limits = {"read": True, "cpus": 1.5, "memory_bytes": 12 * 1024**3, "pids": 2048, "shm_bytes": 64 * 1024**2}
    assert cgroup.describe(limits) == {"cpus": "1.5", "memory": "12 GiB", "pids": "2048", "shm": "64 MiB"}
    assert cgroup.describe({**limits, "cpus": 8.0, "memory_bytes": int(1.5 * 1024**3)})["cpus"] == "8"
    assert cgroup.describe({**limits, "memory_bytes": int(1.5 * 1024**3)})["memory"] == "1.5 GiB"
    free = cgroup.describe({**limits, "cpus": None, "memory_bytes": None, "pids": None})
    assert (free["cpus"], free["memory"], free["pids"]) == ("no limit",) * 3, "memory no limit"
    assert cgroup.describe({**limits, "shm_bytes": None})["shm"] == "unreadable"
    assert cgroup.describe({"read": False, "cpus": None}) == dict.fromkeys(("cpus", "memory", "pids", "shm"),
                                                                           "unreadable")


def test_a_worker_reports_five_boot_names_and_never_anything_secret_looking():
    env = {"WORKER_GRACE_S": "9", "WORKER_ID": "w-1", "RELAY_PROFILE_ROOT": "/profiles", "RELAY_CHROME_PATH": "",
           "CREDENTIAL_SEAL_PRIVATE_KEY": "k", "MINIO_SECRET_KEY": "s", "DATABASE_URL": "postgres://u:p@h/d"}
    got = cgroup.boot_knobs(env)
    assert got == {"WORKER_GRACE_S": "9", "WORKER_ID": "w-1", "RELAY_PROFILE_ROOT": "/profiles",
                   "RELAY_CHROME_PATH": None, "RELAY_BROWSER_HEADLESS": None}
    assert not [n for n in cgroup.BOOT_NAMES if re.search(r"KEY|SECRET|PASSWORD|TOKEN|DSN|URL|CREDENTIAL", n)]
