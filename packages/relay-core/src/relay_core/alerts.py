"""Stored alerts: the unclear replies a provider could not classify, and the read side that lists every stored alert
kind as one hub.

Two halves, both free of any provider knowledge:

- the writer side: `build` turns the reply text into the row (clean, redact, cut, wording, group id) and `store` inserts
  it inside the caller's transaction, in a savepoint, so a report can never block the result or the failure it rides on;
- the read side: `SOURCES` lists each stored kind with the SQL that groups its rows into the one shape `G`, and the
  list, detail and acknowledge functions the admin routes call. A new kind is one more entry of `SOURCES`.

`billing_alerts` keeps its own table and routes (money code writes it); the hub reads it side by side.
"""

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple

from psycopg import AsyncConnection

from relay_core import frames
from relay_core.jobs.events import add_event
from relay_core.redact import redact_text

log = logging.getLogger(__name__)

REPLY_LIMIT = 2000
TITLE_LIMIT = 200
EMPTY_TITLE = "(no reply text read: the provider's API gave no text)"
GROUP_LEN = 16
KIND_UNCLEAR = "unclear_reply"
BILLING_KINDS = ("clamp", "drift")


# ---- the writer side ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Report:
    """A row to store: the cleaned, redacted and cut text, its normalized wording and the group they make."""
    text: str
    wording: str
    group_id: str


def clean(text: str) -> str:
    """Lone surrogates become `?` (they cannot be written to the database), NUL is removed (a token split by a NUL
    would otherwise slip past the redaction patterns), then NFKC (a full-width secret label folds to the ASCII one)."""
    return unicodedata.normalize("NFKC", text.encode("utf-8", "replace").decode("utf-8").replace("\x00", ""))


_MARKDOWN = re.compile(r"[*_`~]")
# Typographic quotes, primes, accents and dashes to their straight forms (NFKC leaves them). The browser kit keeps the
# same map for its own comparisons and may not import the core, so a dola test pins the two equal over every code point.
TYPOGRAPHIC = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2032": "'", "\u2033": '"', "\u00b4": "'", "\u0060": "'",
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2026": "...",
})
_URL = re.compile(r"(?:https?://|www\.)\S+")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_HEX_RUN = re.compile(r"(?<![0-9a-z])[0-9a-f]{8,}(?![0-9a-z])")
_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")


def wording_of(text: str) -> str:
    """The text that makes a group: what is the same in every occurrence of one reply. In this order, so a UUID never
    becomes `#` fragments: case folded, markdown emphasis characters removed, curly quotes made straight, URLs `<url>`,
    UUIDs and hex runs of 8 or more `#`, digit runs (with `.`, `,` and `:` inside) `#`, white space one space, trimmed.
    Generic: it knows no provider's wording."""
    out = _MARKDOWN.sub("", text.casefold()).translate(TYPOGRAPHIC)
    out = _URL.sub("<url>", out)
    out = _HEX_RUN.sub("#", _UUID.sub("#", out))
    return " ".join(_NUMBER.sub("#", out).split())


def group_of(provider: str, wording: str) -> str:
    return hashlib.sha256((provider + "\n" + wording).encode("utf-8")).hexdigest()[:GROUP_LEN]


class JobTexts(NamedTuple):
    """What the job was sent: the customer's words and the whole text relay sent (frame, words and tracking code)."""
    prompt: str
    sent_text: str | None


PROMPT_MARK = "<prompt>"
ECHO_FLOOR = 10            # folded characters: a shorter prompt or line is too common a word to take out of a reply


def _fold(text: str) -> str:
    """The comparison form: NFKC, invisible characters dropped, quotes, primes, accents and dashes straight, white
    space one space, case folded (`Stra\u00dfe` and `STRASSE` are the same word)."""
    out = "".join(c for c in unicodedata.normalize("NFKC", text) if not frames.is_invisible(c))
    return " ".join(out.translate(TYPOGRAPHIC).split()).casefold()


def _is_echo_line(line: str, folded_sent: str) -> bool:
    folded = _fold(line)
    return len(folded) >= ECHO_FLOOR and folded in folded_sent


