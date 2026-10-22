"""The browser tier's container: its cgroup (v2) limits can be read, so the admin Containers card never says
"unreadable" for a worker that runs in the same image (the premise of reporting them at all)."""

import pytest

from relay_core import cgroup

pytestmark = [pytest.mark.browser, pytest.mark.box]   # the box image itself: never on the Mac


def test_the_container_limits_are_readable_inside_the_browser_image():
    got = cgroup.own_limits()
    assert got["read"] is True, "cgroup v2 limits are not readable here: %r" % got
    for name in ("cpus", "memory_bytes", "pids", "shm_bytes"):
        assert got[name] is None or isinstance(got[name], int | float), (name, got)
    assert isinstance(got["shm_bytes"], int) and got["shm_bytes"] > 0, got
    print("container limits: %r" % got)
