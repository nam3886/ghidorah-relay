"""Every provider pauses its models with `<p>.disabled_models` (its own catalog ids, nothing else) and marks as basic
the settings its admin changes often: the switch, the prices, the budgets, the pause list, a
browser provider's slots and flow's captcha source."""

import pytest

from relay_core.settings import SettingsError
from relay_dola.spec import SPEC as DOLA
from relay_flow.spec import SPEC as FLOW
from relay_gemini.spec import SPEC as GEMINI
from relay_meta.spec import SPEC as META
from relay_qwen.spec import SPEC as QWEN
from relay_vibes.spec import SPEC as VIBES

BASIC = {
    "dola": {"claims_enabled", "prices", "video_budget", "image_budget", "master_quality", "disabled_models",
             "browser_slots"},
    "flow": {"claims_enabled", "prices", "image_budget", "video_budget", "disabled_models", "captcha_provider"},
    "gemini": {"claims_enabled", "prices", "image_budget", "disabled_models"},
    "meta": {"claims_enabled", "prices", "image_budget", "video_budget", "disabled_models", "browser_slots"},
    "vibes": {"claims_enabled", "prices", "image_budget", "video_budget", "disabled_models"},
    "qwen": {"claims_enabled", "prices", "image_budget", "video_budget", "disabled_models"},
}


@pytest.mark.parametrize("spec", [DOLA, FLOW, GEMINI, META, VIBES, QWEN], ids=lambda s: s.name)
def test_disabled_models_takes_the_catalog_ids_and_the_basic_settings_are_the_everyday_ones(spec):
    settings = spec.settings()
    paused = settings[spec.name + ".disabled_models"]
    ids = [m.id for m in spec.catalog()]
    assert paused.default == [] and paused.validate(ids) == ids
    for bad in (["nope"], [*ids[:1], "nope"], ids[0]):
        with pytest.raises(SettingsError):
            paused.validate(bad)
    assert {k.split(".", 1)[1] for k, s in settings.items() if s.basic} == BASIC[spec.name]