def strip_echo(text: str, texts: JobTexts) -> str:
    """Take the customer's own words out of a reply (providers restate prompts). A line of at least 10 folded
    characters that is wholly inside the sent text is removed. Then, for a prompt of at least 10 folded characters,
    every occurrence of it at word boundaries becomes `<prompt>`: found on the folded reply, so a different white
    space, case, dash or quote does not hide it, and the stored text is then the folded one. A reply that only repeats
    the prompt keeps that marker, so it is a group of its own and not the read-failure group. `text` is already
    cleaned (NFKC)."""
    folded_sent = _fold(texts.sent_text or "")
    if folded_sent:
        kept = [ln for ln in text.splitlines() if not _is_echo_line(ln, folded_sent)]
        if any(ln.strip() for ln in kept):
            text = "\n".join(kept)
    words = _fold(texts.prompt)
    if len(words) < ECHO_FLOOR:
        return text
    folded = _fold(text)
    pattern = r"(?<!\w)" + re.escape(words) + r"(?!\w)"
    replaced = re.sub(pattern, PROMPT_MARK, folded)
    return replaced if replaced != folded else text


def build(provider: str, text: str, texts: JobTexts | None = None) -> Report:
    """Clean, take out the customer's echoed words (when the job's texts are known), then redact (never cut: a cut
    could leave half a secret), then cut to 2,000 characters; the wording and the group come from the stored text,
    so a group key never derives from a secret."""
    if not isinstance(text, str):
        raise TypeError("an unclear reply is a str")
    cleaned = clean(text)
    if texts is not None:
        cleaned = strip_echo(cleaned, texts)
    stored = redact_text(cleaned)[:REPLY_LIMIT]
    wording = wording_of(stored)
    return Report(stored, wording, group_of(provider, wording))


def prepare(job_id, provider: str, text: str | None, texts: JobTexts | None = None) -> Report | None:
    """`build` that never raises: a report that cannot be built is dropped and logged with the job id and the class of
    the error only, because the text is the one thing that must not reach a log. None: nothing to store."""
    if text is None:
        return None
    try:
        return build(provider, text, texts)
    except Exception as exc:     # noqa: BLE001 - the result or the failure this rides on must go through
        log.error("unclear reply dropped: job=%s error=%s", job_id, type(exc).__name__)
        return None


async def job_texts(conn: AsyncConnection, job_id) -> JobTexts | None:
    """The job's prompt and sent text (frozen at insert), or None for a job that is gone."""
    row = await (await conn.execute("SELECT prompt, sent_text FROM jobs WHERE id = %s", (job_id,))).fetchone()
    return None if row is None else JobTexts(row[0], row[1])


async def report_for(conn: AsyncConnection, job_id, provider: str, text: str) -> Report | None:
    """The report row for a result that carries an unclear reply, inside the result's transaction. The job's texts are
    read in a savepoint of their own: a failing read rolls back to it, is logged with the job id, the class and the
    sqlstate only (any `Exception`, not only a database error; cancellation still propagates), and drops the report;
    the result goes on. The builder never raises (`prepare`)."""
    try:
        async with conn.transaction():
            texts = await job_texts(conn, job_id)
    except Exception as exc:     # noqa: BLE001 - a report never costs its result
        log.error("unclear reply dropped: job=%s error=%s sqlstate=%s", job_id, type(exc).__name__,
                  getattr(exc, "sqlstate", None) or "unknown")
        return None
    return prepare(job_id, provider, text, texts)


# The conflict names no target on purpose: naming the columns makes Postgres demand SELECT on them (probed on the box:
# 42501), and the worker has INSERT only. The only unique key besides the primary key is (job_id, group_id).
_INSERT = ("INSERT INTO unclear_replies (provider, job_id, account_id, text, wording, group_id) "
           "SELECT provider, id, account_id, %s, %s, %s FROM jobs WHERE id = %s "
           "ON CONFLICT DO NOTHING")


async def store(conn: AsyncConnection, job_id, report: Report) -> None:
    """Insert the report in the caller's transaction, inside a savepoint. Any error (not only a database one;
    cancellation still propagates) rolls back to it and is logged with the job id, the class and the sqlstate only
    (psycopg's message carries the server's DETAIL, which can be the failing row, so no `str(exc)` and no traceback);
    a `system` job event records the loss, guarded the same way, and the caller's transaction goes on. One row per
    job and wording: a second report of the same words is skipped by the unique key."""
    try:
        async with conn.transaction():
            await conn.execute(_INSERT, (report.text, report.wording, report.group_id, job_id))
    except Exception as exc:     # noqa: BLE001 - a report never costs its result or failure
        state = getattr(exc, "sqlstate", None) or "unknown"
        log.error("unclear reply not stored: job=%s error=%s sqlstate=%s", job_id, type(exc).__name__, state)
        await _note_not_stored(conn, job_id, state)


