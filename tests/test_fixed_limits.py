"""No numeric limit hides from the admin: every module-level number of the core and of the dola pure modules is either
on the Fixed limits card or excused here with a reason. Add a constant and this fails until it is listed
(`relay_core.limits` or the provider's `limits`) or excused. The dola `engine` package and the browser kit are not
scanned: they are not pure modules, and `relay serve` may not import them."""

import ast
import importlib
import pkgutil
from pathlib import Path

import relay_core
import relay_dola
from relay_core.limits import CORE
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC as DOLA

LIMITS = "packages/relay-core/src/relay_core/limits.py"
MAX_INFLIGHT_ROW = '    _c(engine, "MAX_INFLIGHT", "submits one worker runs at once", "jobs"),\n'


def excuse(reason: str, *names: str) -> dict[str, str]:
    return dict.fromkeys(names, reason)


# Names are `<module under relay_core>.<CONSTANT>` for the core and the full module path for dola.
NOT_SHOWN = {
    **excuse("an admin paging or id bound: it keeps a list request cheap and says nothing about behaviour",
             "admin.routes_alert_groups.MAX_OFFSET", "admin.routes_alert_groups.MAX_ID", "admin.routes_common.MAX_PAGE",
             "admin.routes_jobs.MAX_OFFSET", "admin.routes_logs.MAX_QUERY", "admin.routes_logs.MAX_MS"),
    **excuse("a text trim or a capture size for display and diagnostics, not a limit on what a customer can do",
             "admin.routes_jobs.TITLE_LIMIT", "admin.routes_jobs.DEBUG_READ_MAX", "alerts.REPLY_LIMIT",
             "alerts.TITLE_LIMIT", "alerts.GROUP_LEN", "alerts.ECHO_FLOOR", "api.errors_http.MAX_PARAM",
             "api.filenames.PART_MAX", "api.filenames.NAME_MAX", "api.request_log.MAX_BODY_BYTES",
             "api.request_log.MAX_ANSWER_BYTES", "jobs.apply.NOTE_LIMIT", "jobs.evidence.MAX_FILES",
             "jobs.evidence.MAX_BYTES", "jobs.evidence.TIMEOUT_S", "jobs.titles.TITLE_LIMIT",
             "jobs.webhooks.MAX_ANSWER_BYTES", "redact.LOG_VALUE_LIMIT", "storage.NAME_MAX",
             "pool.DRIFT_LOG_TTL_S", "relay_dola.constants.EVENT_TEXT_MAX"),
    **excuse("a progress band or an estimate input of the job progress figure, not a limit",
             "api.progress.WAIT_FLOOR", "api.progress.WAIT_CAP", "api.progress.FETCH", "api.progress.DONE",
             "api.progress.FALLBACK_S", "api.progress.BUCKET_S", "api.progress.GUARD_TIMEOUT_S",
             "api.progress.SLOW_READ_S", "api.progress.PIN_MAX"),
    **excuse("a storage format: the length of the indexed head of a stored API key", "keys.LOOKUP_LEN"),
    **excuse("plumbing: a connection, a loop tick, a batch size or a shutdown detail that no admin decision depends on",
             "api.facade_common.HEARTBEAT_S", "api.facade_common.POLL_S", "api.runners.STOP_WAIT_S",
             "db.migrate.LOCK_KEY", "db.pool.TX_HOLD_S", "db.pool.GETCONN_TIMEOUT_S", "jobs.checks.TIMEOUT_SHARE",
             "jobs.engine.TICK_S", "jobs.engine.LAG_CHECK_S", "jobs.engine.LAG_WARN_S", "jobs.engine.WRITE_RESERVE_S",
             "jobs.poller.TICK_S", "jobs.reaper.DRIFT_S", "jobs.reconcile.BOUND_SHARE", "jobs.sweeps.BATCH",
             "jobs.sweeps.MEDIA_BATCH", "jobs.sweeps.SWEEP_HOLD_S", "jobs.titles.BATCH", "jobs.webhooks.BATCH",
             "jobs.webhooks.CLAIM_HOLD_S", "jobs.worker.REGISTER_RETRY_S", "net.MAX_REDIRECTS", "net.CLOSE_TIMEOUT_S",
             "storage.CONNECT_TIMEOUT_S", "storage.READ_TIMEOUT_S", "storage.CHUNK_SIZE"),
    **excuse("a code, not a bound",
             "api.request_log.CLIENT_CLOSED", "jobs.worker.EXIT_OK", "jobs.worker.EXIT_FAILED",
             "jobs.worker.EXIT_REFUSED"),
    **excuse("set from the environment (WORKER_GRACE_S) and reported by the worker, so not fixed",
             "jobs.worker.DEFAULT_GRACE_S"),
    **excuse("shown through the provider's Caps rows, which the core builds from the spec",
             "relay_dola.constants.PROXY_REST_S", "relay_dola.constants.CHECK_INTERVAL_S",
             "relay_dola.constants.CATALOG_REFRESH_S", "relay_dola.constants.MAX_ATTEMPTS",
             "relay_dola.constants.SUSPECT_THRESHOLD", "relay_dola.constants.PROMPT_MAX_CHARS",
             "relay_dola.constants.FRAME_MAX_SENT_CHARS"),
    **excuse("the catalog a customer reads from GET /v1/models",
             "relay_dola.catalog.DURATIONS_BY_MODEL", "relay_dola.catalog.VALID_DURATIONS"),
    **excuse("the default of the dola.prices setting, which the page already shows",
             "relay_dola.catalog.DEFAULT_PRICES"),
    **excuse("a unit size the registry itself uses to print a value", "limits.MIB", "limits.KIB"),
}


