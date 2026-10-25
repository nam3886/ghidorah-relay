"""Every provider pauses its models with `<p>.disabled_models` (its own catalog ids, nothing else) and marks as basic
the settings its admin changes often: the switch, the prices, the pause list, a
browser provider's slots and flow's captcha sources and their keys. Every default price is its default cost (1:1,
never below 1)."""

import pytest

from relay_core.pricing import prices_at_cost
from relay_core.settings import SettingsError
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC as DOLA
from relay_flow.spec import SPEC as FLOW
from relay_gemini.spec import SPEC as GEMINI
from relay_meta.spec import SPEC as META
from relay_qwen.spec import SPEC as QWEN
from relay_vibes.spec import SPEC as VIBES

BASIC = {
    "dola": {"claims_enabled", "prices", "master_quality", "disabled_models",
             "browser_slots"},
    "flow": {"claims_enabled", "prices", "disabled_models", "captcha_provider", "yescaptcha_key", "capsolver_key",
             "anticaptcha_key"},
    "gemini": {"claims_enabled", "prices", "disabled_models"},
    "meta": {"claims_enabled", "prices", "disabled_models", "browser_slots"},
    "vibes": {"claims_enabled", "prices", "disabled_models"},
    "qwen": {"claims_enabled", "prices", "disabled_models"},
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


@mutation("packages/provider-qwen/src/relay_qwen/catalog.py", "DEFAULT_PRICES = prices_at_cost(DEFAULT_COSTS)",
          'DEFAULT_PRICES = {**prices_at_cost(DEFAULT_COSTS), "qwen-image-3.0-pro": 2}', expect="not 1:1 with its cost")
@mutation("packages/relay-core/src/relay_core/pricing.py", "return {k: max(1, n) for k, n in costs.items()}",
          "return dict(costs)", expect="a free option")
@pytest.mark.parametrize("spec", [DOLA, FLOW, GEMINI, META, VIBES, QWEN], ids=lambda s: s.name)
def test_every_default_price_is_its_default_cost_and_never_below_one_point(spec):
    """Owner 24/10, restated 25/10: 1 upstream credit or use = 1 point, and an option that costs 0 sells at 1."""
    settings = spec.settings()
    prices, costs = settings[spec.name + ".prices"].default, settings[spec.name + ".costs"].default
    assert set(prices) == set(costs), "the price keys are not the cost keys"
    drift = {k: (prices[k], costs[k]) for k in costs if prices[k] != max(1, costs[k])}
    assert not drift, "a default price not 1:1 with its cost (price, cost): %s" % drift
    assert prices_at_cost({"x@5s": 0, "y": 7}) == {"x@5s": 1, "y": 7}, "a free option must still sell at 1 point"
