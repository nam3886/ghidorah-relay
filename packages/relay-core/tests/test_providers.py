"""The protocol types and the registry (contracts section 5.1-5.3): validation at construction, and a catalog whose
model ids and aliases are unique across providers."""

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from relay_core import providers as pv
from relay_core.settings import Spec
from relay_core.testing.mutation import mutation

SRC = "packages/relay-core/src/relay_core/providers.py"


@dataclass
class Stub:
    name: str
    models: list = field(default_factory=list)
    settings_keys: tuple = ()
    caps: pv.Caps = field(default_factory=lambda: caps())
    bare: bool = False          # True: none of the settings the core reads
    frames: pv.FrameRules | None = None
    max_prompt_chars: int = 1000

    def catalog(self):
        return self.models

    def settings(self):
        keys = set(self.settings_keys)
        if not self.bare:
            keys |= {self.name + "." + n for n in ("claims_enabled", "render_deadline_s", "browser_slots")}
        return {k: object() for k in keys}


def caps(**kw):
    base = dict(execution="http", wallet_kinds=("video",), pools=(pv.PoolSpec("video", "UTC"),), account_slots=1,
                max_attempts=3, suspect_threshold=3, strike_effect=pv.Dead("signed_out"))
    return pv.Caps(**{**base, **kw})


def model(mid, *aliases):
    return pv.ModelSpec(mid, "video", ("t2v",), aliases=aliases)


@mutation(SRC, "if name in seen:", "if False:", expect="DID NOT RAISE RegistryError")
def test_a_model_id_used_by_two_providers_refuses():
    reg = pv.ProviderRegistry()
    reg.register(Stub("one", [model("m-1")]))
    with pytest.raises(pv.RegistryError, match="m-1"):
        reg.register(Stub("two", [model("m-1")]))
    assert reg.names() == ["one"]                  # the refused spec was not stored


def test_an_alias_clashing_with_another_providers_id_refuses():
    reg = pv.ProviderRegistry()
    reg.register(Stub("one", [model("m-1")]))
    with pytest.raises(pv.RegistryError, match="m-1"):
        reg.register(Stub("two", [model("m-2", "m-1")]))


def test_a_duplicate_inside_one_provider_refuses():
    with pytest.raises(pv.RegistryError):
        pv.check_catalog([Stub("one", [model("m-1"), model("m-2", "m-1")])])


def test_resolve_finds_the_owner_by_id_or_alias_and_none_otherwise():
    reg = pv.ProviderRegistry()
    reg.register(Stub("one", [model("m-1", "short")]))
    reg.register(Stub("two", [model("m-2")]))
    assert reg.resolve("short")[0].name == "one"
    assert reg.resolve("m-2")[0].name == "two"
    assert reg.resolve("nope") is None


def test_a_provider_name_is_registered_once_and_must_be_well_formed():
    reg = pv.ProviderRegistry()
    reg.register(Stub("one"))
    with pytest.raises(pv.RegistryError, match="already"):
        reg.register(Stub("one"))
    with pytest.raises(pv.RegistryError, match="lower case"):
        reg.register(Stub("Bad-Name"))


@mutation(SRC, "if not k.startswith(spec.name", "if False and not k.startswith(spec.name",
           expect="DID NOT RAISE RegistryError")
def test_settings_must_live_in_the_providers_namespace():
    with pytest.raises(pv.RegistryError, match="namespace"):
        pv.ProviderRegistry().register(Stub("one", settings_keys=("one.ok", "other.bad")))


def test_a_pool_budget_setting_must_be_declared():
    c = caps(pools=(pv.PoolSpec("video", "UTC", "one.budget"),))
    with pytest.raises(pv.RegistryError, match="one.budget"):
        pv.ProviderRegistry().register(Stub("one", caps=c))
    pv.ProviderRegistry().register(Stub("one", caps=c, settings_keys=("one.budget",)))


@pytest.mark.parametrize("kw", [
    dict(execution="gpu"), dict(wallet_kinds=()), dict(account_slots=0), dict(max_attempts=0),
    dict(suspect_threshold=0), dict(strike_effect="dead"),
    dict(pools=(pv.PoolSpec("a", "UTC"), pv.PoolSpec("a", "UTC"))),
    dict(proxy_rest_s=0), dict(check_interval_s=0),
])
def test_caps_refuse_nonsense(kw):
    with pytest.raises(ValueError):
        caps(**kw)


def test_a_pool_with_an_unknown_timezone_refuses_at_construction():
    with pytest.raises(Exception):  # noqa: B017  (ZoneInfoNotFoundError or ValueError, depending on the platform)
        pv.PoolSpec("video", "Mars/Olympus")


def test_results_validate_what_the_core_relies_on():
    with pytest.raises(ValueError):
        pv.Rest("video", datetime(2030, 1, 1), "naive time")
    with pytest.raises(ValueError):
        pv.Balance("video", 3, "guessed")
    with pytest.raises(ValueError):
        pv.Completed(outputs=())
    with pytest.raises(ValueError):
        pv.Fail("")
    with pytest.raises(ValueError):
        pv.Health("maybe")
    pv.Cooldown("throttled", datetime(2030, 1, 1, tzinfo=UTC))
    assert pv.SubmitResult(pv.Accepted("ref")).job.taken is True


def test_the_spec_constants_default_to_the_contract_values():
    c = caps()
    assert (c.proxy_rest_s, c.check_interval_s) == (1800, 21600)


