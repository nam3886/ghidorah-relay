"""Account refusals: a job THIS account cannot do NOW moves to another account, on at most
`<provider>.refusal_max_accounts` accounts (owner's ruling 25/10, first on dola; the core's for every provider since).

When a submit uses it. The upstream answered the send and the answer PROVES nothing was made or taken (no order id, no
conversation, no charge), and what it refused is the account, not the order: a throttle, a risk or rate code, a quota
or region of that account. The engine returns `account_refused(...)` as the job outcome, with the account's effects (a
`Rest` of the pool or a `Cooldown`, so the claim leaves the account alone meanwhile) in `SubmitResult.account`:

    if throttled and nothing_made:
        until = now + timedelta(seconds=ctx.settings.get("acme.throttle_rest_s"))
        return SubmitResult(account_refused(job, ctx.account.id, ctx.settings, not_taken="pool.account_throttled",
                                            final="acme.refused_on_accounts", detail="acme throttled (429)"),
                            account=(Rest("video", until, "acme throttled (429)"),))

What it is not:
- a refusal of the ORDER (content, policy, a likeness, a prompt the upstream will never take on any account): plain
  `Fail(code)` with a terminal, refunded rule. Another account would refuse it too, and every try costs an account.
- a send that MAY have been taken (a timeout after the send, an order id with no outputs, a conversation that appeared
  before the refusal: dola refuses and still delivers about 25 min later): `Ambiguous`, or `Fail` when the provider
  can prove it ends there. Never moved: a second account is a second upstream order (no re-send once accepted).
- the relay's own fault (a proxy, a signed-out page, a page fault): its own `pool.*` or `Retry` code, not counted.

What it does. The account is appended to the job's `REFUSED_BY` list (`provider_state`; every requeue keeps it,
`jobs.apply.KEPT_ON_REQUEUE`) and the job goes back to the queue with `not_taken` (a rule that uses no attempt and is
not terminal: a `pool.*` code or the provider's own), while the list holds fewer distinct accounts than the setting
allows. The refusal that reaches it fails the job with `final` (a terminal rule with its full refund: zero outputs,
the key gets back exactly what it paid, once); so does any refusal of a pinned job (`JobView.source`: no other account
may run it). The claim never gives a job to an account in its `REFUSED_BY` list (`jobs.claim._PICK`), so the move
really goes elsewhere even when the account's rest ends first; with no other account free the job waits in the queue
until one is, or until `unsent_deadline_s` ends it with its refund.

The setting. Every provider that uses this declares `refusal_max_accounts_setting(provider, description)` in its
`settings()`: `<provider>.refusal_max_accounts`, a live whole number from 1 (the first refusal ends the job) to
`MAX_ACCOUNTS_CEILING`, default `DEFAULT_MAX_ACCOUNTS`. A provider that does not declare it gets the default.
"""

from collections.abc import Mapping
from typing import Any

from relay_core.providers import REFUSED_BY, Fail, JobView, NotTaken
from relay_core.settings import SettingsError, Spec

MAX_ACCOUNTS = "refusal_max_accounts"     # the setting's name under the provider: `<provider>.refusal_max_accounts`
DEFAULT_MAX_ACCOUNTS = 2                  # owner's ruling 25/10: a second account, then fail with the refund
MAX_ACCOUNTS_CEILING = 9


def max_accounts_key(provider: str) -> str:
    return "%s.%s" % (provider, MAX_ACCOUNTS)


def refusal_max_accounts_setting(provider: str, description: str) -> tuple[str, Spec]:
    """`<provider>.refusal_max_accounts` as a `settings()` entry: the provider says in `description` what it counts as
    a refusal of the account (its codes), the bounds and the default are the core's."""
    key = max_accounts_key(provider)
    return key, Spec(key, int, DEFAULT_MAX_ACCOUNTS, description, min=1, max=MAX_ACCOUNTS_CEILING)


def max_accounts(settings: Any, provider: str) -> int:
    """How many accounts one job may be refused on: the live setting, else (no settings view, a provider that does not
    declare it) `DEFAULT_MAX_ACCOUNTS`."""
    try:
        cap = settings.get(max_accounts_key(provider)) if settings is not None else None
    except (KeyError, SettingsError):
        cap = None
    return cap if type(cap) is int else DEFAULT_MAX_ACCOUNTS


def refused_accounts(job: JobView, account_id: str) -> list[str]:
    """The job's `REFUSED_BY` list with this account added (once: a list counts distinct accounts), oldest first."""
    before = job.provider_state.get(REFUSED_BY)
    before = list(before) if isinstance(before, list) else []
    return before if account_id in before else [*before, account_id]


def account_refused(job: JobView, account_id: str, settings: Any, *, not_taken: str, final: str, detail: str,
                    patch: Mapping[str, Any] | None = None) -> NotTaken | Fail:
    """The outcome of a refusal of `account_id` that proves nothing was made (see the module docstring):
    `NotTaken(not_taken)` while the job's distinct refused accounts, this one included, are fewer than
    `<provider>.refusal_max_accounts`, else (or for a pinned job) `Fail(final)` with `detail` and why it ended. Both
    carry `patch` and the whole `REFUSED_BY` list. Pure: the caller puts the account's effects beside it in its
    `SubmitResult`."""
    refused = refused_accounts(job, account_id)
    state = {**(patch or {}), REFUSED_BY: refused}
    if job.source is not None:
        return Fail(final, "%s (a pinned job runs only on its source's account)" % detail, state)
    if len(refused) < max_accounts(settings, job.provider):
        return NotTaken(not_taken, state)
    return Fail(final, "%s (refused on %d account(s), the most %s allows)"
                % (detail, len(refused), max_accounts_key(job.provider)), state)