def numeric(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if isinstance(value, tuple | list | set | frozenset):
        return bool(value) and all(numeric(v) for v in value)
    if isinstance(value, dict):
        return bool(value) and all(numeric(v) for v in value.values())
    return False


def module_names(module) -> list[str]:
    """The public UPPER_CASE names a module assigns at its top level (an imported name is not one)."""
    names = []
    for node in ast.parse(Path(module.__file__).read_text()).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) \
            else []
        for target in targets:
            for t in (target.elts if isinstance(target, ast.Tuple) else [target]):
                if isinstance(t, ast.Name) and t.id.isupper() and not t.id.startswith("_"):
                    names.append(t.id)
    return names


def scanned_modules():
    """(listing prefix, module) for every module of the core (not its test support) and every dola pure module."""
    for info in pkgutil.walk_packages(relay_core.__path__, "relay_core."):
        if not info.name.startswith("relay_core.testing"):
            yield info.name.removeprefix("relay_core."), importlib.import_module(info.name)
    yield from ((m.__name__, m) for m in map(importlib.import_module, (
        "relay_dola." + i.name for i in pkgutil.iter_modules(relay_dola.__path__)
        if not i.ispkg and i.name != "e2e")))


def numeric_constants() -> dict[str, object]:
    found = {}
    for prefix, module in scanned_modules():
        for name in module_names(module):
            if numeric(getattr(module, name)):
                found["%s.%s" % (prefix, name)] = getattr(module, name)
    return found


def listed() -> dict[str, object]:
    return {f.name: f.value for f in (*CORE, *DOLA.limits)}


@mutation(LIMITS, MAX_INFLIGHT_ROW, "", expect="jobs.engine.MAX_INFLIGHT")
def test_every_numeric_constant_is_on_the_card_or_excused_with_a_reason():
    shown, found = listed(), numeric_constants()
    unlisted = sorted(n for n in found if n not in shown and n not in NOT_SHOWN)
    assert not unlisted, "list these in relay_core.limits (or the provider's limits) or excuse them: %s" % unlisted
    both = sorted(n for n in found if n in shown and n in NOT_SHOWN)
    assert not both, "listed and excused at once: %s" % both


def test_every_excuse_still_names_a_numeric_constant():
    stale = sorted(set(NOT_SHOWN) - set(numeric_constants()))
    assert not stale, "excused names that no longer exist or are no longer numeric: %s" % stale
    assert all(reason.strip() for reason in NOT_SHOWN.values())


def test_every_listed_value_is_the_live_constant():
    found, wrong = numeric_constants(), []
    for name, value in listed().items():
        if found.get(name, object()) != value:
            wrong.append(name)
    assert not wrong, "listed rows that are not a numeric constant, or not its value: %s" % wrong