async def _note_not_stored(conn: AsyncConnection, job_id, state: str) -> None:
    """The job event that records the loss, in a savepoint of its own and guarded like the insert: a failing
    diagnostic (a statement timeout, say) is logged and never rolls back the caller's transaction."""
    try:
        async with conn.transaction():
            await add_event(conn, job_id, "system", "unclear_not_stored", "unclear reply not stored: %s" % state)
    except Exception as exc:     # noqa: BLE001
        log.error("unclear reply note not stored: job=%s error=%s", job_id, type(exc).__name__)


# ---- the read side -----------------------------------------------------------------------------------------------
# Every source selects the same columns, one row per group: kind, grp, severity, title, provider, first_at, last_at,
# count, open_count, keys, newest_id, reply (an unclear group's newest stored text, cut like the title; else NULL) and
# key_label (a billing group's key: its label, else its prefix; else NULL).
GROUP_COLUMNS = ("kind", "grp", "severity", "title", "provider", "first_at", "last_at", "count", "open_count", "keys",
                 "newest_id", "reply", "key_label")
RANK = "CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"      # explicit, never the name's order
# A key's name wherever the hub shows one (`k` is api_keys): its label, else its prefix, as the Jobs list names a key.
KEY_NAME = "COALESCE(NULLIF(k.label, ''), k.key_prefix)"


@dataclass(frozen=True)
class Source:
    """One stored alert kind (or the kinds of one table) and the SQL that groups its rows."""
    kinds: tuple[str, ...]
    groups: str


# One group per (alert, key, scope): the key's points wallet (`*`, no provider) or one provider's cap.
BILLING = Source(BILLING_KINDS, """
    SELECT alert AS kind, key_id::text || '.' || scope AS grp, 'error' AS severity,
           alert || ': ' || CASE WHEN scope = '*' THEN 'points wallet' ELSE scope || ' cap' END || ' of key '
           || key_id::text AS title, CASE WHEN scope = '*' THEN '' ELSE scope END AS provider,
           min(at) AS first_at, max(at) AS last_at, count(*) AS count,
           count(*) FILTER (WHERE acknowledged_at IS NULL) AS open_count, 1 AS keys, max(id) AS newest_id,
           NULL::text AS reply,
           (SELECT """ + KEY_NAME + """ FROM api_keys k WHERE k.id = billing_alerts.key_id) AS key_label
      FROM billing_alerts WHERE alert IN ('clamp', 'drift') GROUP BY alert, key_id, scope""")

UNCLEAR = Source((KIND_UNCLEAR,), """
    SELECT 'unclear_reply' AS kind, u.group_id AS grp, 'warning' AS severity,
           CASE WHEN max(u.wording) = '' THEN %(empty_title)s
                ELSE left(max(u.wording), %(title_limit)s) END AS title,
           u.provider, min(u.at) AS first_at, max(u.at) AS last_at, count(*) AS count,
           count(*) FILTER (WHERE u.acknowledged_at IS NULL) AS open_count,
           count(DISTINCT j.key_id) AS keys, max(u.id) AS newest_id,
           (SELECT left(t.text, %(title_limit)s) FROM unclear_replies t WHERE t.id = max(u.id)) AS reply,
           NULL::text AS key_label
      FROM unclear_replies u JOIN jobs j ON j.id = u.job_id GROUP BY u.group_id, u.provider""")

SOURCES = (BILLING, UNCLEAR)


def kinds(sources=None) -> tuple[str, ...]:
    return tuple(k for s in (sources or SOURCES) for k in s.kinds)


def _groups_sql(where: str, sources) -> str:
    union = " UNION ALL ".join("(%s)" % s.groups for s in sources)
    return ("SELECT " + ", ".join(GROUP_COLUMNS) + " FROM (" + union + ") g" + where
            + " ORDER BY (open_count > 0) DESC, " + RANK + ", last_at DESC, newest_id DESC")


