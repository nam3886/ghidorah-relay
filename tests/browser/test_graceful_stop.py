"""A stop signal reaches the worker only, the worker keeps its display for the whole grace period, and a killed
container comes back with a working display.

Contracts §7.7 / v1.3 #21: `docker stop` must let an in-flight submit finish. The config tests in
tests/test_deploy.py only see strings (`stop_grace_period`, the entrypoint); these run the real image with a
stand-in worker, so they fail for an entrypoint that kills Xvfb or the worker at once.

Three cases:
- graceful stop: the stand-in gets the full grace period with its display alive and exits 0;
- kill, then start (what `restart: unless-stopped` does after a SIGKILL, a daemon restart or a reboot): the
  container's /tmp still holds the dead Xvfb's lock, and the display must still come up;
- negative control: the same stop against the OLD entrypoint (`tini -g -- xvfb-run`, which ships in the image)
  must be cut off at once. If it were not, the timing assertion of the first case would prove nothing.

They drive the docker CLI, not the container's own pytest: `scripts/check-in-container.sh` runs them from the
Mac with DOCKER_HOST=ssh://<box>, with RELAY_STOP_TEST_IMAGE naming the image it built there. The same variable
works against the local daemon for any image with the entrypoint of the `xvfb` Dockerfile stage.
"""

import os
import subprocess
import time
import uuid

import pytest

pytestmark = [pytest.mark.browser, pytest.mark.box]   # the box image itself: never on the Mac (D60)

GRACE_S = 6

# The stand-in worker: it needs a display at start, and on SIGTERM keeps working for GRACE_S seconds, checking
# every 0.2 s that the X display still accepts connections, then exits 0 (a worker that finished its submit).
STAND_IN = """
import os, signal, socket, sys, time
got = []
signal.signal(signal.SIGTERM, lambda *_: got.append(time.time()))
def display_alive():
    s = socket.socket(socket.AF_UNIX)
    try:
        s.connect("/tmp/.X11-unix/X" + os.environ["DISPLAY"].lstrip(":").split(".")[0])
        return True
    except OSError:
        return False
    finally:
        s.close()
assert display_alive(), "no display at start"
print("ready", flush=True)
while not got:
    time.sleep(0.05)
alive = True
while time.time() < got[0] + %d:
    alive = alive and display_alive()
    time.sleep(0.2)
print("display_alive_during_grace=%%s" %% alive, flush=True)
sys.exit(0)
""" % GRACE_S

# The image was built by `docker compose build`, so it carries the labels com.docker.compose.project=relay and
# com.docker.compose.service=<service>, and a plain `docker run` of it inherits them: the stand-in would then look
# like a production container to `docker compose -p relay ps/stop/down`, to box.sh's cap detection and to every
# project filter. These two labels override the inherited ones.
STANDIN_LABELS = ["--label", "com.docker.compose.project=relay-standin",
                  "--label", "com.docker.compose.service=standin"]

OLD_ENTRYPOINT = ["--entrypoint", "tini"]
OLD_COMMAND = ["-g", "--", "xvfb-run", "-a", "--server-args=-screen 0 1440x900x24"]


def docker(*args: str, timeout: int = 120) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=True, timeout=timeout).stdout.strip()


def image() -> str:
    name = os.environ.get("RELAY_STOP_TEST_IMAGE", "")
    assert name, "RELAY_STOP_TEST_IMAGE is not set: run the tier through scripts/check-in-container.sh"
    return name


@pytest.fixture
def start():
    """Start the stand-in in the image (optionally with another entrypoint); remove the containers afterwards."""
    names = []

    def run(old_entrypoint: bool = False) -> str:
        name = "relay-check-stop-%s" % uuid.uuid4().hex[:10]
        label = "relay.check=" + os.environ.get("RELAY_CHECK_TOKEN", "local")
        names.append(name)
        prefix = OLD_ENTRYPOINT if old_entrypoint else []
        suffix = OLD_COMMAND if old_entrypoint else []
        docker("run", "-d", "--name", name, "--label", label, *STANDIN_LABELS, *prefix, image(), *suffix,
               "python", "-c", STAND_IN)
        return name

    yield run
    for name in names:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def wait_ready(name: str, times: int = 1) -> None:
    deadline = time.time() + 60
    while docker("logs", name).split().count("ready") < times:
        assert time.time() < deadline and docker("inspect", "-f", "{{.State.Running}}", name) == "true", \
            "the stand-in worker did not start:\n" + docker("logs", name)
        time.sleep(0.3)


def stop(name: str) -> float:
    started = time.time()
    docker("stop", "-t", str(GRACE_S + 10), name, timeout=GRACE_S + 60)
    return time.time() - started


def assert_graceful(name: str) -> None:
    took = stop(name)
    logs = docker("logs", name)
    assert took >= GRACE_S - 0.5, "docker stop returned after %.1f s: the worker was cut off\n%s" % (took, logs)
    assert "display_alive_during_grace=True" in logs, "Xvfb died during the grace period\n" + logs
    assert docker("inspect", "-f", "{{.State.ExitCode}}", name) == "0", logs


def test_stop_reaches_the_worker_only_and_keeps_the_display_for_the_whole_grace_period(start):
    name = start()
    wait_ready(name)
    assert_graceful(name)


def test_a_killed_container_restarts_with_a_working_display_and_still_stops_gracefully(start):
    name = start()
    wait_ready(name)
    docker("kill", name)
    docker("start", name)
    wait_ready(name, times=2)       # the second "ready" is the restarted stand-in: it asserts the display itself
    assert_graceful(name)


def test_the_old_entrypoint_is_cut_off_at_once(start):
    """Negative control: proves the timing assertion above can fail on this image."""
    name = start(old_entrypoint=True)
    wait_ready(name)
    took = stop(name)
    assert took < 2.0, "the control entrypoint kept the container for %.1f s; the stop test cannot tell" % took
    assert "display_alive_during_grace=True" not in docker("logs", name)


def test_a_running_stand_in_is_never_seen_as_a_production_container(start):
    name = start()
    labels = docker("inspect", "-f", "{{json .Config.Labels}}", name)
    assert '"com.docker.compose.project":"relay-standin"' in labels, labels
    assert '"com.docker.compose.project":"relay"' not in labels, labels
    seen = docker("ps", "-a", "--filter", "label=com.docker.compose.project=relay", "--format", "{{.Names}}")
    assert name not in seen.split(), "the stand-in is listed as project relay:\n" + seen
