"""Named crash and pause points for tests. Env-gated: nothing can arm one unless
`RELAY_FAILPOINTS` is present in the environment, so production code carrying a `hit()` call is inert.

`RELAY_FAILPOINTS="name=action,name=action"`, with the actions
    crash        os._exit(137): the process dies mid-transaction, like a kill -9
    raise        raise FailpointError at that point
    sleep:<s>    wait <s> seconds
In-process tests arm a point with `arm(name, async_callable)`; `arm` refuses when the variable is absent.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable

ENV = "RELAY_FAILPOINTS"


class FailpointError(RuntimeError):
    pass


_armed: dict[str, Callable[[], Awaitable[None]]] = {}
_loaded_from: str | None = None


def _parse(spec: str) -> dict[str, Callable[[], Awaitable[None]]]:
    out: dict[str, Callable[[], Awaitable[None]]] = {}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        name, _, action = item.partition("=")
        out[name.strip()] = _action(name.strip(), action.strip())
    return out


def _action(name: str, action: str) -> Callable[[], Awaitable[None]]:
    async def crash() -> None:
        os._exit(137)

    async def fail() -> None:
        raise FailpointError("failpoint " + name)

    if action == "crash":
        return crash
    if action == "raise":
        return fail
    if action.startswith("sleep:"):
        seconds = float(action.removeprefix("sleep:"))
        return lambda: asyncio.sleep(seconds)
    raise ValueError("unknown failpoint action %r for %s" % (action, name))


def arm(name: str, action: Callable[[], Awaitable[None]]) -> None:
    if os.environ.get(ENV) is None:
        raise RuntimeError("failpoints are off: %s is not set" % ENV)
    _armed[name] = action


def disarm(name: str | None = None) -> None:
    if name is None:
        _armed.clear()
    else:
        _armed.pop(name, None)


async def hit(name: str) -> None:
    """Run the action armed for `name`, if any. The env spec is (re)read when its text changes."""
    global _loaded_from
    spec = os.environ.get(ENV)
    if spec is None:
        return
    if spec != _loaded_from:
        _armed.update(_parse(spec))
        _loaded_from = spec
    action = _armed.get(name)
    if action is not None:
        await action()
