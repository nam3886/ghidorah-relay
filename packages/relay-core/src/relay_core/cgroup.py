"""The limits of the container this process runs in, read once at start (cgroup v2, stdlib only).

A worker writes them into its heartbeat row and the api keeps its own, so the admin sees the CPU quota, the memory and
pids limits and the `/dev/shm` size next to the settings that depend on them. A later `docker update` shows after a
restart. Nothing here guesses: a value that cannot be read is `None`, and `read` is false when no limit file could be
read at all (cgroup v1, or a process outside a container).
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# The environment names a worker reports beside its container limits. A fixed list: nothing else is ever copied from
# the environment, so a secret cannot end up in the record.
BOOT_NAMES = ("WORKER_GRACE_S", "WORKER_ID", "RELAY_PROFILE_ROOT", "RELAY_CHROME_PATH", "RELAY_BROWSER_HEADLESS")
UNREADABLE, NO_LIMIT = "unreadable", "no limit"
GIB, MIB, KIB = 1024**3, 1024**2, 1024


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _number(text: str | None) -> int | None:
    """An int, or None for `max` (no limit), nothing, or text that is not a whole number."""
    try:
        return None if text is None or text == "max" else int(text)
    except ValueError:
        return None


def _cpus(text: str | None) -> float | None:
    """`cpu.max` is `<quota> <period>` or `max <period>`: the quota over the period, in CPUs."""
    parts = (text or "").split()
    if len(parts) != 2 or parts[0] == "max":
        return None
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    return quota / period if period > 0 else None


def _directory(root: str, proc: str) -> Path:
    """The cgroup directory of this process: `root/<path>` of the `0::<path>` line when it holds `cpu.max` (a container
    in the host's cgroup namespace), else `root` itself (its own namespace, where the line reads `0::/`)."""
    base = Path(root)
    text = _read(Path(proc)) or ""
    for line in text.splitlines():
        if line.startswith("0::"):
            nested = base / line[3:].lstrip("/")
            if (nested / "cpu.max").is_file():
                return nested
    return base


def own_limits(root: str = "/sys/fs/cgroup", proc: str = "/proc/self/cgroup", shm: str = "/dev/shm") -> dict[str, Any]:
    """`{"read", "cpus", "memory_bytes", "pids", "shm_bytes"}`; every limit is `None` when there is none or it cannot
    be read."""
    here = _directory(root, proc)
    cpu, memory, pids = _read(here / "cpu.max"), _read(here / "memory.max"), _read(here / "pids.max")
    read = cpu is not None or memory is not None
    try:
        st = os.statvfs(shm)
        shm_bytes: int | None = st.f_blocks * st.f_frsize
    except OSError:
        shm_bytes = None
    return {"read": read, "cpus": _cpus(cpu) if read else None, "memory_bytes": _number(memory) if read else None,
            "pids": _number(pids) if read else None, "shm_bytes": shm_bytes if read else None}


def boot_knobs(env: Mapping[str, str]) -> dict[str, str | None]:
    """The boot-time variables a worker reports (None when unset), from the fixed list `BOOT_NAMES` only."""
    return {name: env.get(name) or None for name in BOOT_NAMES}


def _size(n: int) -> str:
    for unit, label in ((GIB, "GiB"), (MIB, "MiB"), (KIB, "KiB")):
        if n >= unit:
            return "%g %s" % (round(n / unit, 2), label)
    return "%d bytes" % n


def describe(limits: Mapping[str, Any]) -> dict[str, str]:
    """The record as the admin reads it: `cpus` "1.5", `memory` "12 GiB", `pids` "2048", `shm` "64 MiB", with
    "no limit" for a limit that is not set and "unreadable" for one that could not be read."""
    if not limits.get("read"):
        return {"cpus": UNREADABLE, "memory": UNREADABLE, "pids": UNREADABLE, "shm": UNREADABLE}
    cpus, memory, pids, shm = (limits.get(k) for k in ("cpus", "memory_bytes", "pids", "shm_bytes"))
    return {"cpus": NO_LIMIT if cpus is None else "%g" % cpus,
            "memory": NO_LIMIT if memory is None else _size(memory),
            "pids": NO_LIMIT if pids is None else str(pids),
            "shm": UNREADABLE if shm is None else _size(shm)}
