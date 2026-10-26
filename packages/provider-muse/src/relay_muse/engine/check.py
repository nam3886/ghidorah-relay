"""Account check: muse.ai's session read and the weekly Usage figure, over plain HTTP through the account's proxy.

It spends nothing and opens no tunnel. The session read (`GET /api/session`) is also what keeps the account alive:
muse rotates `hatch_vml` there, and the rotated cookies are written back (web.keep_rotated); the relay is the only
thing that renews a muse session. A 401 is a spaced strike and a rest that asks for a recheck (the strike threshold
ends the account as signed out); an answer with no workspace is an unknown read with no strike; a network failure
raises (the check is recorded as failed). The identity is a hash of the workspace's VM id (one per muse account; the id
itself is never stored).

The Usage figure (web.read_usage) becomes what is left of both pools: none at `muse.usage_stop_percent` or when muse
says the quota is not sufficient, else each pool's weekly budget for the tier in proportion to what is unused, until
the reset muse names. A figure that cannot be read changes nothing.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from relay_core.providers import Balance, Health, Tier
from relay_muse.engine.link import Deps
from relay_muse.engine.submit import signed_out_effects
from relay_muse.engine.web import (
    NetError,
    SignedOut,
    SiteError,
    Usage,
    VmNotReady,
    Web,
    read_session,
    read_usage,
    workspace,
)
from relay_muse.spec import POOLS, WEEK


def identity_of(vm_id: str) -> str:
    return "vm-" + hashlib.sha256(vm_id.encode()).hexdigest()[:16]


def budget(settings, pool: str, tier: str) -> int | None:
    """The pool's weekly budget for the tier (a tier not named takes free's); None when unbound or not set."""
    windows = ((settings.get("muse.pools") or {}).get(pool) or {}).get("windows") or []
    week = next((w for w in windows if w.get("name") == WEEK), None)
    if week is None:
        return None
    budgets = week.get("budget") or {}
    value = budgets[tier] if tier in budgets else budgets.get("free")
    return value if type(value) is int else None


def usage_effects(usage: Usage, settings, tier: str) -> tuple[Any, ...]:
    stop = usage.percent >= settings.get("muse.usage_stop_percent") or not usage.quota_ok
    effects: list[Any] = []
    for pool in (p.name for p in POOLS):
        total = budget(settings, pool, tier)
        if stop:
            effects.append(Balance(pool, 0, usage.resets_at, WEEK))
        elif total is not None:
            effects.append(Balance(pool, total * (100 - usage.percent) // 100, usage.resets_at, WEEK))
    return tuple(effects)


async def check_account(ctx, settings, *, deps: Deps | None = None,
                        now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> Health:
    deps = deps or Deps()
    try:
        async with Web(ctx.account, settings, ctx, make_session=deps.make_session) as web:
            try:
                info = await read_session(web)
            except SignedOut:
                return Health("unknown", effects=signed_out_effects(now()))
            try:
                vm, _ = workspace(info)
            except (VmNotReady, SiteError):
                return Health("unknown", state_patch={"vm_state": str(info.get("status"))[:40]})
            usage = await read_usage(web)
    except (NetError, SiteError) as e:
        raise RuntimeError(str(e)) from None
    patch: dict[str, Any] = {"vm_state": str(info.get("vm_state"))[:40]}
    effects: tuple[Any, ...] = ()
    if usage is not None:
        tier = "pro" if usage.paid else "free"
        patch.update(usage_percent=usage.percent, quota_ok=usage.quota_ok,
                     usage_resets_at=usage.resets_at.isoformat() if usage.resets_at else None)
        effects = (*usage_effects(usage, settings, tier), *((Tier(tier),) if usage.paid is not None else ()))
    return Health("alive", identity=identity_of(vm), state_patch=patch, effects=effects)
