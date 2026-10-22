"""Prompt frames: the text relay sends is one admin-written frame with two
placeholders, rendered once per job at insert.

The pure part (no I/O, no database): the invisible-character rule, the grammar, the renderer and the core frame. The
SQL helpers follow below; the admin routes (`admin/routes_frames.py`) stay thin around them. This module imports no
provider and no other core module that does, so `providers.py` can use the grammar in its registry check.

The invisible-character rule has one copy here, used by the frame grammar and by dola's blank-prompt check. The browser
kit may not import the core and keeps its own copy (`relay_browser.text._invisible`); a dola test pins the two equal
over every code point.
"""

import logging
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from relay_core import failpoints

log = logging.getLogger(__name__)

CLIENT_PROMPT = "{{client_prompt}}"
TRACKING_CODE = "{{tracking_code}}"
PLACEHOLDERS = (CLIENT_PROMPT, TRACKING_CODE)
CORE_FRAME = CLIENT_PROMPT + " " + TRACKING_CODE        # the frame of a provider that declares none
FRAME_MAX = 1000                                        # characters of a stored frame text
KINDS = ("video", "image")

# Characters that change the compared string and draw nothing: every format character (category Cf), the combining
# grapheme joiner, the Hangul fillers (U+3164 and U+FFA0 become U+1160 under NFKC), the variation selectors and the
# tag characters.
_ALWAYS = frozenset({0x034F, 0x115F, 0x1160})
_RANGES = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF), (0xE0000, 0xE007F))
_SLOT = re.compile(r"\{\{[^{}]*\}\}")


def is_invisible(ch: str) -> bool:
    """Does this one character (already NFKC-folded) draw nothing?"""
    cp = ord(ch)
    return cp in _ALWAYS or unicodedata.category(ch) == "Cf" or any(lo <= cp <= hi for lo, hi in _RANGES)


def has_invisible(text: str) -> bool:
    return any(is_invisible(c) for c in unicodedata.normalize("NFKC", text))


def has_surrogate(text: str) -> bool:
    """A lone surrogate (JSON can carry one, UTF-8 cannot): it would crash the first write or encode of the text."""
    return any(0xD800 <= ord(c) <= 0xDFFF for c in text)


def shows_nothing(text: str) -> bool:
    """Only white space and invisible characters (NFKC first, as the page folds): a prompt like that is sent as the
    bare tracking code, which any conversation of the account could be taken for."""
    return all(c.isspace() or is_invisible(c) for c in unicodedata.normalize("NFKC", text or ""))


class FrameError(ValueError):
    """A frame text the grammar refuses. `code` is a platform code (`input.frame_placeholder` or `input.invalid`) and
    `param` the placeholder as written or the missing one (`input.frame_placeholder`), else `text`."""

    def __init__(self, code: str, param: str):
        super().__init__("%s (%s)" % (code, param))
        self.code, self.param = code, param


def check_frame(text: object) -> str:
    """The grammar. Returns the text when it passes; raises `FrameError` and stores nothing otherwise.

    One line of 1 to 1,000 characters with no invisible character; the placeholders `{{client_prompt}}` and
    `{{tracking_code}}`, each exactly once; no other `{{...}}` and no stray `{{` or `}}`. Single braces are text."""
    if not isinstance(text, str) or not text or len(text) > FRAME_MAX or "\r" in text or "\n" in text \
            or has_invisible(text) or has_surrogate(text):
        raise FrameError("input.invalid", "text")
    found = _SLOT.findall(text)
    for slot in found:
        if slot not in PLACEHOLDERS:
            raise FrameError("input.frame_placeholder", slot)
    for slot in PLACEHOLDERS:
        if found.count(slot) > 1:
            raise FrameError("input.frame_placeholder", slot)
    rest = text.replace(CLIENT_PROMPT, "").replace(TRACKING_CODE, "")
    for stray in ("{{", "}}"):
        if stray in rest:
            raise FrameError("input.frame_placeholder", stray)
    for slot in PLACEHOLDERS:
        if slot not in found:
            raise FrameError("input.frame_placeholder", slot)
    return text


def render(frame: str, prompt: str, marker: str) -> str:
    """The text relay sends: the frame with `{{client_prompt}}` replaced by the customer's words and
    `{{tracking_code}}` by `[marker]`, in one pass (a value is never scanned again), then every white-space run
    collapsed to one space and the ends trimmed. The frame is parsed first, so a frame that fails the grammar cannot
    be rendered."""
    check_frame(frame)
    values = {CLIENT_PROMPT: prompt, TRACKING_CODE: "[%s]" % marker}
    return " ".join(_SLOT.sub(lambda m: values[m.group(0)], frame).split())


# ---- the stored versions ------------------------------------------------------------------------
@dataclass(frozen=True)
class Version:
    """One row of `prompt_frames`."""
    id: int
    text: str | None
    created_at: datetime
    created_by: str
    created_by_id: uuid.UUID | None
    restored_from: int | None


@dataclass(frozen=True)
class Effective:
    """The frame a model is sent with: its own newest version when that sets a text, else the kind's newest default.
    `source` says which."""
    version: Version
    source: str          # "own" | "default"


_VERSION = "id, text, created_at, created_by, created_by_id, restored_from"


async def newest(conn, provider: str, kind: str, model: str | None) -> Version | None:
    """The newest version of one target (`model` None = the kind's default)."""
    row = await (await conn.execute(
        "SELECT " + _VERSION + " FROM prompt_frames WHERE provider = %s AND kind = %s "
        "AND model IS NOT DISTINCT FROM %s ORDER BY id DESC LIMIT 1", (provider, kind, model))).fetchone()
    return None if row is None else Version(*row)


