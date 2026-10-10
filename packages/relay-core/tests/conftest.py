"""Database fixtures for the relay-core tests (contracts §14.2, §14.3).

One migrated disposable database per session; every test that asks for `db` gets it empty. After each such
test the invariant checker runs over what the test left behind, then every table is emptied.
"""

import os
import uuid
from dataclasses import dataclass

import psycopg
import pytest
from pgtemplate import migrated_database
from psycopg.conninfo import make_conninfo

from relay_core.testing.checker import violations

# Failpoints are inert unless the variable exists (relay_core.failpoints); the tests arm them in process.
os.environ.setdefault("RELAY_FAILPOINTS", "")

API_PASSWORD = "api-test-password"
WORKER_PASSWORD = "worker-test-password"

# The strict checker tier (the ten rules of checker._STRICT_JOB_RULES, about what the platform's own writers promise)
# runs after every test. A test that plants rows by hand cannot satisfy every one of them, so it names the rules it
# plants: only those strict-only lines are ignored for that test, and a rule it names that no longer fires fails the
# test, so an exemption never outlives the row that needed it. A rule is named by a substring of its text in
# checker._STRICT_JOB_RULES. Use `@pytest.mark.fabricated_rows("why", "<rule>", ...)` for a new test; an entry here
# keeps the older tests' files untouched. test_invariants.py fails when a name here no longer exists or a rule
# matches no strict rule (or two).
# test file -> (why these tests plant rows by hand, {test: the strict rules it plants})
FABRICATED = {
    'test_account_commands.py': (
        'plants a failed job with no error code and a charge that is not the job account_cost',
        {
            'test_delete_refuses_a_used_account_and_leaves_its_row_and_its_screenshot_object[charge-it has an account charge]':  # noqa: E501
                ('account_cost',),
            'test_delete_refuses_a_used_account_and_leaves_its_row_and_its_screenshot_object[job-it has a job]':
                ('without an error code',),
        }),
    'test_claim.py': (
        'plants an unknown job that was never sent',
        {
            'test_a_browser_provider_skips_an_account_whose_unknown_job_is_due_for_a_reconcile': ('never sent',),
        }),
    'test_crash_windows.py': (
        'plants a failed job with no error code to test webhook redelivery',
        {
            'test_a_webhook_whose_2xx_came_after_the_sender_died_is_sent_again_with_the_same_event_id':
                ('without an error code',),
        }),
    'test_create.py': (
        'plants a queued priced job with no ledger row (a foreign writer) and finished jobs with no finished_at',
        {
            'test_a_unique_violation_from_a_writer_without_the_lock_replays_the_winner':
                ('no charge row in the ledger',),
            'test_finished_jobs_do_not_count_toward_the_cap': ('terminal job without finished_at',),
        }),
    'test_db_accounts.py': (
        'plants an account charge that is not the job account_cost',
        {
            'test_an_account_with_a_charge_is_refused': ('account_cost',),
        }),
    'test_engine.py': (
        'deletes the lease of a submitting job: the checker sees an orphan the reaper has not yet taken',
        {
            'test_when_the_lease_is_gone_the_submit_is_cancelled_the_result_is_dropped_and_the_reaper_owns_the_job':
                ('no lease row',),
        }),
    'test_fetcher.py': (
        're-arms next_poll_at on a failed job to prove the fetcher ignores it',
        {
            'test_a_download_that_keeps_failing_fails_the_job_at_the_render_deadline_and_refunds_once':
                ('poll token or a next_poll_at',),
        }),
    'test_leases.py': (
        'plants submitting jobs with no lease row to test the lease reaper',
        {
            'test_every_purpose_gets_a_hard_until_that_caps_its_ttl': ('no lease row',),
            'test_the_reaper_never_deletes_a_live_lease_or_a_job_lease': ('no lease row',),
        }),
    'test_pool.py': (
        'plants account charges that are not the job account_cost, and submitting jobs with no lease row',
        {
            'test_a_charge_counts_account_cost_once_per_job_and_a_reported_value_stays_reported': ('account_cost',),
            'test_a_counted_balance_does_not_wake_a_rest_and_a_reported_one_overrides_a_counted_one':
                ('account_cost',),
            'test_a_refund_after_the_pool_day_rolled_credits_nothing': ('account_cost',),
            'test_a_refund_restores_the_exact_amount_once_and_wakes_the_pool': ('account_cost',),
            'test_charging_the_same_job_twice_decrements_once': ('account_cost',),
            'test_drift_corrects_a_counted_value_and_returns_what_it_found': ('account_cost',),
            'test_eight_concurrent_refunds_of_one_charge_restore_it_once': ('account_cost',),
            'test_reserved_counts_uncharged_job_leases_of_the_pool_only': ('no lease row',),
        }),
    'test_recheck_recover.py': (
        'plants terminal jobs with no finished_at or stale poll tokens, and submitting jobs with no lease',
        {
            'test_bulk_recheck_skips_jobs_the_provider_flagged_and_reports_why_the_others_were_left':
                ('poll token or a next_poll_at', 'terminal job without finished_at'),
            'test_recheck_acts_only_on_failed_waiting_and_unknown_jobs_that_were_sent':
                ('no lease row', 'poll token or a next_poll_at', 'terminal job without finished_at'),
        }),
    'test_reconcile.py': (
        'deletes the lease of a submitting job to free the slot',
        {
            'test_a_busy_account_is_reconciled_when_its_slot_frees': ('no lease row',),
        }),
    'test_redaction_writers.py': (
        'plants a failed job with no error code to test its writers',
        {
            'test_a_long_value_is_stored_as_an_omitted_marker_by_every_writer': ('without an error code',),
        }),
    'test_roles.py': (
        'runs hand-written statements per role: plants jobs with no finished_at, no lease, odd charges, pending hooks',
        {
            'test_every_statement_the_api_makes_runs_under_relay_api':
                ('pending webhook', 'terminal job without finished_at'),
            'test_the_worker_does_what_it_needs_and_not_ddl_the_ledger_or_the_admin_tables':
                ('account_cost', 'no lease row'),
        }),
    'test_titles.py': (
        'plants done and failed jobs with no finished_at or error code, and waiting jobs with no next_poll_at',
        {
            'test_the_sweep_names_untitled_jobs_including_done_ones_and_leaves_a_set_title_alone':
                ('no next_poll_at', 'without an error code'),
            'test_the_sweep_skips_a_job_that_is_too_young_too_old_or_has_no_ref': ('without an error code',),
        }),
    'test_webhooks.py': (
        'plants failed jobs with no error code, or jobs that are not terminal but carry finished_at',
        {
            'test_2xx_is_delivered_with_a_thin_body': ('without an error code',),
            'test_4xx_is_final': ('without an error code',),
            'test_a_batch_is_at_most_five': ('without an error code',),
            'test_a_claim_that_died_before_its_mark_ends_as_failed': ('without an error code',),
            'test_a_long_answer_is_stored_as_omitted_and_its_secret_never_lands': ('without an error code',),
            'test_a_redirect_is_not_followed': ('without an error code',),
            'test_a_send_of_a_cleared_event_does_not_mark_the_job': ('no next_poll_at', 'still has finished_at'),
            'test_admin_resend_of_a_failed_webhook_keeps_its_event_id': ('without an error code',),
            'test_concurrent_senders_post_each_delivery_once': ('without an error code',),
            'test_event_id_is_stable_across_attempts': ('without an error code',),
            'test_failures_retry_at_30s_then_300s_then_fail': ('without an error code',),
            'test_recheck_during_a_send_drops_the_old_mark_and_delivers_the_newer_event': ('without an error code',),
            'test_ssrf_refusals_are_final_and_send_nothing': ('without an error code',),
            'test_two_claimers_never_take_one_delivery': ('without an error code',),
        }),
}


