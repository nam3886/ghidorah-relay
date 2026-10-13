"""Unclear replies (contracts 4.8, 5.3, 7.1, 13): the builder and its wording, the type that carries the
text, both writers (a submit result and a render-deadline failure) under the worker role with their failure windows, the
trigger that freezes a stamp, and the absence of the text from every public surface.

A report never blocks what it rides on: a builder error drops it, a database error rolls back to a savepoint. Neither
log line ever holds the text.
"""

import logging

import psycopg
import pytest
from accountkit import run_as
from jobkit import CAPS, POOLS, account, claim, create, key, run, submit
from lifecyclekit import age, reaper, row, to_waiting

from relay_core import alerts, failpoints
from relay_core.jobs import store
from relay_core.leases import LeaseLost
from relay_core.providers import Accepted, SubmitResult
from relay_core.testing.fake_provider import FakeEngine, FakeSpec, sent
from relay_core.testing.mutation import mutation

ALERTS = "packages/relay-core/src/relay_core/alerts.py"
APPLY = "packages/relay-core/src/relay_core/jobs/apply.py"
REAPER = "packages/relay-core/src/relay_core/jobs/reaper.py"
PROVIDERS = "packages/relay-core/src/relay_core/providers.py"
MIGRATION = "packages/relay-core/src/relay_core/db/migrations/0015_unclear_replies.sql"
# past the render deadline, with the final read made (the reaper waits for one)
PAST_DEADLINE = ("UPDATE jobs SET render_since = clock_timestamp() - interval '9100 seconds', "
                 "answered_at = clock_timestamp() WHERE id = %s")

STORE_CALL = ("            if report is not None:\n                await alerts.store(conn, job.id, report)\n"
              '                await failpoints.hit("apply.unclear_reported")')
STORE_NEVER = ("            if False:\n                await alerts.store(conn, job.id, report)\n"
               '                await failpoints.hit("apply.unclear_reported")')
STORED = "    stored = redact_text(cleaned)[:REPLY_LIMIT]\n"
CUT_FIRST = "    stored = redact_text(cleaned[:REPLY_LIMIT])\n"
NOT_CLEANED = "    cleaned = clean(text)\n"
NOT_CLEANED_AT_ALL = "    cleaned = text\n"
LOG_LINE = ('        log.error("unclear reply not stored: job=%s error=%s sqlstate=%s", job_id, '
            "type(exc).__name__, state)")
LOG_EXC = ('        log.error("unclear reply not stored: job=%s error=%s sqlstate=%s", job_id, '
           "str(exc), state)")
BUILDER_CATCH = ("    except Exception as exc:     # noqa: BLE001 - "
                 "the result or the failure this rides on must go through")
BUILDER_NARROW = ("    except KeyError as exc:     # noqa: BLE001 - "
                  "the result or the failure this rides on must go through")
TRIGGER = ("CREATE TRIGGER unclear_replies_stamp BEFORE UPDATE ON unclear_replies\n"
           "    FOR EACH ROW EXECUTE FUNCTION unclear_replies_stamp();")
HOOK_GUARD = ('        except Exception as exc:     # noqa: BLE001\n'
              '            log.error("%s: unclear_at_deadline failed')
HOOK_NARROW = ('        except KeyError as exc:     # noqa: BLE001\n'
               '            log.error("%s: unclear_at_deadline failed')
MARKER = "PLANTED-MARKER-7f3a"
LOG = "relay_core.alerts"


# ---- the wording ------------------------------------------------------------------------------------------------
@mutation(ALERTS, "    return \" \".join(_NUMBER.sub(\"#\", out).split())", "    return \" \".join(out.split())",
          expect="digit runs are one wording")
def test_digit_runs_make_one_wording_whatever_the_number():
    assert alerts.wording_of("Your 30-second video [4821] needs 2 credits at 12:30") == \
        "your #-second video [#] needs # credits at #", "digit runs are one wording"
    assert alerts.wording_of("It costs 1,250.5 points") == "it costs # points", "digit runs are one wording"
    assert alerts.wording_of("video [4821]") == alerts.wording_of("video [77]"), "digit runs are one wording"


@mutation(ALERTS, "    out = _URL.sub(\"<url>\", out)\n", "    out = out\n", expect="a url is one wording")
def test_urls_make_one_wording():
    assert alerts.wording_of("See https://www.dola.com/chat/123?x=1 or www.example.org/a.") == "see <url> or <url>", \
        "a url is one wording"
    assert alerts.wording_of("go to https://a.example/1") == alerts.wording_of("go to https://b.example/2?q"), \
        "a url is one wording"


@mutation(ALERTS, "    out = _HEX_RUN.sub(\"#\", _UUID.sub(\"#\", out))\n", "    out = out\n",
          expect="ids are one wording")
