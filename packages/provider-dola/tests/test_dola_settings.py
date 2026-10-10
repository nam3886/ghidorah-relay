"""dola's live settings and their validators (contracts 5.4.1; coverage CFG07, CFG08, PRICE00, RUN00)."""

import pytest
from kit_a import settings

from relay_core.providers import ProviderRegistry
from relay_core.settings import Registry, SettingsError, is_boot_only
from relay_core.testing.mutation import mutation
from relay_dola import catalog as C
from relay_dola.spec import SPEC

CONSTANTS_FILE = "packages/provider-dola/src/relay_dola/constants.py"
SPEC_FILE = "packages/provider-dola/src/relay_dola/spec.py"

LIVE_KEYS = {
    "dola.claims_enabled", "dola.render_deadline_s", "dola.prices",
    "dola.video_budget",
    "dola.image_budget", "dola.master_quality", "dola.alert_duration_mismatch_rate", "dola.alert_watermark_only_rate",
    "dola.alert_content_refused_rate", "dola.browser_slots"}


SLOTS = "BROWSER_SLOTS_MIN, BROWSER_SLOTS_MAX = 1, 30"
SLOTS_99 = "BROWSER_SLOTS_MIN, BROWSER_SLOTS_MAX = 1, 99"


def spec_of(key: str):
    return SPEC.settings()[key]


def test_the_live_keys_are_exactly_the_contract_list_all_namespaced():
    assert set(SPEC.settings()) == LIVE_KEYS
    assert all(k.startswith("dola.") for k in SPEC.settings())
    assert not any(is_boot_only(k) for k in SPEC.settings()), "an upstream URL or proxy must never be a live setting"


def test_the_defaults_are_the_contract_values_and_pass_their_own_validators():
    d = {k: s.default for k, s in SPEC.settings().items()}
    assert (d["dola.render_deadline_s"], d["dola.video_budget"], d["dola.image_budget"]) == (9000, 4, 40)
    assert d["dola.master_quality"] is True and d["dola.claims_enabled"] is True
    assert (d["dola.alert_duration_mismatch_rate"], d["dola.alert_watermark_only_rate"],
            d["dola.alert_content_refused_rate"]) == (0.2, 0.2, 0.5)
    # a fresh registry validates every default and refuses a boot-only name or a duplicate
    Registry(*SPEC.settings().values())
    for s in SPEC.settings().values():
        s.validate(s.default)
    registry = ProviderRegistry()
    # the keys the core reads on every tick (claims_enabled, render_deadline_s, browser_slots)
    registry.register(SPEC)


def test_the_video_prompt_hint_setting_is_gone_its_sentence_lives_in_the_video_frame():
    assert "dola.video_prompt_hint" not in SPEC.settings()
    with pytest.raises(SettingsError):
        Registry().spec("dola.video_prompt_hint")


# ---- CFG07: prices ---------------------------------------------------------------------------------------------------
def test_the_old_estimate_table_cannot_come_back_as_a_setting():
    for gone in ("dola.credit_estimate", "dola.profiles", "dola.key_default_quota", "dola.video_points_per_day",
                 "dola.image_points_per_day", "dola.browser_workers", "dola.prompt_marker_enabled",
                 "dola.max_queued_per_key", "dola.resolve_deadline_h"):
        assert gone not in SPEC.settings(), "%s is gone: it was either never read or is now a core setting" % gone
    with pytest.raises(SettingsError):
        Registry().spec("dola.credit_estimate")


@mutation(SPEC_FILE, "if type(price) is not int:", "if False:", expect="TypeError")
def test_a_wrong_typed_price_table_is_refused_naming_the_fault():
    s = spec_of("dola.prices")
    tier = "seedance_v2.5@30s"
    for bad, why in (({tier: "two"}, "is not a whole number"), ({tier: 2.5}, "whole number"),
                     ({tier: True}, "whole number"), ({tier: None}, "whole number"),
                     ({"": 1}, "empty name"), ({"  ": 1}, "empty name")):
        with pytest.raises(SettingsError, match=why):
            s.validate(bad)
    for bad in ([1, 2], "x", 5, None):
        with pytest.raises(SettingsError, match="must be dict"):
            s.validate(bad)
    assert s.validate({}) == {}, "an empty table is allowed: every tier then takes the fallback with a warning"