def _ms(moment) -> int:
    return int(moment.timestamp() * 1000)


def group_view(row: tuple) -> dict:
    """One group in the common shape `G`."""
    g = dict(zip(GROUP_COLUMNS, row, strict=True))
    return {"kind": g["kind"], "group": g["grp"], "severity": g["severity"], "title": g["title"],
            "provider": g["provider"], "first_ms": _ms(g["first_at"]), "last_ms": _ms(g["last_at"]),
            "count": g["count"], "open_count": g["open_count"], "keys": g["keys"], "newest_id": g["newest_id"],
            "reply": g["reply"], "key_label": g["key_label"],
            "status": "open" if g["open_count"] > 0 else "acknowledged",
            "detail": "/admin/alerts/groups/%s/%s" % (g["kind"], g["grp"])}


async def _groups(conn, where: str, params: dict, limit: int | None, sources) -> list[dict]:
    sql = _groups_sql(where, sources or SOURCES) + ("" if limit is None else " LIMIT %(limit)s")
    params = {"empty_title": EMPTY_TITLE, "title_limit": TITLE_LIMIT, **params, "limit": limit}
    return [group_view(r) for r in await (await conn.execute(sql, params)).fetchall()]


async def list_groups(conn, *, status: str, kind: str | None, limit: int, sources=None) -> list[dict]:
    """The groups of every stored kind: open first, then the severity rank, then the newest."""
    where = [] if status == "all" else ["open_count %s 0" % (">" if status == "open" else "=")]
    params: dict = {}
    if kind is not None:
        where.append("kind = %(kind)s")
        params["kind"] = kind
    return await _groups(conn, (" WHERE " + " AND ".join(where)) if where else "", params, limit, sources)


async def get_group(conn, kind: str, group: str, sources=None) -> dict | None:
    rows = await _groups(conn, " WHERE kind = %(kind)s AND grp = %(group)s", {"kind": kind, "group": group}, 1, sources)
    return rows[0] if rows else None


_BILLING_GROUP = re.compile(_UUID.pattern + r"\.(?:\*|[a-z][a-z0-9_]*)")
_UNCLEAR_GROUP = re.compile(r"[0-9a-f]{16}")


def group_form(kind: str, group: str) -> bool:
    """Is `group` written the way a group of this kind is: a malformed one is a 404, never a database error."""
    if kind in BILLING_KINDS:
        return _BILLING_GROUP.fullmatch(group) is not None
    return kind == KIND_UNCLEAR and _UNCLEAR_GROUP.fullmatch(group) is not None


# billing items: the shape `GET /admin/alerts` shows. Defined here so the hub does not import a route module (a test
# keeps the two equal).
BILLING_COLUMNS = ("id", "at", "alert", "key_id", "scope", "ledger_id", "amount", "used_day", "used_total",
                   "detail", "acknowledged_at", "acknowledged_by")


def billing_item(row: tuple) -> dict:
    item = dict(zip(BILLING_COLUMNS, row, strict=True))
    item["at_ms"] = _ms(item.pop("at"))
    acknowledged = item.pop("acknowledged_at")
    item["acknowledged_ms"] = None if acknowledged is None else _ms(acknowledged)
    item["key_id"] = None if item["key_id"] is None else str(item["key_id"])
    return item


def _billing_key(group: str) -> tuple[str, str]:
    key_id, scope = group.split(".")
    return key_id, scope


def _shown_title() -> str:
    # imported here: at module level it is a cycle (titles -> checks -> runctx -> store -> apply -> alerts)
    from relay_core.jobs.titles import SHOWN_TITLE
    return SHOWN_TITLE