async def effective(conn, provider: str, kind: str, model: str) -> Effective | None:
    """The frame a job of this model gets, or None when there is no usable row at all (the caller refuses: nothing
    falls back to the core frame)."""
    own = await newest(conn, provider, kind, model)
    if own is not None and own.text is not None:
        return Effective(own, "own")
    default = await newest(conn, provider, kind, None)
    return None if default is None or default.text is None else Effective(default, "default")


@dataclass(frozen=True)
class By:
    """Who saves: the admin session's user (`created_by_id`, `created_by`), never a request body."""
    id: uuid.UUID | None
    name: str


SYSTEM = By(None, "system")


async def _lock_target(conn, provider: str, kind: str) -> None:
    """The transaction advisory lock for one (provider, kind): seeding, a lock toggle and apply to all take it first,
    so apply to all never writes to a model whose lock committed before it read the locks."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                       ("relay.frames:%s:%s" % (provider, kind),))


async def insert_version(conn, provider: str, kind: str, model: str | None, text: str | None, by: By, *,
                         restored_from: int | None = None) -> Version:
    """Append one version. `text` None (a model only) makes the model follow the default again."""
    row = await (await conn.execute(
        "INSERT INTO prompt_frames (provider, kind, model, text, restored_from, created_by_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING " + _VERSION,
        (provider, kind, model, text, restored_from, by.id, by.name))).fetchone()
    return Version(*row)


async def history(conn, provider: str, kind: str, model: str | None, limit: int, offset: int) -> list[Version]:
    """The versions of one target, newest first."""
    rows = await (await conn.execute(
        "SELECT " + _VERSION + " FROM prompt_frames WHERE provider = %s AND kind = %s "
        "AND model IS NOT DISTINCT FROM %s ORDER BY id DESC LIMIT %s OFFSET %s",
        (provider, kind, model, limit, offset))).fetchall()
    return [Version(*r) for r in rows]


async def version_of(conn, version_id: int) -> tuple[Version, str, str, str | None] | None:
    """(the version, its provider, kind and model), or None."""
    row = await (await conn.execute(
        "SELECT " + _VERSION + ", provider, kind, model FROM prompt_frames WHERE id = %s", (version_id,))).fetchone()
    return None if row is None else (Version(*row[:6]), *row[6:])


@dataclass(frozen=True)
class Lock:
    """One row of `prompt_frame_locks`: a keep-own toggle."""
    locked: bool
    created_at: datetime
    created_by: str
    created_by_id: uuid.UUID | None


_LOCK = "locked, created_at, created_by, created_by_id"


async def lock_history(conn, provider: str, kind: str, model: str, limit: int, offset: int) -> list[Lock]:
    rows = await (await conn.execute(
        "SELECT " + _LOCK + " FROM prompt_frame_locks WHERE provider = %s AND kind = %s AND model = %s "
        "ORDER BY id DESC LIMIT %s OFFSET %s", (provider, kind, model, limit, offset))).fetchall()
    return [Lock(*r) for r in rows]


async def current_locks(conn, provider: str, kind: str) -> dict[str, Lock]:
    """The newest toggle of every model of the kind that has one."""
    rows = await (await conn.execute(
        "SELECT DISTINCT ON (model) model, " + _LOCK + " FROM prompt_frame_locks WHERE provider = %s AND kind = %s "
        "ORDER BY model, id DESC", (provider, kind))).fetchall()
    return {r[0]: Lock(*r[1:]) for r in rows}


async def set_lock(conn, provider: str, kind: str, model: str, locked: bool, by: By) -> tuple[Lock | None, bool]:
    """Toggle the keep-own lock of a model: (its current lock, unchanged). A toggle to the current value adds no row;
    no row at all counts as unlocked, so locking off a never-locked model is unchanged too."""
    await _lock_target(conn, provider, kind)
    current = (await current_locks(conn, provider, kind)).get(model)
    if (current.locked if current else False) == locked:
        return current, True
    row = await (await conn.execute(
        "INSERT INTO prompt_frame_locks (provider, kind, model, locked, created_by_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING " + _LOCK,
        (provider, kind, model, locked, by.id, by.name))).fetchone()
    return Lock(*row), False


async def apply_all(conn, provider: str, kind: str, models: list[str], text: str, by: By
                    ) -> tuple[list[str], list[str], list[str]]:
    """Write `text` as an own version of every model of the kind whose lock is off, in one transaction (a copy of the
    text, not a link to the default). Returns (applied, kept, unchanged): kept = the locked models, unchanged = those
    whose own newest text already equals it. The caller has validated the text."""
    await _lock_target(conn, provider, kind)
    locks = await current_locks(conn, provider, kind)
    applied, kept, unchanged = [], [], []
    for model in models:
        if locks.get(model) and locks[model].locked:
            kept.append(model)
            continue
        own = await newest(conn, provider, kind, model)
        if own is not None and own.text == text:
            unchanged.append(model)
            continue
        await insert_version(conn, provider, kind, model, text, by)
        applied.append(model)
        await failpoints.hit("frames.apply_all.written")
    return applied, kept, unchanged


async def seed(conn, provider: str, defaults: Mapping[str, str]) -> list[str]:
    """`relay serve` start: version 1 of every shipped default that has no row, as 'system', under the (provider, kind)
    lock. After that the database wins. Returns the kinds whose stored default differs from the shipped text; each is
    logged at WARNING at every start."""
    differs = []
    for kind in sorted(defaults):
        await _lock_target(conn, provider, kind)
        current = await newest(conn, provider, kind, None)
        if current is None:
            await insert_version(conn, provider, kind, None, defaults[kind], SYSTEM)
        elif current.text != defaults[kind]:
            differs.append(kind)
            log.warning("the stored default %s frame of %s differs from the shipped one: reset it from the admin if "
                        "the shipped text should apply", kind, provider)
    return differs