def test_uuids_and_hex_runs_make_one_wording_and_a_uuid_never_becomes_fragments():
    one = "job 123e4567-e89b-12d3-a456-426614174000 failed"
    assert alerts.wording_of(one) == "job # failed", "ids are one wording"
    assert alerts.wording_of("token deadbeef01234567 here") == "token # here", "ids are one wording"
    assert alerts.wording_of("a face of cafe") == "a face of cafe", "a short hex-looking word stays"


def test_case_markdown_quotes_and_white_space_do_not_make_a_new_wording():
    said = alerts.wording_of("**Sorry**,\u2003I can\u2019t   do \u201cTHAT\u201d\n\nnow ")
    assert said == "sorry, i can't do \"that\" now"
    assert alerts.wording_of("") == "" and alerts.wording_of("  \n ") == ""


def test_the_same_words_under_another_provider_are_another_group_and_digits_do_not_split_a_group():
    a, b = alerts.build("dola", "Order [4821] is in."), alerts.build("dola", "Order [77] is in.")
    other = alerts.build("meta", "Order [4821] is in.")
    assert a.group_id == b.group_id and a.group_id != other.group_id
    assert len(a.group_id) == alerts.GROUP_LEN and set(a.group_id) <= set("0123456789abcdef")
    assert alerts.build("dola", "").wording == ""


# ---- the builder ------------------------------------------------------------------------------------------------
def test_a_lone_surrogate_becomes_a_question_mark_and_a_nul_is_removed():
    built = alerts.build("dola", "bad \ud800 text\x00 end")
    assert built.text == "bad ? text end"


@mutation(ALERTS, STORED, "    stored = cleaned[:REPLY_LIMIT]\n",
          expect="a planted secret was stored")
def test_a_planted_secret_is_redacted_and_the_stored_text_is_at_most_2000_characters():
    secret = "rk-" + "a1B2" * 8
    built = alerts.build("dola", "dola said hello. Authorization: Bearer %s and token=%s" % (secret, "Z" * 30))
    assert secret not in built.text and "Z" * 30 not in built.text, "a planted secret was stored"
    assert alerts.build("dola", "x " * 2500).text == ("x " * 2500)[:alerts.REPLY_LIMIT]


@mutation(ALERTS, STORED, CUT_FIRST,
          expect="half a secret was stored")
def test_a_secret_that_straddles_the_cut_is_redacted_whole_not_cut_in_half():
    text = "x " * 990 + "S" * 60
    built = alerts.build("dola", text)
    assert len(built.text) <= alerts.REPLY_LIMIT
    assert "S" * 8 not in built.text, "half a secret was stored"
    labelled = alerts.build("dola", "x " * 985 + "token=" + "S" * 60)
    assert "S" * 8 not in labelled.text and len(labelled.text) <= alerts.REPLY_LIMIT


@mutation(ALERTS, NOT_CLEANED, NOT_CLEANED_AT_ALL,
          expect="a secret split by a NUL was stored")
def test_a_secret_split_by_a_nul_or_written_full_width_is_redacted():
    split = "note " + "S" * 30 + "\x00" + "S" * 30 + " end"
    assert "S" * 20 not in alerts.build("dola", split).text, "a secret split by a NUL was stored"
    wide = "ｔｏｋｅｎ=" + "Ｓ" * 12                     # full-width "token=SSSS..."
    assert "S" * 6 not in alerts.build("dola", wide).text, "a full-width secret was stored"


def test_a_value_that_is_not_text_is_refused_and_the_prepare_step_drops_it_without_the_text(caplog):
    with pytest.raises(TypeError):
        alerts.build("dola", b"bytes")        # type: ignore[arg-type]
    with caplog.at_level(logging.ERROR, logger=LOG):
        assert alerts.prepare("job-1", "dola", b"bytes") is None       # type: ignore[arg-type]
        assert alerts.prepare("job-1", "dola", None) is None
    assert any("job-1" in r.getMessage() and "TypeError" in r.getMessage() for r in caplog.records)


# ---- the customer's own words ------------------------------------------------------------------------------------
ECHO_RULE = "    return replaced if replaced != folded else text"
ECHO_KEPT = "    return text"
FOLD_LINE = '    return " ".join(out.translate(TYPOGRAPHIC).split()).casefold()'
FOLD_LOWER = '    return " ".join(out.translate(TYPOGRAPHIC).split()).lower()'
FOLD_NO_DASH = '    return " ".join(out.translate({}).split()).casefold()'
FLOOR_RULE = "    if len(words) < ECHO_FLOOR:"
FLOOR_GONE = "    if not words:"
BOUNDARY_RULE = '    pattern = r"(?<!\\w)" + re.escape(words) + r"(?!\\w)"'
BOUNDARY_GONE = "    pattern = re.escape(words)"
LINE_FLOOR_RULE = "    return len(folded) >= ECHO_FLOOR and folded in folded_sent"
LINE_FLOOR_GONE = "    return bool(folded) and folded in folded_sent"
TEXTS_PASSED = "built = self._unclear(job_id, text, state, alerts.JobTexts(prompt, sent_text))"
TEXTS_DROPPED = "built = self._unclear(job_id, text, state, None)"
READ_GUARD = "    except Exception as exc:     # noqa: BLE001 - a report never costs its result\n"
READ_GUARD_NARROW = "    except KeyError as exc:     # noqa: BLE001 - a report never costs its result\n"
STORE_GUARD = "    except Exception as exc:     # noqa: BLE001 - a report never costs its result or failure\n"
STORE_GUARD_NARROW = "    except KeyError as exc:     # noqa: BLE001 - a report never costs its result or failure\n"
NOTE_SAVEPOINT = ('        async with conn.transaction():\n            await add_event(conn, job_id, "system", '
                  '"unclear_not_stored"')