@mutation(SRC, "    if missing:", "    if False:", expect="DID NOT RAISE RegistryError")
def test_a_spec_must_declare_the_settings_the_core_reads():
    bare = Stub("one", bare=True)
    with pytest.raises(pv.RegistryError, match="one.claims_enabled.*one.render_deadline_s"):
        pv.ProviderRegistry().register(bare)
    # browser_slots is only needed by a browser provider
    only_two = Stub("one", bare=True, settings_keys=("one.claims_enabled", "one.render_deadline_s"))
    pv.ProviderRegistry().register(only_two)
    with pytest.raises(pv.RegistryError, match="one.browser_slots"):
        pv.ProviderRegistry().register(Stub("one", bare=True, caps=caps(execution="browser"),
                                            settings_keys=("one.claims_enabled", "one.render_deadline_s")))


class SlotsStub(Stub):
    """A browser provider whose browser_slots spec carries the given bounds."""

    def __init__(self, *, kind=int, default=2, **bounds):
        super().__init__("one", caps=caps(execution=bounds.pop("execution", "browser")))
        self.kind, self.default, self.bounds = kind, default, bounds

    def settings(self):
        return {**super().settings(),
                "one.browser_slots": Spec("one.browser_slots", self.kind, self.default, "slots", **self.bounds)}


@mutation(SRC, "if not _bounded_slots(slots):", "if False:", expect="DID NOT RAISE")
def test_a_browser_provider_must_bound_its_slots_between_one_and_a_maximum():
    for bounds in ({}, {"min": 1}, {"max": 12}, {"min": 0, "max": 12}):
        with pytest.raises(pv.RegistryError, match="one.browser_slots must be an int setting that declares min >= 1"):
            pv.ProviderRegistry().register(SlotsStub(**bounds))
    pv.ProviderRegistry().register(SlotsStub(min=1, max=12))


@mutation(SRC, "math.isfinite(v)", "True", expect="OverflowError")
def test_the_slot_setting_must_be_an_int_with_whole_finite_bounds():
    for bad in (SlotsStub(kind=float, default=2.0, min=1, max=12), SlotsStub(min=1, max=math.inf),
                SlotsStub(min=1, max=12.5), SlotsStub(min=1, max=math.nan), SlotsStub(min=1.5, max=12),
                SlotsStub(min=True, max=12), SlotsStub(min=1, max=True)):
        with pytest.raises(pv.RegistryError, match="finite whole max"):
            pv.ProviderRegistry().register(bad)
    pv.ProviderRegistry().register(SlotsStub(min=1, max=12.0))


@mutation(SRC, "slots.validate(slots.default)", "pass", expect="DID NOT RAISE")
def test_a_slot_default_outside_its_own_bounds_is_a_refusal_not_a_traceback():
    with pytest.raises(pv.RegistryError, match=r"one.browser_slots default: .*between 1 and 12"):
        pv.ProviderRegistry().register(SlotsStub(default=50, min=1, max=12))


def test_an_http_provider_needs_no_slot_bounds():
    pv.ProviderRegistry().register(SlotsStub(execution="http"))


# ---- prompt frames in the registry check (contracts 5.1) -------------------------------------------------------
CORE_FRAME = "{{client_prompt}} {{tracking_code}}"


def rules(**kw):
    base = dict(defaults={"video": CORE_FRAME}, warnings=lambda kind, text: (), max_sent_chars=100)
    return pv.FrameRules(**{**base, **kw})


@mutation(SRC, "for kind in sorted(kinds - set(rules.defaults)):", "for kind in []:",
          expect="a missing default was accepted")
@mutation(SRC, "            frames.check_frame(text)\n", "            pass\n",
          expect="a default that fails the grammar was accepted")
@mutation(SRC, "        if kind not in frames.KINDS:", "        if False:",
          expect="a default for an unknown kind was accepted")
@mutation(SRC, "rules.max_sent_chars < 1", "rules.max_sent_chars < 0", expect="a zero length cap was accepted")
def test_a_spec_with_frames_must_ship_a_valid_default_for_every_kind_of_its_catalog():
    reg = pv.ProviderRegistry()
    reg.register(Stub("framed", [model("f-1")], frames=rules()))
    reg.register(Stub("plain", [model("g-1")]))                     # no frames: the core frame applies
    bad = {
        "a missing default was accepted": rules(defaults={}),
        "a default that fails the grammar was accepted": rules(defaults={"video": "no placeholders"}),
        "a default for an unknown kind was accepted": rules(defaults={"video": CORE_FRAME, "audio": CORE_FRAME}),
        "a zero length cap was accepted": rules(max_sent_chars=0),
    }
    for why, broken in bad.items():
        with pytest.raises(pv.RegistryError):
            pv.ProviderRegistry().register(Stub("x", [model("x-1")], frames=broken))
            pytest.fail(why)


@mutation(SRC, "    if not isinstance(cap, int) or isinstance(cap, bool) or cap < 1:", "    if False:",
          expect="a prompt cap that is not a positive whole number was accepted")
def test_a_spec_must_declare_a_positive_whole_prompt_cap():
    pv.ProviderRegistry().register(Stub("fine", [model("f-1")]))
    for cap in (0, -5, 2.5, True, None, "50000"):
        with pytest.raises(pv.RegistryError):
            pv.ProviderRegistry().register(Stub("x", [model("x-1")], max_prompt_chars=cap))
            pytest.fail("a prompt cap that is not a positive whole number was accepted: %r" % (cap,))
    with pytest.raises(pv.RegistryError):
        pv.ProviderRegistry().register(NoCap("y", [model("y-1")]))
        pytest.fail("a spec with no prompt cap was accepted")


class NoCap(Stub):
    def __getattribute__(self, attr):
        if attr == "max_prompt_chars":
            raise AttributeError(attr)
        return super().__getattribute__(attr)
