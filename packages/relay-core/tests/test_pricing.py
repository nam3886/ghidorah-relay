"""The core prices a job (`relay_core.pricing`): the sale price from `<p>.prices`, the upstream cost from `<p>.costs`,
both by the price key the provider's plan names; no fallback price, no guessed cost, and nothing a client sends."""

import pytest

from relay_core import errors, pricing
from relay_core.providers import CreateRequest
from relay_core.settings import SettingsError
from relay_core.testing.fake_provider import FakeSettings, FakeSpec
from relay_core.testing.mutation import mutation

PRICING = "packages/relay-core/src/relay_core/pricing.py"
SPEC = FakeSpec()


def plan(model="fake-video", **options):
    return SPEC.plan(CreateRequest(model=model, prompt="a cat", options=options), FakeSettings(SPEC), {})


def priced(model="fake-video", cfg=None, **options):
    return pricing.price_plan(SPEC, plan(model, **options), cfg or FakeSettings(SPEC))


@mutation(PRICING, "    if price is None:\n        raise Unpriced(", "    if False:\n        raise Unpriced(",
          expect="DID NOT RAISE")
def test_a_missing_sale_price_refuses_model_unpriced():
    with pytest.raises(pricing.Unpriced) as caught:
        priced(cfg=FakeSettings(SPEC, fake__prices={"fake-image": 1}))
    assert caught.value.code == "model.unpriced" and "fake-video" in str(caught.value)
    assert errors.rule("model.unpriced").http_status == 403 and not errors.rule("model.unpriced").job_scoped


@mutation(PRICING, "    if cost is None:\n        raise Uncosted(", "    if False:\n        raise Uncosted(",
          expect="DID NOT RAISE")
def test_a_missing_upstream_cost_refuses_model_uncosted():
    with pytest.raises(pricing.Uncosted) as caught:          # an image: its pool has no budget to compare with
        priced("fake-image", cfg=FakeSettings(SPEC, fake__costs={"fake-video": 1}))
    assert caught.value.code == "model.uncosted"
    assert errors.rule("model.uncosted").http_status == 403 and not errors.rule("model.uncosted").job_scoped


def test_the_core_fills_price_and_cost_from_the_settings_and_never_from_the_plan_or_the_options():
    cfg = FakeSettings(SPEC, fake__prices={"fake-video": 7, "fake-image": 1}, fake__costs={"fake-video": 3,
                                                                                         "fake-image": 1})
    p = priced(cfg=cfg, price=999, account_cost=50)          # the fake's plan carries these: the core replaces them
    assert (p.price, p.account_cost, p.price_key) == (7, 3, "fake-video")
    free = priced(cfg=FakeSettings(SPEC, fake__prices={"fake-video": 0, "fake-image": 1}))
    assert free.price == 0, "0 points is a price the owner may set"


@mutation(PRICING, "    if budget is not None and cost > budget:", "    if False:", expect="DID NOT RAISE")
def test_a_cost_above_the_pool_budget_is_too_costly():
    """No account could ever take it: refused at the door instead of queued until the unsent deadline."""
    with pytest.raises(pricing.TooCostly) as caught:
        priced(cfg=FakeSettings(SPEC, fake__costs={"fake-video": 5, "fake-image": 1}))   # no tier holds more than 4
    assert caught.value.code == "input.too_costly"
    assert priced(cfg=FakeSettings(SPEC, fake__costs={"fake-video": 4, "fake-image": 1})).account_cost == 4
    # a pool without a budget setting is unknown: any cost passes
    assert priced("fake-image", cfg=FakeSettings(SPEC, fake__costs={"fake-video": 1, "fake-image": 99})).account_cost \
        == 99


TABLE = pricing.table_setting("p.prices", "Points.", ("a", "b@5s", "c@480p", "c@720p", "d"), {"a": 1, "b@5s": 2},
                              unit="points", renamed={"b": ("b@5s",), "c": ("c@480p", "c@720p")})


