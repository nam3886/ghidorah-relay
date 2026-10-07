"""`run_command` for dola (contracts 5.1, 5.4.1): the four kinds the core hands to the engine.

- `titles`: args `{"refs": [conversation id, ...]}`, result `{"titles": {ref: name}}`. HTTP only, no browser: the core
  calls it with no lease and no browser token.
- `refresh_catalog`: args `{}`, result `{"snapshots": {"templates": ..., "styles": ...}}`. HTTP only.
- `list_conversations`: args `{}`, result `{"conversations": [id, ...]}` (the 30 most recent). Needs the browser (a
  probe inside a tab) and so the core's browser token.
- `recover`: args `{"conversation_id" or "external_ref", ["marker" | "prompt"], ["sent_text"]}`, result
  `{"outputs": [Output, ...]}`. HTTP only.

A failure raises; the core records it as the command's failure and keeps the old snapshot / the job unchanged.
"""

import time
from collections.abc import Callable

from relay_dola.engine import recovery, resolver, templates
from relay_dola.engine.http import DolaHttp
from relay_dola.engine.reconcile import Lister, list_conversation_ids

TITLES_PER_CALL = 50      # dola's own sidebar asks 50 conversations per `batch_get`


class CommandRefused(ValueError):
    """The command's arguments are not enough to run it safely."""


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def titles(http: DolaHttp, refs: list[str]) -> dict:
    """dola's name for each conversation, 50 per call. A call that fails gives no names for its chunk only."""
    found: dict[str, str] = {}
    for chunk in _chunks([str(r) for r in refs], TITLES_PER_CALL):
        found.update(await resolver.conversation_names(http, chunk))
    return {"titles": found}


async def recover(http: DolaHttp, args: dict, now: float) -> dict:
    """The clean videos of a conversation as `Output`s (the core stores them and attaches them to the job).

    Another job's video must never be attached: the conversation has to prove it is the job's. Its own conversation (the
    job's `external_ref`) is the job's by definition; any other conversation needs the job's text in the args."""
    conversation = str(args.get("conversation_id") or args.get("external_ref") or "")
    if not conversation:
        raise CommandRefused("recover needs a conversation id")
    sent_text = args.get("sent_text") or None
    expect = args.get("marker") or args.get("prompt") or sent_text
    own = conversation == str(args.get("external_ref") or "")
    if not own and not expect:
        raise CommandRefused("recover of another conversation needs the job's marker or text to check it")
    outputs = await recovery.recover_outputs(http, conversation, expect=expect, sent_text=sent_text, now=now)
    return {"outputs": list(outputs)}


async def run_command(ctx, kind: str, args: dict, settings, *, http: DolaHttp | None = None,
                      lister: Lister | None = None, clock: Callable[[], float] = time.time) -> dict:
    """Run one command. `http`, `lister` and `clock` are seams for tests; the engine passes none of them."""
    if kind == "list_conversations":
        listing = await (lister or list_conversation_ids)(ctx, "command")
        return {"conversations": list(listing.ids)}
    http = http or DolaHttp.for_account(ctx.account)
    if kind == "titles":
        return await titles(http, list(args.get("refs") or []))
    if kind == "refresh_catalog":
        return {"snapshots": await templates.build_snapshots(http)}
    if kind == "recover":
        return await recover(http, args, clock())
    raise CommandRefused("dola runs no command of kind %r" % kind)