def planted_rules(node) -> tuple[str, ...]:
    """The strict rules this test plants by hand; () means the whole strict tier applies."""
    marker = node.get_closest_marker("fabricated_rows")
    if marker is not None:
        if len(marker.args) < 2:
            raise pytest.UsageError("fabricated_rows needs the why and the rules it plants: %s" % node.name)
        return tuple(marker.args[1:])
    _why, tests = FABRICATED.get(node.path.name, ("", {}))
    return tuple(tests.get(node.name) or tests.get(getattr(node, "originalname", node.name), ()))


def judge(base: list[str], found: list[str], planted: tuple[str, ...]) -> list[str]:
    """`found` (the strict tier) minus the strict-only lines of the rules the test plants; plus one line for every
    planted rule that fired nothing (a stale exemption)."""
    hidden = [v for v in found if v not in base and any(rule in v for rule in planted)]
    stale = ["exempt rule %r fires no more: remove it from the test's exemption" % r
             for r in planted if not any(r in v for v in hidden)]
    return [v for v in found if v not in hidden] + stale


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "fabricated_rows(why, *rules): the test plants rows by hand; the strict rules named are ignored")


JOB_COLUMNS = ("id, provider, key_id, operation, media_type, model, prompt, marker, status, completion, after_sent, "
               "submit_timeout_s, wallet_kind, price")