async def billing_items(conn, kind: str, group: str, limit: int, offset: int) -> list[dict]:
    key_id, scope = _billing_key(group)
    # The key's name and the job (through the ledger row the alert points at) ride along so the page can name them;
    # an alert with no ledger row, or a deleted key, has null names. The ledger row and the job must belong to the
    # alert's own key: a row of another key is never named here.
    rows = await (await conn.execute(
        "SELECT " + ", ".join("b." + c for c in BILLING_COLUMNS) + ", " + KEY_NAME + ", j.id, " + _shown_title() + " "
        "FROM billing_alerts b LEFT JOIN api_keys k ON k.id = b.key_id "
        "LEFT JOIN ledger l ON l.id = b.ledger_id AND l.key_id = b.key_id "
        "LEFT JOIN jobs j ON j.id = l.job_id AND j.key_id = b.key_id "
        "WHERE b.alert = %s AND b.key_id = %s AND b.scope = %s "
        "ORDER BY b.id DESC LIMIT %s OFFSET %s",
        (kind, key_id, scope, limit, offset))).fetchall()
    n = len(BILLING_COLUMNS)
    return [{**billing_item(r[:n]), "key_label": r[n], "job_id": None if r[n + 1] is None else str(r[n + 1]),
             "job_title": r[n + 2]} for r in rows]


async def unclear_items(conn, group: str, limit: int, offset: int) -> list[dict]:
    rows = await (await conn.execute(
        "SELECT u.id, u.at, u.job_id, u.account_id, a.label, j.key_id, u.text, j.status, j.error_code, "
        "u.acknowledged_at, u.acknowledged_by, " + _shown_title() + ", " + KEY_NAME + " FROM unclear_replies u "
        "JOIN jobs j ON j.id = u.job_id "
        "LEFT JOIN accounts a ON a.id = u.account_id LEFT JOIN api_keys k ON k.id = j.key_id "
        "WHERE u.group_id = %s ORDER BY u.id DESC LIMIT %s OFFSET %s",
        (group, limit, offset))).fetchall()
    return [{"id": r[0], "at_ms": _ms(r[1]), "job_id": str(r[2]), "account_id": None if r[3] is None else str(r[3]),
             "account_label": r[4], "key_id": str(r[5]), "text": r[6], "job_status": r[7], "job_error_code": r[8],
             "acknowledged_ms": None if r[9] is None else _ms(r[9]), "acknowledged_by": r[10], "job_title": r[11],
             "key_label": r[12]} for r in rows]


async def unclear_outcomes(conn, group: str) -> dict:
    """How the group's jobs stand now, over distinct jobs: `done`, `failed` (refunded) and still open, and the
    error codes of the failed ones. They tell the admin whether the wording was an acceptance or a refusal.
    `media` is the one media type of all the group's jobs (`video`, `image`), or null when they are mixed: the reply
    is stored as that video's or that image's answer."""
    rows = await (await conn.execute(
        "SELECT j.status, j.error_code, j.media_type, count(*) FROM jobs j WHERE j.id IN "
        "(SELECT job_id FROM unclear_replies WHERE group_id = %s) GROUP BY j.status, j.error_code, j.media_type",
        (group,))).fetchall()
    outcomes, codes, media = {"done": 0, "failed": 0, "open": 0}, {}, set()
    for status, code, kind, n in rows:
        outcomes["done" if status == "done" else "failed" if status == "failed" else "open"] += n
        if status == "failed":
            codes[code] = codes.get(code, 0) + n
        media.add(kind)
    return {"outcomes": outcomes, "failed_codes": codes, "media": media.pop() if len(media) == 1 else None}


async def acknowledge(conn, kind: str, group: str, through_id: int, user: str) -> int:
    """Stamp the open rows of one group up to `through_id` (for billing, of that alert kind only) with the session user
    and the time. Rows that arrived later stay open; a stamped row is never touched (the filter, and for unclear
    replies the trigger). One INFO line records who, which group and how many: no text."""
    if kind in BILLING_KINDS:
        key_id, scope = _billing_key(group)
        cur = await conn.execute(
            "UPDATE billing_alerts SET acknowledged_at = clock_timestamp(), acknowledged_by = %s WHERE alert = %s "
            "AND key_id = %s AND scope = %s AND id <= %s AND acknowledged_at IS NULL",
            (user, kind, key_id, scope, through_id))
    else:
        cur = await conn.execute(
            "UPDATE unclear_replies SET acknowledged_at = clock_timestamp(), acknowledged_by = %s "
            "WHERE group_id = %s AND id <= %s AND acknowledged_at IS NULL", (user, group, through_id))
    log.info("alert group acknowledged: user=%s kind=%s group=%s through_id=%s count=%s", user, kind, group,
             through_id, cur.rowcount)
    return cur.rowcount
