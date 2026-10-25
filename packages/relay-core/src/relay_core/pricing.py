"""Job pricing: the core, not the provider, puts the price and the upstream cost on a plan.

A provider's `plan()` names a price key (the option the job is: `seedance_v2.5@10s`, `qwen-image-3.0`). The sale price
in points is `<p>.prices[key]` and the upstream cost, in the unit of the job's pool, is `<p>.costs[key]`; both are
settings the owner edits. A key missing from either table refuses the job at the door (`model.unpriced`,
`model.uncosted`): there is no fallback price and no guessed cost. A cost above the pool's budget refuses it too
(`input.too_costly`): no account could ever take it. No provider is named here.
"""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from relay_core.pool import ceiling, pool_configs
from relay_core.providers import ModelSpec, Plan, ProviderSpec
from relay_core.settings import Rule, Spec

log = logging.getLogger(__name__)


class PricingRefused(Exception):
    """A plan the core cannot price; `code` is the platform code the door answers with."""

    code = "internal.unexpected"


class Unpriced(PricingRefused):
    code = "model.unpriced"


class Uncosted(PricingRefused):
    code = "model.uncosted"


class TooCostly(PricingRefused):
    code = "input.too_costly"


def prices_at_cost(costs: Mapping[str, int]) -> dict[str, int]:
    """The default sale price of every option, from its default upstream cost (owner 24/10, restated 25/10): one point
    per upstream credit or use, and an option that costs nothing upstream still sells at 1 point."""
    return {k: max(1, n) for k, n in costs.items()}


def table_setting(key: str, description: str, keys: Iterable[str], default: Mapping[str, int], *, unit: str,
                  basic: bool = False, renamed: Mapping[str, tuple[str, ...]] | None = None) -> Spec:
    """A table of option key -> whole number of `unit`, 0 or more (`<p>.prices` in points, `<p>.costs` in upstream
    units). A key the provider does not offer is refused on a write.

    A stored table is read OVER the shipped defaults, key by key: an option the stored table does not name keeps its
    default, so an option added by a later build (or a key the owner never wrote) is never refused for want of a stored
    entry. `renamed` maps a key an earlier build stored to the key(s) that replace it (a model that gained a length or a
    resolution in its key); its value carries over unless the stored table names the new key itself. Any other key the
    provider no longer offers is dropped and logged, so a retired option keeps none of its old entries."""
    known = tuple(keys)
    renamed = dict(renamed or {})

    def check(v: dict) -> None:
        for k, n in v.items():
            if k not in known:
                raise ValueError("%r is not an option of this provider" % (k,))
            if type(n) is not int:
                raise ValueError("the entry for %s is not a whole number: %r" % (k, n))
            if n < 0:
                raise ValueError("the entry for %s is negative" % k)

    def upgrade(v: Any) -> Any:
        if type(v) is not dict:
            return v                    # validate refuses it
        stored = {k: n for k, n in v.items() if k in known}
        for old, new_keys in renamed.items():
            if old in v:
                for new in new_keys:
                    stored.setdefault(new, v[old])
        retired = [k for k in v if k not in known and k not in renamed]
        if retired:
            log.error("stored setting %s: dropped keys that are no option: %s", key, ", ".join(map(repr, retired)))
        return {**default, **stored}

    return Spec(key, dict, dict(default), description,
                rule=Rule("each entry: an option key of this provider and a whole number of %s, 0 or more" % unit,
                          check), basic=basic, on_load=upgrade, keys=known)


def _budget(spec: ProviderSpec, pool: str | None, settings: Any) -> int | None:
    """The most an account can ever hold in the pool (`<p>.pools`, the largest budget of any tier), or None (unknown)
    for a pool with no budget."""
    if pool is None:
        return None
    cfg = pool_configs(spec.name, spec.caps, settings.get).get(pool)
    return None if cfg is None else ceiling(cfg)


def price_plan(spec: ProviderSpec, plan: Plan, settings: Any) -> Plan:
    """The plan with the core's `price` (points) and `account_cost` (upstream units) from the provider's tables.
    Raises Unpriced, Uncosted or TooCostly; never prices from anything a client sent."""
    price = settings.get(spec.name + ".prices").get(plan.price_key)
    if price is None:
        raise Unpriced("%s has no price for %s" % (spec.name, plan.price_key))
    cost = settings.get(spec.name + ".costs").get(plan.price_key)
    if cost is None:
        raise Uncosted("%s has no upstream cost for %s" % (spec.name, plan.price_key))
    budget = _budget(spec, plan.pool, settings)
    if budget is not None and cost > budget:
        raise TooCostly("%s %s costs %d, a %s account holds at most %d" % (
            spec.name, plan.price_key, cost, plan.pool, budget))
    return replace(plan, price=price, account_cost=cost)


def option_prices(spec: ProviderSpec, model: ModelSpec, settings: Any) -> dict[str, int | None]:
    """A catalog entry's live prices: option label -> points, None when the option has no price (refused)."""
    table = settings.get(spec.name + ".prices")
    return {label: table.get(key) for label, key in model.price_keys.items()}