@mutation(PRICING, "            if k not in known:", "            if False:", expect="DID NOT RAISE")
def test_an_unknown_key_in_a_table_is_refused_on_write():
    with pytest.raises(SettingsError, match="not an option"):
        TABLE.validate({"a": 1, "c": 3})
    assert TABLE.validate({"a": 0}) == {"a": 0}


@mutation(PRICING, "            if type(n) is not int:", "            if False:", expect="DID NOT RAISE")
@mutation(PRICING, "            if n < 0:", "            if False:", expect="DID NOT RAISE")
def test_a_negative_or_fractional_entry_is_refused():
    for bad in ({"a": 1.5}, {"a": True}, {"a": "2"}, {"a": None}):
        with pytest.raises(SettingsError, match="whole number"):
            TABLE.validate(bad)
    with pytest.raises(SettingsError, match="negative"):
        TABLE.validate({"a": -1})
    assert TABLE.allowed == "each entry: an option key of this provider and a whole number of points, 0 or more"


@mutation(PRICING, "        stored = {k: n for k, n in v.items() if k in known}", "        stored = dict(v)",
          expect="retired option")
def test_a_stored_table_drops_a_retired_option_and_keeps_the_rest():
    """A model retired since the owner priced it must not make the whole stored table invalid (every price would
    fall back to the defaults): its entry is dropped on load, the others keep their stored values."""
    loaded = TABLE.on_load({"a": 9, "retired": 3})
    assert loaded == {"a": 9, "b@5s": 2}, "a retired option was kept: %s" % loaded
    assert TABLE.validate(loaded) == loaded


@mutation(PRICING, "        return {**default, **stored}", "        return stored", expect="keeps its default")
def test_a_stored_table_is_read_over_the_defaults_key_by_key():
    """An option the stored table does not name keeps its shipped default: a key added by a later build, or one the
    owner never wrote, is never refused for want of a stored entry. A stored value wins over its default."""
    assert TABLE.on_load({"a": 9}) == {"a": 9, "b@5s": 2}, "an option the stored table does not name keeps its default"
    assert TABLE.on_load({}) == {"a": 1, "b@5s": 2}
    assert TABLE.default == {"a": 1, "b@5s": 2}, "the default table was mutated"


@mutation(PRICING, "                    stored.setdefault(new, v[old])", "                    pass",
          expect="carries over")
@mutation(PRICING, "                    stored.setdefault(new, v[old])", "                    stored[new] = v[old]",
          expect="the new key wins")
def test_a_key_an_earlier_build_stored_carries_its_value_to_the_key_that_replaced_it():
    """A model that gained a length or a resolution in its key: the stored price of the old key moves to the new
    key(s), unless the stored table already names the new key itself; the old key is never kept."""
    loaded = TABLE.on_load({"b": 7, "c": 3})
    assert loaded == {"a": 1, "b@5s": 7, "c@480p": 3, "c@720p": 3}, "the old value carries over: %s" % loaded
    assert TABLE.on_load({"b": 7, "b@5s": 4}) == {"a": 1, "b@5s": 4}, "the new key wins over the old one"
    assert TABLE.validate(loaded) == loaded


def test_a_table_declares_its_option_keys_for_the_settings_page():
    assert TABLE.keys == ("a", "b@5s", "c@480p", "c@720p", "d")


def test_the_catalog_shows_each_options_live_price_and_null_when_it_has_none():
    m = SPEC.catalog()[0]
    assert pricing.option_prices(SPEC, m, FakeSettings(SPEC)) == {"default": 10}
    assert pricing.option_prices(SPEC, m, FakeSettings(SPEC, fake__prices={"fake-image": 1})) == {"default": None}
    assert TABLE.on_load({})["a"] == 1 and "d" not in TABLE.on_load({}), "an option with no default stays unpriced"