NOTE_NO_SAVEPOINT = ('        if True:\n            await add_event(conn, job_id, "system", '
                     '"unclear_not_stored"')
READ_SAVEPOINT = "    try:\n        async with conn.transaction():\n            texts = await job_texts(conn, job_id)"
READ_NO_SAVEPOINT = "    try:\n        if True:\n            texts = await job_texts(conn, job_id)"
STORE_SAVEPOINT = "        async with conn.transaction():\n            await conn.execute(_INSERT,"
STORE_NO_SAVEPOINT = "        if True:\n            await conn.execute(_INSERT,"
LINE_RULE = '        if any(ln.strip() for ln in kept):\n            text = "\\n".join(kept)'
LINE_KEPT = '        if False:\n            text = "\\n".join(kept)'
ONLY_PROMPT = "        if any(ln.strip() for ln in kept):"
ONLY_PROMPT_DROPPED = "        if True:"


def texts_of(prompt, marker="4821"):
    from relay_core import frames
    return alerts.JobTexts(prompt, frames.render(frames.CORE_FRAME, prompt, marker))


@mutation(ALERTS, ECHO_RULE, ECHO_KEPT, expect="the customer's words made a group of their own")
def test_the_prompt_inside_a_sentence_becomes_a_marker_so_two_customers_share_one_group():
    one, other = "a red kite over the sea", "A dog\u2019s  day at the park"
    a = alerts.build("dola", "You asked: %s. Hmm, I am not sure." % one, texts_of(one))
    b = alerts.build("dola", "You asked: %s. Hmm, I am not sure." % other.replace("\u2019", "'").lower(),
                     texts_of(other))
    assert a.text == "you asked: <prompt>. hmm, i am not sure.", "the customer's words made a group of their own"
    assert a.group_id == b.group_id, "the customer's words made a group of their own"


@mutation(ALERTS, LINE_RULE, LINE_KEPT, expect="a line of the customer's words was kept")
def test_a_line_wholly_inside_the_sent_text_is_removed_and_the_rest_of_the_reply_is_kept():
    prompt = "a red kite over the sea"
    plain = alerts.build("dola", "Hmm, let me think.")
    for echo in (prompt, prompt.upper(), "%s [4821]" % prompt, "  a  red kite\u200b over the sea "):
        built = alerts.build("dola", "Hmm, let me think.\n%s" % echo, texts_of(prompt))
        assert built.group_id == plain.group_id, "a line of the customer's words was kept: %r" % (built.text,)


@mutation(ALERTS, ONLY_PROMPT, ONLY_PROMPT_DROPPED, expect="a reply that only repeats the prompt became empty")
def test_a_reply_that_only_repeats_the_prompt_keeps_the_marker_and_is_not_the_read_failure_group():
    prompt = "a red kite over the sea"
    built = alerts.build("dola", prompt, texts_of(prompt))
    assert built.text == "<prompt>", "a reply that only repeats the prompt became empty: %r" % (built.text,)
    assert built.group_id != alerts.build("dola", "").group_id


@mutation(ALERTS, FOLD_LINE, FOLD_LOWER, expect="the fold missed")
@mutation(ALERTS, FOLD_LINE, FOLD_NO_DASH, expect="the fold missed")
def test_the_inline_match_ignores_white_space_case_sharp_s_and_dashes():
    cases = (("a red kite flying", "a  red   kite\nflying"), ("Die Stra\u00dfe am Meer", "DIE STRASSE AM MEER"),
             ("red\u2013kite flying high", "red-kite flying high"),
             ("\u00b4quoted\u00b4 words here", "\u00b4quoted\u00b4  WORDS here"),
             ("`quoted` words here", "'quoted' words here"),
             ("it\u2032s a prime test here", "it's a prime test here"))
    for prompt, said in cases:
        built = alerts.build("dola", "You asked: %s. Hmm." % said, texts_of(prompt))
        assert "<prompt>" in built.text, "the fold missed: %r -> %r" % (said, built.text)


@mutation(ALERTS, FLOOR_RULE, FLOOR_GONE, expect="a short prompt was cut out of dola's words")
def test_a_prompt_shorter_than_ten_characters_is_never_cut_out_of_dolas_words():
    for prompt in ("a", "cat", "red kite"):
        said = "Dola cannot answer that, a red kite cat."
        built = alerts.build("dola", said, texts_of(prompt))
        assert built.text == said, "a short prompt was cut out of dola's words: %r" % (built.text,)