def job_args(job_id, key_id, *, provider="p1", kind="video", price=0, prompt="a prompt") -> tuple:
    """Parameters for `INSERT INTO jobs (JOB_COLUMNS)`: the smallest valid job row."""
    return (job_id, provider, key_id, "generate", kind, "m", prompt, "marker-" + str(job_id)[:8], "queued",
            "detach", "reconcile", 60, kind, price)


JOB_INSERT = "INSERT INTO jobs (%s) VALUES (%s)" % (JOB_COLUMNS, ", ".join(["%s"] * 14))


@dataclass
class Db:
    dsn: str          # the owner role
    api_dsn: str
    worker_dsn: str

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True, options="-c search_path=relay")

    def key(self, *wallets: tuple[str, str, int | None, int | None], enabled: bool = True) -> uuid.UUID:
        """An api key with wallets (provider, kind, day_limit, total_limit); None = unlimited."""
        key_id = uuid.uuid4()
        with self.connect() as c:
            c.execute("INSERT INTO api_keys (id, key_hash, key_prefix, enabled) VALUES (%s, %s, %s, %s)",
                      (key_id, key_id.hex, "rk-test", enabled))
            for provider, kind, day, total in wallets:
                c.execute("INSERT INTO key_wallets (key_id, provider, kind, day_limit, total_limit) "
                          "VALUES (%s, %s, %s, %s, %s)", (key_id, provider, kind, day, total))
        return key_id

    def job(self, key_id, **kw) -> uuid.UUID:
        job_id = uuid.uuid4()
        with self.connect() as c:
            c.execute(JOB_INSERT, job_args(job_id, key_id, **kw))
        return job_id

    def one(self, sql: str, *params):
        with self.connect() as c:
            return c.execute(sql, params).fetchone()

    def run(self, sql: str, *params) -> None:
        with self.connect() as c:
            c.execute(sql, params)

    def all(self, sql: str, *params):
        with self.connect() as c:
            return c.execute(sql, params).fetchall()

    def wallet(self, key_id, provider="p1", kind="video") -> tuple[int, int]:
        """(used_day, used_total)"""
        return self.one("SELECT used_day, used_total FROM key_wallets WHERE key_id=%s AND provider=%s AND kind=%s",
                        key_id, provider, kind)

    def pin(self, tz: str = "UTC") -> None:
        """The test seam for the billing day: production code cannot choose a timezone, a test moves the pin."""
        with self.connect() as c:
            c.execute("DELETE FROM billing_pin")
            c.execute("INSERT INTO billing_pin (tz) VALUES (%s)", (tz,))

    def reset(self) -> None:
        with self.connect() as c:
            names = [r[0] for r in c.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'relay' "
                "AND tablename NOT IN ('schema_migrations', 'billing_pin')")]
            c.execute("TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(names))
        self.pin("UTC")


@pytest.fixture(scope="session")
def _migrated():
    with migrated_database({"relay_api": API_PASSWORD, "relay_worker": WORKER_PASSWORD}, "tests") as dsn:
        yield Db(dsn,
                 make_conninfo(dsn, user="relay_api", password=API_PASSWORD),
                 make_conninfo(dsn, user="relay_worker", password=WORKER_PASSWORD))


@pytest.fixture
def db(_migrated) -> Db:
    return _migrated


@pytest.fixture(autouse=True)
def _after_each_db_test(request):
    """The invariant checker runs after every test that used `db`, then the tables are emptied."""
    yield
    used = request.node.funcargs.get("db")
    if used is None:
        return
    try:
        with used.connect() as c:
            found = judge(violations(c), violations(c, strict=True), planted_rules(request.node))
    finally:
        used.reset()
    assert not found, "invariants broken by this test: %s" % found