@mutation(SPEC_FILE, "        if price < 0:", "        if False:", expect="DID NOT RAISE")
def test_a_negative_price_is_refused():
    with pytest.raises(SettingsError, match="negative"):
        spec_of("dola.prices").validate({"seedance_v2.5@30s": -1})
    assert spec_of("dola.prices").validate({"seedance_v2.5@30s": 0}) == {"seedance_v2.5@30s": 0}


def test_prices_come_from_the_environment_as_json_and_a_bad_one_is_refused():
    s = spec_of("dola.prices")
    assert s.env_name == "DOLA_PRICES"
    assert s.from_env('{"seedance_v2.5@30s": 3}') == {"seedance_v2.5@30s": 3}
    with pytest.raises(SettingsError):
        s.from_env('{"seedance_v2.5@30s": -3}')
    with pytest.raises(SettingsError):
        s.from_env("not json")


def test_an_invalid_price_value_never_reaches_the_running_table():
    # Settings.set validates BEFORE it writes (relay_core.settings, tested there): a refused value leaves the stored
    # table as it was. The validator is what decides, so it is proven here with the table at hand.
    cfg = settings()
    running = dict(cfg.get("dola.prices"))
    with pytest.raises(SettingsError):
        spec_of("dola.prices").validate({**running, "seedance_v2.5@30s": -1})
    assert cfg.get("dola.prices") == running == C.DEFAULT_PRICES


# ---- CFG08: browser slots --------------------------------------------------------------------------------------------
@mutation(CONSTANTS_FILE, SLOTS, SLOTS_99, expect="did not match")
def test_browser_slot_values_stay_inside_the_measured_range():
    s = spec_of("dola.browser_slots")
    assert s.default == 8 and s.validate(1) == 1 and s.validate(25) == 25 and s.validate(30) == 30
    for bad in (0, 31, -1):
        with pytest.raises(SettingsError, match="between 1 and 30"):
            s.validate(bad)
    for bad in (True, 8.0, "8"):
        with pytest.raises(SettingsError, match="must be int"):
            s.validate(bad)


# ---- the other validators --------------------------------------------------------------------------------------------



@mutation(SPEC_FILE, "if not 0.0 <= v <= 1.0:", "if False:", expect="DID NOT RAISE")
def test_the_alert_rates_are_between_zero_and_one():
    for key in ("dola.alert_duration_mismatch_rate", "dola.alert_watermark_only_rate",
                "dola.alert_content_refused_rate"):
        s = spec_of(key)
        assert s.validate(0.0) == 0.0 and s.validate(1.0) == 1.0
        for bad in (-0.1, 1.1):
            with pytest.raises(SettingsError, match="between 0 and 1"):
                s.validate(bad)
        with pytest.raises(SettingsError, match="must be float"):
            s.validate(1)


def test_the_deadline_and_the_budgets_are_positive_whole_numbers():
    for key, floor in (("dola.render_deadline_s", 60), ("dola.video_budget", 1), ("dola.image_budget", 1)):
        s = spec_of(key)
        assert s.validate(floor) == floor
        with pytest.raises(SettingsError, match="at least"):
            s.validate(floor - 1)
        with pytest.raises(SettingsError, match="must be int"):
            s.validate(True)
    assert spec_of("dola.master_quality").validate(False) is False
    assert spec_of("dola.claims_enabled").validate(False) is False


# ---- RUN00 (dola part): the production path scales slots -----------------------------------------
def test_the_settings_a_worker_reloads_each_tick_exist_with_the_core_names():
    keys = set(SPEC.settings())
    assert {"dola.claims_enabled", "dola.render_deadline_s", "dola.browser_slots"} <= keys, (
        "the core reads these three on every tick")
    for pool in SPEC.caps.pools:
        assert pool.budget_setting in keys, pool
    assert [(p.name, p.tz, p.budget_setting) for p in SPEC.caps.pools] == [
        ("video", "UTC", "dola.video_budget"), ("image", "UTC", "dola.image_budget")]
