"""Fixtures of the admin UI tests: the relay-core database fixtures, and a live server on a real port for the browser
tests (`live`). The page tests need a real HTTP server because Chrome loads real URLs; the data is planted through the
same `db` handle the core tests use, and read back through the app."""

import asyncio
import importlib.util
import sys
import threading
from pathlib import Path

import pytest
import uvicorn

CORE_TESTS = Path(__file__).resolve().parents[2] / "packages" / "relay-core" / "tests"
sys.path.insert(0, str(CORE_TESTS))
sys.path.insert(0, str(CORE_TESTS / "api"))

# The core fixtures live in a conftest of another directory; load that file once under its own name.
_spec = importlib.util.spec_from_file_location("relay_core_tests_conftest", CORE_TESTS / "conftest.py")
_core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _core
_spec.loader.exec_module(_core)
_migrated = _core._migrated
db = _core.db
_after_each_db_test = _core._after_each_db_test
pytest_configure = _core.pytest_configure      # registers the `fabricated_rows` marker the invariant checker reads

from apikit import ADMIN_PASSWORD, ADMIN_USER, serving  # noqa: E402  (needs the path set above)

from relay_core.admin import users  # noqa: E402
from relay_core.testing.fake_provider import FAKE_FRAMES, FakeSpec  # noqa: E402


class Live:
    """The app on 127.0.0.1:<port>, running on its own event loop in a thread."""

    def __init__(self, url: str, loop: asyncio.AbstractEventLoop, env):
        self.url, self.loop, self.env = url, loop, env
        self.user, self.password = ADMIN_USER, ADMIN_PASSWORD

    def call(self, coro):
        """Run a coroutine on the server's loop (it owns the pool) and wait for it."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(30)


@pytest.fixture
def framed():
    """Asking for this fixture makes `live` serve a provider that has prompt frames."""
    return True


@pytest.fixture
def live(db, request):
    spec = FakeSpec(frames=FAKE_FRAMES) if "framed" in request.fixturenames else None
    ready = threading.Event()
    box: dict = {}

    async def main():
        async with serving(db, raise_app_exceptions=False, spec=spec) as env:
            await users.create_user(env.pool, ADMIN_USER, ADMIN_PASSWORD)
            server = uvicorn.Server(uvicorn.Config(env.app, host="127.0.0.1", port=0, log_level="warning"))
            task = asyncio.create_task(server.serve())
            while not server.started:
                await asyncio.sleep(0.05)
            port = server.servers[0].sockets[0].getsockname()[1]
            box.update(live=Live("http://127.0.0.1:%d" % port, asyncio.get_running_loop(), env), server=server)
            ready.set()
            await task

    def run():
        try:
            asyncio.run(main())
        except BaseException as exc:      # a start-up failure must reach the test now, not after the wait below
            box["error"] = exc
            ready.set()
            raise

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert ready.wait(60), "the live server did not start"
    if "error" in box:
        raise RuntimeError("the live server died while starting: %r" % (box["error"],)) from box["error"]
    try:
        yield box["live"]
    finally:
        box["server"].should_exit = True
        thread.join(30)
