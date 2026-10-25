"""The limits of the container this process runs in, read once at start (cgroup v2, stdlib only).

A worker writes them into its heartbeat row and the api keeps its own, so the admin sees the CPU quota, the memory and
pids limits and the `/dev/shm` size next to the settings that depend on them. A later `docker update` shows after a
restart. "No limit" (`max`) and "could not read" are different facts: a limit is `None` for the first, and its
name is in `missing` for the second. `read` is false when none of the three limit files could be read at all
(cgroup v1, a process outside a container, or a read that failed).
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

LIMIT_FILES = {"cpus": "cpu.max", "memory_bytes": "memory.max", "pids": "pids.max"}
UNREADABLE, NO_LIMIT = "unreadable", "no limit"
GIB, MIB, KIB = 1024**3, 1024**2, 1024


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _number(text: str | None) -> tuple[int | None, bool]:
    """(value, readable): `max` is (None, True), no limit; nothing, or text that is no whole number: (None, False)."""
    if text == "max":
        return None, True
    try:
        return int(text or ""), True
    except ValueError:
        return None, False


def _cpus(text: str | None) -> tuple[float | None, bool]:
    """`cpu.max` is `<quota> <period>` or `max <period>`: the quota over the period, in CPUs."""
    parts = (text or "").split()
    if len(parts) != 2:
        return None, False
    if parts[0] == "max":
        return None, True
    try:
        quota, period = int(parts[0]), int(parts[1])
    except ValueError:
        return None, False
    return (quota / period, True) if period > 0 else (None, False)


def _directory(root: str, proc: str) -> Path:
    """The cgroup directory of this process: `root/<path>` of the `0::<path>` line when it holds a limit file (a
    container in the host's cgroup namespace), else `root` itself (its own namespace, where the line reads `0::/`)."""
    base = Path(root)
    text = _read(Path(proc)) or ""
    for line in text.splitlines():
        if line.startswith("0::"):
            nested = base / line[3:].lstrip("/")
            if any((nested / name).is_file() for name in LIMIT_FILES.values()):
                return nested
    return base


def _unreadable() -> dict[str, Any]:
    return {"read": False, "cpus": None, "memory_bytes": None, "pids": None, "shm_bytes": None,
            "missing": list(LIMIT_FILES)}


def own_limits(root: str = "/sys/fs/cgroup", proc: str = "/proc/self/cgroup", shm: str = "/dev/shm") -> dict[str, Any]:
    """`{"read", "cpus", "memory_bytes", "pids", "shm_bytes", "missing"}`: a limit is `None` when there is none, and
    named in `missing` when it could not be read. Never raises: a failed read is `read: false`."""
    try:
        here = _directory(root, proc)
        cpus, cpus_ok = _cpus(_read(here / LIMIT_FILES["cpus"]))
        memory, memory_ok = _number(_read(here / LIMIT_FILES["memory_bytes"]))
        pids, pids_ok = _number(_read(here / LIMIT_FILES["pids"]))
        if not (cpus_ok or memory_ok or pids_ok):
            return _unreadable()
        try:
            st = os.statvfs(shm)
            shm_bytes: int | None = st.f_blocks * st.f_frsize
        except OSError:
            shm_bytes = None
    except OSError:
        return _unreadable()
    missing = [name for name, ok in (("cpus", cpus_ok), ("memory_bytes", memory_ok), ("pids", pids_ok)) if not ok]
    return {"read": True, "cpus": cpus, "memory_bytes": memory, "pids": pids, "shm_bytes": shm_bytes,
            "missing": missing}


def _size(n: int) -> str:
    for unit, label in ((GIB, "GiB"), (MIB, "MiB"), (KIB, "KiB")):
        if n >= unit:
            shown = round(n / unit, 2)
            return ("%g %s" if abs(shown * unit - n) < 1 else "≈ %g %s") % (shown, label)
    return "%d bytes" % n


def describe(limits: Mapping[str, Any]) -> dict[str, str]:
    """The record as the admin reads it: `cpus` "1.5", `memory` "12 GiB", `pids` "2048", `shm` "64 MiB", with
    "no limit" for a limit that is not set and "unreadable" for one that could not be read."""
    if not limits.get("read"):
        return {"cpus": UNREADABLE, "memory": UNREADABLE, "pids": UNREADABLE, "shm": UNREADABLE}
    missing = limits.get("missing") or ()
    cpus, memory, pids, shm = (limits.get(k) for k in ("cpus", "memory_bytes", "pids", "shm_bytes"))
    return {"cpus": UNREADABLE if "cpus" in missing else NO_LIMIT if cpus is None else "%g" % cpus,
            "memory": UNREADABLE if "memory_bytes" in missing else NO_LIMIT if memory is None else _size(memory),
            "pids": UNREADABLE if "pids" in missing else NO_LIMIT if pids is None else str(pids),
            "shm": UNREADABLE if shm is None else _size(shm)}
