"""Recover: take the clean videos of ONE dola conversation (the admin's manual action; from dola_v4 `recovery.py`).

A conversation can hold a paid order that no job points to any more (25/09: four conversations, five 30 s videos,
dropped when an extra send hit "daily limit"). Recover reads the conversation through the real poll path: the job's
marker guard stops it attaching another job's video, every clean video in it is taken, a watermarked one is not.

What changed from v4: this only READS and returns `Output`s (the core fetches, probes and stores them, dedupes by
`upstream_id`, attaches them, re-charges a refunded job, and turns a failed job into done).
"""

from relay_core.providers import Output
from relay_dola.engine import resolver
from relay_dola.engine.http import DolaHttp


class RecoveryError(RuntimeError):
    """Nothing could be recovered from this conversation (the command fails with this text)."""


async def recover_outputs(http: DolaHttp, conversation_id: str, *, expect: str | None, sent_text: str | None,
                          now: float) -> tuple[Output, ...]:
    """The clean videos of `conversation_id`, in message order.

    `expect` (the job's marker, else its prompt) is the guard v4 used: judged once the user's words are readable.
    `sent_text`, when given, is stricter: the user's words must carry the whole text that was sent, and an unreadable
    conversation is refused (Recover moves money, so it needs positive proof)."""
    try:
        body = await resolver.fetch_chain(http, conversation_id, expect)
    except resolver.ChainError as exc:
        raise RecoveryError("%s (%s)" % (exc, exc.code)) from exc
    if sent_text and not resolver.carries_text(resolver.user_text(body), sent_text):
        raise RecoveryError("the conversation does not carry the job's text: it is not this job's")
    try:
        read = resolver.read_video(body, since=None, now=now)
    except resolver.ChainError as exc:
        raise RecoveryError("%s (%s)" % (exc, exc.code)) from exc
    clean = [c for c in read.ready if c.clean]
    if not clean:
        raise RecoveryError("the conversation holds no clean video (%d still rendering, %d with a watermark only)"
                            % (read.pending, len(read.ready)))
    return resolver.video_outputs(clean, conversation_id)