@mutation(ALERTS, BOUNDARY_RULE, BOUNDARY_GONE, expect="the prompt was cut out of the middle of a word")
def test_the_prompt_is_replaced_only_at_word_boundaries():
    built = alerts.build("dola", "a red kites fly; also a red kite.", texts_of("a red kite"))
    assert built.text == "a red kites fly; also <prompt>.", "the prompt was cut out of the middle of a word"


@mutation(ALERTS, LINE_FLOOR_RULE, LINE_FLOOR_GONE, expect="dola's own short line was removed")
def test_a_short_line_is_kept_even_when_it_sits_inside_the_sent_text():
    said = "Yes\nOK then, we will see."
    built = alerts.build("dola", said, texts_of("Please answer yes"))
    assert built.text == said, "dola's own short line was removed: %r" % (built.text,)


def test_a_job_without_texts_or_with_a_blank_prompt_is_not_changed():
    assert alerts.build("dola", "You asked: x").text == "You asked: x"
    assert alerts.build("dola", "Hmm", alerts.JobTexts("\u200b ", None)).text == "Hmm"


@mutation(REAPER, TEXTS_PASSED, TEXTS_DROPPED, expect="a second row for the same words")
def test_the_same_reply_at_submit_and_at_the_deadline_makes_one_row_even_with_the_prompt_echoed(db):
    (job_id,) = queued(db, 1, prompt="a red kite")
    prompt = db.one("SELECT prompt FROM jobs WHERE id = %s", job_id)[0]
    applied(db, [job_id], [Accepted("ref-1", taken=False, unclear="Hmm, not sure.\n" + prompt)])
    db.run(PAST_DEADLINE, job_id)

    async def reap(p):
        await reaper(p, spec=Reporting("Hmm, not sure.\n" + prompt.upper())).tick()

    run_as(db.worker_dsn, reap)
    assert row(db, job_id, "status")[0] == "failed"
    assert [r[4] for r in reports(db)] == ["hmm, not sure."], "a second row for the same words"


@mutation(REAPER, TEXTS_PASSED, TEXTS_DROPPED, expect="a second row for the same words")
def test_the_deadline_builder_gets_the_texts_of_the_row_the_reaper_locked(db):
    (job_id,) = queued(db, 1, prompt="a red kite")
    prompt = db.one("SELECT prompt FROM jobs WHERE id = %s", job_id)[0]
    applied(db, [job_id], [Accepted("ref-1", taken=False, unclear="Hmm, not sure.")])
    db.run(PAST_DEADLINE, job_id)

    async def reap(p):
        await reaper(p, spec=Reporting("Hmm, not sure.\n" + prompt)).tick()

    run_as(db.worker_dsn, reap)
    assert [r[4] for r in reports(db)] == ["hmm, not sure."], "a second row for the same words"


@mutation(ALERTS, STORE_SAVEPOINT, STORE_NO_SAVEPOINT, expect="a report that cannot be stored held back the failure")
def test_a_report_that_cannot_be_stored_never_holds_back_the_deadline_failure_or_the_refund(db):
    db.run("REVOKE INSERT ON unclear_replies FROM relay_worker")
    try:
        job_id, refunds = expired(db, Reporting("an odd reply"))
    finally:
        db.run("GRANT INSERT ON unclear_replies TO relay_worker")
    assert row(db, job_id, "status")[0] == "failed", "a report that cannot be stored held back the failure"
    assert refunds == 1 and reports(db) == []


# ---- the type ---------------------------------------------------------------------------------------------------
@mutation(PROVIDERS, "if self.unclear is not None and (not isinstance(self.unclear, str) or self.taken):",
          "if self.unclear is not None and (not isinstance(self.unclear, str)):",
          expect="accepted an unclear reply on an order that is taken")
@mutation(PROVIDERS, "if self.unclear is not None and (not isinstance(self.unclear, str) or self.taken):",
          "if self.unclear is not None and (self.taken):", expect="accepted an unclear value that is not text")
def test_accepted_unclear_is_text_only_on_an_order_not_taken_and_stays_out_of_the_repr():
    try:
        Accepted("r", taken=True, unclear="x")
    except ValueError:
        pass
    else:
        pytest.fail("accepted an unclear reply on an order that is taken")
    try:
        Accepted("r", taken=False, unclear=b"x")        # type: ignore[arg-type]
    except ValueError:
        pass
    else:
        pytest.fail("accepted an unclear value that is not text")
    assert Accepted("r", taken=False, unclear="").unclear == "", "an empty reply is legal: it means nothing was read"
    secret = Accepted("r", taken=False, unclear="SECRET-WORDS-123")
    assert "SECRET-WORDS-123" not in repr(secret)
    assert secret != Accepted("r", taken=False) and Accepted("r", taken=False).unclear is None


