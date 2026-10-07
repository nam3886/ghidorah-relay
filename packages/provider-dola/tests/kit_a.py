"""Helpers for the pure-module tests of the dola provider (spec, catalog, classifier, credential)."""

import pytest

from relay_core.providers import CreateRequest, InputError, MediaIn
from relay_core.testing.fake_provider import FakeSettings
from relay_dola.spec import SPEC

IMG = MediaIn(sha256="0" * 64, mime="image/png", size=10)


def settings(**overrides) -> FakeSettings:
    """The settings view with dola's own defaults; `dola__prices=...` overrides `dola.prices`."""
    return FakeSettings(SPEC, **overrides)


def request(model: str = "seedance-2.5", prompt: str = "a calm sea at dusk", **kw) -> CreateRequest:
    return CreateRequest(model=model, prompt=prompt, **kw)


def plan(model: str = "seedance-2.5", prompt: str = "a calm sea at dusk", *, snapshots=None, cfg=None, **kw):
    return SPEC.plan(request(model, prompt, **kw), cfg or settings(), snapshots or {})


def refused(model: str = "seedance-2.5", prompt: str = "a calm sea at dusk", *, snapshots=None, cfg=None,
            **kw) -> InputError:
    """The InputError a request is refused with (the test fails when it is not refused)."""
    with pytest.raises(InputError) as caught:
        SPEC.plan(request(model, prompt, **kw), cfg or settings(), snapshots or {})
    return caught.value


TEMPLATES = {"categories": {"0": [
    {"index": 0, "category": "Featured", "prompt": "hero section etched engraving",
     "show_value": "hero section etched engraving", "thumbnail_uri": "tos/abc.jpeg",
     "thumbnail_url": "https://example.invalid/t0.jpg", "width": 512, "height": 512},
    {"index": 3, "category": "Poster", "prompt": "retro travel poster", "show_value": "retro travel poster",
     "thumbnail_uri": "tos/def.jpeg", "thumbnail_url": "https://example.invalid/t3.jpg", "width": 512, "height": 768},
]}}
