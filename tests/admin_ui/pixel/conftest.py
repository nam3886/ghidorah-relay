"""Fixtures of the pixel harness: a renderer for the canvas boards and the folder a run writes into."""

import os

import pytest
from pixel_render import Renderer

SHOTS_ENV = "RELAY_SCREENSHOTS_DIR"      # the folder the browser tier copies back (/shots in the container)


@pytest.fixture(autouse=True)
def frozen_server_clock(monkeypatch):
    """The live server runs in this process (a thread, see `live`), so its one Python clock, the hourly band's `_now`,
    can be set to the clock the boards assume. The SQL windows use the database's `clock_timestamp()` and cannot be
    moved: a seed plants the rows a "24 h" count reads relative to the real now, and the rows whose time the page
    shows at their absolute sample time."""
    from datetime import UTC, datetime

    from pixel_seed import NOW

    from relay_core.admin import routes_stats
    frozen = datetime.fromisoformat(NOW).astimezone(UTC)
    monkeypatch.setattr(routes_stats, "_now", lambda: frozen)
    return frozen


@pytest.fixture(autouse=True)
def provider_named_like_the_boards(monkeypatch):
    """The boards show the provider "dola" (alert, provider list, pool). The live server registers one FakeSpec, which
    is named "fake" unless told otherwise, so every FakeSpec built while a pixel test runs is named after the boards."""
    from pixel_seed import PROVIDER

    from relay_core.testing.fake_provider import FakeSpec
    original = FakeSpec.__init__

    def named(self, name: str = PROVIDER, *args, **kwargs):
        original(self, name, *args, **kwargs)

    monkeypatch.setattr(FakeSpec, "__init__", named)


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    """Where this run's images and report go: `<RELAY_SCREENSHOTS_DIR>/pixel` when set, else a temp folder."""
    from pathlib import Path
    base = os.environ.get(SHOTS_ENV)
    return Path(base) / "pixel" if base else tmp_path_factory.mktemp("pixel")


@pytest.fixture(scope="module")
def renderer(chrome):
    """One page that renders the canvas boards, with nothing but local files behind it."""
    context = chrome.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light",
                                 timezone_id="Asia/Saigon")
    try:
        yield Renderer(context)
    finally:
        context.close()


@pytest.fixture
def diff_page(renderer):
    """A blank page of the renderer's context, to run the diff script in."""
    page = renderer.context.new_page()
    try:
        yield page
    finally:
        page.close()