# ---- the submit writer ------------------------------------------------------------------------------------------
def reports(db) -> list[tuple]:
    return db.all("SELECT job_id, provider, account_id, text, wording, group_id, acknowledged_at FROM unclear_replies "
                  "ORDER BY id")


def queued(db, n: int = 1, prompt: str = "a prompt") -> list:
    """`n` queued jobs and one account, made under the owner role (the worker cannot read api keys)."""
    k = key(db)
    account(db)

    async def scenario(p):
        return [(await create(p, k, "%s %d" % (prompt, i))).job_id for i in range(n)]

    return run(db, scenario)


def applied(db, job_ids, outcomes, *, setup=None):
    """Claim each job in turn and apply its scripted result under the worker role, as the engine loop does."""
    async def scenario(p):
        for outcome in outcomes:
            got = await claim(p)
            engine = FakeEngine()
            engine.script_submit("*", sent(SubmitResult(outcome)))
            await submit(p, got, engine)

    run_as(db.worker_dsn, scenario)


def reported(db, results):
    """One job per entry of `results` (an `Accepted`), each applied under the worker role. Returns the job ids."""
    ids = queued(db, len(results))
    applied(db, ids, results)
    return ids


@mutation(APPLY, STORE_CALL, STORE_NEVER, expect="no row was stored for the unclear reply")
def test_an_unclear_result_writes_one_row_with_the_jobs_provider_and_account_and_the_job_waits_uncharged(db):
    (job_id,) = reported(db, [Accepted("ref-1", taken=False, unclear="Hmm, let me think about order [4821].")])
    found = reports(db)
    assert len(found) == 1, "no row was stored for the unclear reply"
    (got,) = found
    assert got[0] == job_id and got[1] == "fake" and got[3] == "Hmm, let me think about order [4821]."
    assert got[2] == db.one("SELECT account_id FROM jobs WHERE id = %s", job_id)[0]
    assert got[4] == "hmm, let me think about order [#]."
    assert got[5] == alerts.group_of("fake", got[4]) and got[6] is None, "no row was stored for the unclear reply"
    assert db.one("SELECT status, taken_at IS NULL FROM jobs WHERE id = %s", job_id) == ("waiting", True)
    assert db.one("SELECT count(*) FROM account_charges")[0] == 0


def test_a_result_without_an_unclear_reply_writes_no_row_and_an_empty_one_writes_the_read_failure_row(db):
    reported(db, [Accepted("r-1", taken=False), Accepted("r-2", taken=True)])
    assert reports(db) == []
    reported(db, [Accepted("r-3", taken=False, unclear="")])
    (got,) = reports(db)
    assert got[3] == "" and got[4] == "", "an empty reply is stored: the provider's API gave no text"


def test_a_lone_surrogate_reply_still_applies_the_result_and_stores_the_cleaned_text(db):
    (job_id,) = reported(db, [Accepted("r-1", taken=False, unclear="odd \ud800 reply")])
    assert db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0] == "waiting"
    assert reports(db)[0][3] == "odd ? reply"


@mutation(MIGRATION, "    UNIQUE (job_id, group_id)\n);", "    CHECK (true)\n);",
          expect="the same words were stored twice")
@mutation(ALERTS, "           \"ON CONFLICT DO NOTHING\")", "           \"\")",
          expect="the repeat was an error, not a skip")
def test_the_same_words_for_one_job_are_one_row_and_a_repeat_is_a_skip_not_an_error(db, caplog):
    """The worker has INSERT only, so the key decides: the same (job, group) twice is skipped, no permission error."""
    (job_id,) = reported(db, [Accepted("r-1", taken=False, unclear="Same words [1].")])
    report = alerts.build("fake", "Same words [22].")             # the same wording: digits fold
    other = alerts.build("fake", "Other words")

    async def again(p):
        for built in (report, report, other):
            async with p.connection() as conn, conn.transaction():
                await alerts.store(conn, job_id, built)

    with caplog.at_level(logging.ERROR, logger=LOG):
        run_as(db.worker_dsn, again)
    assert [r[4] for r in reports(db)] == ["same words [#].", "other words"], "the same words were stored twice"
    assert not caplog.records and "unclear_not_stored" not in [r[0] for r in db.all("SELECT stage FROM job_events")], \
        "the repeat was an error, not a skip"


def test_the_worker_may_insert_and_not_read_update_or_delete_an_unclear_reply(db):
    (job_id,) = reported(db, [Accepted("r-1", taken=False, unclear="words")])
    failed = []
    with psycopg.connect(db.worker_dsn, autocommit=True, options="-c search_path=relay") as c:
        for sql in ("SELECT 1 FROM unclear_replies", "UPDATE unclear_replies SET text = 'x'",
                    "DELETE FROM unclear_replies"):
            try:
                c.execute(sql)
            except psycopg.errors.InsufficientPrivilege:
                failed.append(sql)
    assert len(failed) == 3, "the worker has INSERT only: %s" % failed
    assert db.one("SELECT count(*) FROM unclear_replies WHERE job_id = %s", job_id)[0] == 1


def test_a_lost_fence_writes_neither_the_row_nor_the_transition(db):
    (job_id,) = queued(db)

    async def scenario(p):
        got = await claim(p)
        db.run("UPDATE leases SET until = clock_timestamp() - interval '1 second' WHERE id = %s", got.lease_id)
        result = SubmitResult(Accepted("r", taken=False, unclear="words"))
        with pytest.raises(LeaseLost):
            await store.apply_submit(p, got, result, caps=CAPS, pools=POOLS)

    run_as(db.worker_dsn, scenario)
    assert reports(db) == [] and db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0] == "submitting"


@mutation(ALERTS, "        async with conn.transaction():\n            await conn.execute(_INSERT,",
          "        if True:\n            await conn.execute(_INSERT,",
          expect="a report that cannot be stored blocked the result")
def test_a_revoked_grant_leaves_the_result_applied_and_logs_the_job_and_the_sqlstate_only(db, caplog):
    (job_id,) = queued(db)
    db.run("REVOKE INSERT ON unclear_replies FROM relay_worker")
    try:
        with caplog.at_level(logging.ERROR, logger=LOG):
            try:
                applied(db, [job_id], [Accepted("r", taken=False, unclear="words " + MARKER)])
            except Exception as exc:     # noqa: BLE001
                pytest.fail("a report that cannot be stored blocked the result: %r" % type(exc).__name__)
    finally:
        db.run("GRANT INSERT ON unclear_replies TO relay_worker")
    assert db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0] == "waiting" and reports(db) == []
    lines = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(lines) == 1 and str(job_id) in lines[0].getMessage() and "42501" in lines[0].getMessage()
    assert lines[0].exc_info is None and MARKER not in lines[0].getMessage()
    assert db.one("SELECT text FROM job_events WHERE job_id = %s AND stage = 'unclear_not_stored'", job_id) == (
        "unclear reply not stored: 42501",)


@mutation(ALERTS, LOG_LINE, LOG_EXC,
          expect="the planted marker reached a log record")
def test_a_constraint_failure_whose_server_detail_holds_the_row_never_reaches_a_log_record(db, caplog):
    (job_id,) = queued(db)
    db.run("ALTER TABLE unclear_replies ADD CONSTRAINT planted_check CHECK (text NOT LIKE '%%PLANTED%%') NOT VALID")
    try:
        with caplog.at_level(logging.DEBUG):
            applied(db, [job_id], [Accepted("r", taken=False, unclear="words " + MARKER)])
    finally:
        db.run("ALTER TABLE unclear_replies DROP CONSTRAINT planted_check")
    assert db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0] == "waiting" and reports(db) == []
    assert any("23514" in r.getMessage() for r in caplog.records), "premise: the constraint failed"
    for record in caplog.records:
        assert MARKER not in record.getMessage() and MARKER not in (record.exc_text or ""), \
            "the planted marker reached a log record"


@mutation(ALERTS, BUILDER_CATCH, BUILDER_NARROW,
          expect="a builder error blocked the result")
def test_a_builder_error_drops_the_report_and_the_result_still_applies(db, monkeypatch, caplog):
    def broken(provider, text, texts=None):
        raise RuntimeError("builder broke " + MARKER)

    monkeypatch.setattr(alerts, "build", broken)
    with caplog.at_level(logging.ERROR, logger=LOG):
        try:
            (job_id,) = reported(db, [Accepted("r", taken=False, unclear="words")])
        except RuntimeError:
            pytest.fail("a builder error blocked the result")
    assert db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0] == "waiting" and reports(db) == []
    assert all(MARKER not in r.getMessage() for r in caplog.records), "the error text is not logged"


@mutation(ALERTS, READ_SAVEPOINT, READ_NO_SAVEPOINT, expect="a failing text read dropped the result")
def test_a_failing_read_of_the_jobs_texts_drops_the_report_and_keeps_the_result(db, monkeypatch, caplog):
    async def unreadable(conn, job_id):
        await conn.execute("SELECT 1 / 0")

    monkeypatch.setattr(alerts, "job_texts", unreadable)
    (job_id,) = queued(db)
    with caplog.at_level(logging.ERROR, logger=LOG):
        try:
            applied(db, [job_id], [Accepted("ref-1", taken=False, unclear="words " + MARKER)])
        except Exception as exc:     # noqa: BLE001
            pytest.fail("a failing text read dropped the result: %r" % type(exc).__name__)
    assert db.one("SELECT status, external_ref, sent_at IS NOT NULL FROM jobs WHERE id = %s", job_id) == (
        "waiting", "ref-1", True)
    assert db.one("SELECT count(*) FROM leases WHERE job_id = %s", job_id) == (0,), "the lease was not released"
    assert reports(db) == []
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(lines) == 1 and str(job_id) in lines[0] and "22012" in lines[0] and MARKER not in lines[0]


def result_state(db, job_id):
    return db.one("SELECT status, external_ref, sent_at IS NOT NULL FROM jobs WHERE id = %s", job_id), \
        db.one("SELECT count(*) FROM leases WHERE job_id = %s", job_id)


@mutation(ALERTS, READ_GUARD, READ_GUARD_NARROW, expect="a plain exception in the text read dropped the result")
def test_a_plain_exception_in_the_text_read_drops_the_report_and_keeps_the_result(db, monkeypatch, caplog):
    async def unreadable(conn, job_id):
        raise TimeoutError("read timed out " + MARKER)

    monkeypatch.setattr(alerts, "job_texts", unreadable)
    (job_id,) = queued(db)
    with caplog.at_level(logging.ERROR, logger=LOG):
        try:
            applied(db, [job_id], [Accepted("ref-1", taken=False, unclear="words")])
        except Exception as exc:     # noqa: BLE001
            pytest.fail("a plain exception in the text read dropped the result: %r" % type(exc).__name__)
    assert result_state(db, job_id) == (("waiting", "ref-1", True), (0,)) and reports(db) == []
    assert all(MARKER not in r.getMessage() for r in caplog.records)


@mutation(ALERTS, STORE_GUARD, STORE_GUARD_NARROW, expect="a plain exception in the insert dropped the result")
def test_a_plain_exception_in_the_insert_leaves_the_result_as_without_a_report(db, monkeypatch, caplog):
    monkeypatch.setattr(alerts, "_INSERT", object())          # the driver refuses a query that is not text
    (job_id,) = queued(db)
    with caplog.at_level(logging.ERROR, logger=LOG):
        try:
            applied(db, [job_id], [Accepted("ref-1", taken=False, unclear="words")])
        except Exception as exc:     # noqa: BLE001
            pytest.fail("a plain exception in the insert dropped the result: %r" % type(exc).__name__)
    assert result_state(db, job_id) == (("waiting", "ref-1", True), (0,)) and reports(db) == []


@mutation(ALERTS, NOTE_SAVEPOINT, NOTE_NO_SAVEPOINT, expect="a failing diagnostic rolled back the result")
def test_a_failing_diagnostic_after_a_failed_insert_never_rolls_back_the_result(db, monkeypatch):
    async def failing(conn, *args):
        await conn.execute("SELECT 1 / 0")

    monkeypatch.setattr(alerts, "add_event", failing)
    (job_id,) = queued(db)
    db.run("REVOKE INSERT ON unclear_replies FROM relay_worker")
    try:
        try:
            applied(db, [job_id], [Accepted("ref-1", taken=False, unclear="words")])
        except Exception as exc:     # noqa: BLE001
            pytest.fail("a failing diagnostic rolled back the result: %r" % type(exc).__name__)
    finally:
        db.run("GRANT INSERT ON unclear_replies TO relay_worker")
    assert result_state(db, job_id) == (("waiting", "ref-1", True), (0,)) and reports(db) == []


def test_the_failpoint_after_the_insert_rolls_back_the_row_and_the_transition(db):
    (job_id,) = queued(db)

    async def die():
        raise failpoints.FailpointError("after the insert")

    failpoints.arm("apply.unclear_reported", die)
    try:
        with pytest.raises(failpoints.FailpointError):
            applied(db, [job_id], [Accepted("r", taken=False, unclear="words")])
    finally:
        failpoints.disarm("apply.unclear_reported")
    assert reports(db) == [] and db.one("SELECT status FROM jobs WHERE id = %s", job_id)[0] == "submitting"


# ---- the trigger ------------------------------------------------------------------------------------------------
@mutation(MIGRATION, TRIGGER, "", expect="a stamp changed again")
def test_a_stamp_is_final_for_every_role_and_the_api_cannot_change_the_text_or_delete(db):
    (job_id,) = reported(db, [Accepted("r", taken=False, unclear="words")])
    (row_id,) = db.one("SELECT id FROM unclear_replies WHERE job_id = %s", job_id)

    def as_api(sql, *params):
        with psycopg.connect(db.api_dsn, autocommit=True, options="-c search_path=relay") as c:
            return c.execute(sql, params).rowcount

    for refused in ("UPDATE unclear_replies SET text = 'x'", "DELETE FROM unclear_replies",
                    "UPDATE unclear_replies SET acknowledged_at = now()"):        # a half stamp: the CHECK refuses it
        with pytest.raises(psycopg.errors.Error):
            as_api(refused)
    stamp = "UPDATE unclear_replies SET acknowledged_at = clock_timestamp(), acknowledged_by = 'ann' WHERE id = %s"
    assert as_api(stamp, row_id) == 1
    for again in ("UPDATE unclear_replies SET acknowledged_by = 'bob' WHERE id = %s",
                  "UPDATE unclear_replies SET acknowledged_at = NULL, acknowledged_by = NULL WHERE id = %s"):
        try:
            as_api(again, row_id)
        except psycopg.errors.Error:
            continue
        pytest.fail("a stamp changed again")
    assert db.one("SELECT acknowledged_by FROM unclear_replies WHERE id = %s", row_id) == ("ann",)
    with psycopg.connect(db.api_dsn, autocommit=True, options="-c search_path=relay") as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT nextval('unclear_replies_id_seq')")


# ---- the deadline writer ----------------------------------------------------------------------------------------
class Reporting(FakeSpec):
    """A provider whose hook reports a text, counts its calls, or raises."""

    def __init__(self, text="dola said something odd", raises=False, **kw):
        super().__init__(unclear=text, **kw)
        self.calls, self.raises = 0, raises

    def unclear_at_deadline(self, upstream_text, provider_state):
        self.calls += 1
        if self.raises:
            raise RuntimeError("hook broke " + MARKER)
        return self.unclear


def expired(db, spec, *, prompt="x"):
    """A waiting job past its render deadline, made as the owner and reaped by `spec` under the worker role, as in
    production; returns (job id, refund count)."""
    k = key(db)
    account(db, left=100)

    async def make(p):
        job_id = await to_waiting(p, k, prompt=prompt)
        age(db, job_id, "render_since", 9_100)
        return job_id

    job_id = run(db, make)

    async def reap(p):
        await reaper(p, spec=spec).tick()

    run_as(db.worker_dsn, reap)
    return job_id, db.one("SELECT count(*) FROM ledger WHERE job_id = %s AND entry = 'refund'", job_id)[0]


@mutation(REAPER, "            said = self.spec.unclear_at_deadline(text, state)\n", "            said = None\n",
          expect="no row for the deadline report")
def test_a_render_deadline_with_an_unexplained_reply_reports_it_in_the_failing_transaction(db):
    job_id, refunds = expired(db, Reporting("Still thinking about order [31]."))
    assert row(db, job_id, "status, error_code") == ("failed", "render.deadline") and refunds == 1
    found = reports(db)
    assert len(found) == 1, "no row for the deadline report"
    (got,) = found
    assert got[0] == job_id and got[3] == "Still thinking about order [31]."


def test_a_deadline_with_no_text_to_report_adds_no_row(db):
    job_id, refunds = expired(db, FakeSpec())
    assert row(db, job_id, "status")[0] == "failed" and refunds == 1 and reports(db) == []


def test_the_same_words_already_reported_at_submit_add_no_row_and_other_words_add_one(db):
    (job_id,) = reported(db, [Accepted("ref-1", taken=False, unclear="Hmm [1].")])
    db.run(PAST_DEADLINE, job_id)

    async def tick(p, spec):
        await reaper(p, spec=spec).tick()

    run(db, lambda p: tick(p, Reporting("Hmm [22].")))
    assert row(db, job_id, "status")[0] == "failed" and len(reports(db)) == 1, "the same words make one row"
    (second,) = reported(db, [Accepted("ref-2", taken=False, unclear="First.")])
    db.run(PAST_DEADLINE, second)
    run(db, lambda p: tick(p, Reporting("Different words")))
    assert [r[4] for r in reports(db)] == ["hmm [#].", "first.", "different words"]


@mutation(REAPER, HOOK_GUARD, HOOK_NARROW,
          expect="a raising hook blocked the failure")
def test_a_hook_that_raises_or_a_builder_that_raises_never_blocks_the_failure_or_the_refund(db, caplog):
    with caplog.at_level(logging.ERROR):
        job_id, refunds = expired(db, Reporting(raises=True))
    assert row(db, job_id, "status")[0] == "failed", "a raising hook blocked the failure"
    assert refunds == 1 and reports(db) == [] and all(MARKER not in r.getMessage() for r in caplog.records)
    odd = Reporting()
    odd.unclear = object()                                    # the builder refuses a value that is not text
    job_id, refunds = expired(db, odd)
    assert row(db, job_id, "status")[0] == "failed" and refunds == 1 and len(reports(db)) == 0


def test_a_job_that_never_started_is_failed_without_asking_the_hook(db):
    spec = Reporting()
    k = key(db)
    account(db, left=100)

    async def scenario(p):
        created = await create(p, k)
        age(db, created.job_id, "created_at", 10_900)
        await reaper(p, spec=spec).tick()
        return created.job_id

    job_id = run(db, scenario)
    assert row(db, job_id, "error_code") == ("queue.never_started",) and spec.calls == 0 and reports(db) == []


def test_the_failpoint_after_the_deadline_insert_leaves_neither_the_row_nor_the_failure(db):
    async def die():
        raise failpoints.FailpointError("after the insert")

    failpoints.arm("reaper.unclear_reported", die)
    try:
        job_id, refunds = expired(db, Reporting())
    finally:
        failpoints.disarm("reaper.unclear_reported")
    assert row(db, job_id, "status")[0] == "waiting" and refunds == 0 and reports(db) == []
