"""`relay serve | worker | migrate | admin | keygen | key | provider | jobs | e2e`.

Imports happen per subcommand, because they are the process boundary: `serve` loads relay_core and
every provider's spec and never an engine; `worker --provider X` loads X's engine, and so pulls in
relay_browser only when X is a browser provider; `e2e --provider X` loads the same plus X's e2e module.

Every command that touches the database reads `DATABASE_URL` and runs as the role that URL names: `serve`, `admin`,
`key` and `provider` as `relay_api`, `jobs restore-mark` as `relay_worker` (or the owner), `migrate` as the owner.
Exit codes: 0 done, 1 failed, 2 refused (a configuration or an argument that must change first).
"""

import argparse
import asyncio
import getpass
import importlib
import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from types import ModuleType
from typing import Any

from relay_core.redact import install_log_redaction, redact, redact_text

# The provider registry is an explicit list; entry-point discovery is not used.
PROVIDERS = {"dola": "relay_dola", "vibes": "relay_vibes", "meta": "relay_meta", "flow": "relay_flow"}
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
DEFAULT_PORT = 8100
API_POOL_MAX = 15            # the api's pool; a worker's is 10
SHUTDOWN_GRACE_S = 25         # under the api container stop grace (30 s): the runners and the pool still close


class CliError(Exception):
    """A command that stops with a message and an exit code."""

    def __init__(self, message: str, code: int = EXIT_FAILED):
        super().__init__(message)
        self.code = code


def load_specs() -> dict[str, ModuleType]:
    return {name: importlib.import_module(pkg + ".spec") for name, pkg in PROVIDERS.items()}


def load_engine(provider: str) -> ModuleType:
    importlib.import_module(PROVIDERS[provider] + ".spec")
    return importlib.import_module(PROVIDERS[provider] + ".engine")


def load_e2e(provider: str) -> ModuleType:
    load_engine(provider)
    return importlib.import_module(PROVIDERS[provider] + ".e2e")


def built_specs(modules: Mapping[str, ModuleType]) -> list[Any]:
    """The `SPEC` of every provider package that has one; a package still empty (not built yet) is left out."""
    return [m.SPEC for _, m in sorted(modules.items()) if getattr(m, "SPEC", None) is not None]


def worker(provider: str) -> int:
    """Run the worker of one provider until SIGTERM/SIGINT or its error limit; the exit code is the process's.

    A provider package offers `spec.SPEC` (its ProviderSpec) and `engine.build_engine(spec, settings)`; the engine
    module is imported only here, so a browser provider pulls in the browser kit and no other process does.

    `WORKER_ID` (default `worker-<provider>`) names the process's heartbeat row: it must be unique per worker
    process and contain no "/"; a worker that finds a "/" in it refuses to start (exit 2)."""
    from relay_core.jobs.worker import EXIT_REFUSED as REFUSED
    from relay_core.jobs.worker import serve as serve_worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_log_redaction()
    engine = load_engine(provider)
    spec_module = importlib.import_module(PROVIDERS[provider] + ".spec")
    spec, build = getattr(spec_module, "SPEC", None), getattr(engine, "build_engine", None)
    if spec is None or build is None:
        print("relay worker: provider %s has no SPEC and build_engine yet" % provider, file=sys.stderr)
        return REFUSED
    from relay_core.storage import ObjectStore, StorageError

    async def make_bucket() -> None:
        await ObjectStore.from_env(os.environ).ensure_bucket()      # a fresh store has none: the first screenshot fails

    try:
        asyncio.run(make_bucket())
    except StorageError as e:
        print("relay worker: object store is not usable at start: %s" % e, file=sys.stderr)
        return EXIT_REFUSED if "is not set" in str(e) else EXIT_FAILED
    return asyncio.run(serve_worker(spec, build, os.environ))


def migrate() -> int:
    """Apply the SQL migrations with the owner role (`DATABASE_URL`); give the service roles their passwords."""
    import psycopg

    from relay_core.db import ROLES, run_migrations

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("relay migrate: DATABASE_URL is not set", file=sys.stderr)
        return 1
    passwords = {role: os.environ.get(role.upper() + "_DB_PASSWORD", "") for role in ROLES}
    try:
        applied = run_migrations(dsn, passwords=passwords, billing_tz=os.environ.get("BILLING_TZ") or None)
    except (psycopg.Error, RuntimeError, ValueError) as e:
        print("relay migrate: %s" % str(e).strip().splitlines()[0], file=sys.stderr)
        return 1
    print("relay migrate: applied %s" % (", ".join("%04d" % v for v in applied) or "nothing (up to date)"))
    return 0


# ---- serve --------------------------------------------------------------------------------------------------------
def private_key_vars(env: Mapping[str, str]) -> list[str]:
    """Names (never values) of the environment variables that hold a private key."""
    return sorted(k for k, v in env.items() if "PRIVATE_KEY" in k.upper() and v.strip())


def settings_registry(specs: list[Any]):
    from relay_core.settings import REGISTRY, Registry

    return Registry(*REGISTRY.specs.values(), *(s for spec in specs for s in spec.settings().values()))


def redact_arg(value: Any) -> Any:
    """One log argument, redacted and still fit for `%s`/`%d`: numbers and None as they are, text and containers
    through the redactor (a tuple stays a tuple), any other object (an exception) as the redacted text of `str()`."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (dict, list, tuple)):
        out = redact(value)
        return tuple(out) if isinstance(value, tuple) else out
    return redact_text(str(value))


class AccessPathOnly(logging.Filter):
    """uvicorn's access line carries the request path with its query string, and a client that sends `?key=rk-...`
    (the API refuses it, but the line is written first) would leave its raw key in the process log. This keeps the
    path, drops the query and redacts every text argument. uvicorn's access args are (client, method,
    path-with-query, http version, status) and its formatter unpacks them, so the access handler must not get
    `LogRedactor` (that folds the args into the message and leaves none: every line then fails with "Logging error")."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            if len(args) == 5 and isinstance(args[2], str):
                args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
            record.args = tuple(redact_arg(a) for a in args)
        elif isinstance(args, Mapping):             # `msg % {"name": ...}`: logging keeps a lone dict argument
            record.args = {k: redact_arg(v) for k, v in args.items()}
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.exc_info and not record.exc_text:             # as LogRedactor: the traceback, whole and redacted
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


def uvicorn_config(app, port: int):
    """The api server. A stop signal waits at most SHUTDOWN_GRACE_S for open requests (a held facade request can
    last 30 min); then they are cut, their jobs run on, and serve goes on to stop the runners and close the pool.
    The access log loses the query string and is redacted per argument (`AccessPathOnly`)."""
    import uvicorn

    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info", server_header=False,
                            proxy_headers=False,                  # the app reads X-Forwarded-* itself
                            timeout_graceful_shutdown=SHUTDOWN_GRACE_S)
    access = logging.getLogger("uvicorn.access")        # uvicorn set its logging up in Config(): install after it
    if not any(isinstance(f, AccessPathOnly) for f in access.filters):
        access.addFilter(AccessPathOnly())
    install_log_redaction("uvicorn")                    # not uvicorn.access: see AccessPathOnly
    return config


async def run_uvicorn(app, port: int) -> None:
    import uvicorn

    await uvicorn.Server(uvicorn_config(app, port)).serve()          # returns on SIGTERM / SIGINT


async def serve_api(specs: list[Any], env: Mapping[str, str], *,
                    http: Callable[[Any, int], Awaitable[None]] | None = None) -> int:
    """The api process: public and admin routes on one port, the webhook sender and the global sweeps.

    Refuses (exit 2) before it connects when a private key is set (the api cannot open a sealed credential and must
    never be able to), when the credential PUBLIC key is missing or malformed (account import and proxy edits seal
    with it), when the database or object store settings are missing, or when the object-store secret is empty or an
    example value; when the bucket cannot be made (exit 1, before the pool opens); after it connects, when
    BILLING_TZ differs from the pin. Then every (provider, kind) pair the registered specs declare gets a closed
    wallet on the keys that exist (I29), each shipped default prompt frame without a row is seeded, the app is
    built, and `http` serves until a stop signal. Shutdown order: stop serving, stop the runners, then the pool."""
    from relay_core import billing, crypto, frames
    from relay_core.api import runners
    from relay_core.api.app import create_app
    from relay_core.db import open_pool, tx
    from relay_core.providers import ProviderRegistry
    from relay_core.settings import Settings
    from relay_core.storage import ObjectStore, StorageError

    held = private_key_vars(env)
    if held:
        raise CliError("%s is set: the api holds only the credential public key; remove the private key from its "
                       "environment" % ", ".join(held), EXIT_REFUSED)
    dsn = env.get("DATABASE_URL", "")
    if not dsn:
        raise CliError("DATABASE_URL is not set", EXIT_REFUSED)
    from relay_core.admin.users import is_example_secret
    if is_example_secret(env.get("MINIO_SECRET_KEY")):
        raise CliError("MINIO_SECRET_KEY is empty or an example value: set the real object-store secret", EXIT_REFUSED)
    try:
        crypto.seal(crypto.public_key_from_env(env), b"")        # present and a real key: imports and edits need it
        store = ObjectStore.from_env(env)
        port = int(env.get("PORT") or DEFAULT_PORT)
        registry = ProviderRegistry()
        for spec in specs:
            registry.register(spec)
    except (crypto.CryptoError, StorageError, ValueError) as e:
        raise CliError(str(e), EXIT_REFUSED) from None

    try:
        await store.ensure_bucket()              # a fresh object store has no bucket: the first media write would fail
    except StorageError as e:
        raise CliError("object store is not usable at start: %s" % e) from None

    pool = await open_pool(dsn, max_size=API_POOL_MAX, name="relay-api")
    settings = Settings(pool, dsn, registry=settings_registry(specs))
    running = None
    try:
        await settings.start()
        try:
            async with tx(pool) as conn:
                await billing.verify_billing_tz(conn, env.get("BILLING_TZ", ""))
        except billing.BillingTzMismatch as e:
            raise CliError(str(e), EXIT_REFUSED) from None
        async with tx(pool) as conn:
            for spec in specs:
                await billing.ensure_wallets(conn, spec.name, spec.caps.wallet_kinds)
                if getattr(spec, "frames", None) is not None:       # version 1 of each shipped default
                    await frames.seed(conn, spec.name, spec.frames.defaults)
        app = create_app(pool=pool, settings=settings, store=store, registry=registry)
        running = runners.start_runners(pool, settings, store)
        await (http or run_uvicorn)(app, port)
        return EXIT_OK
    finally:
        if running is not None:
            await running.stop()
        await settings.stop()
        await pool.close()


# ---- database commands --------------------------------------------------------------------------------------------
async def with_pool(fn: Callable[[Any], Awaitable[int]], *, options: str | None = None) -> int:
    from relay_core.db import open_pool

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        raise CliError("DATABASE_URL is not set", EXIT_REFUSED)
    pool = await open_pool(dsn, max_size=2, name="relay-cli", options=options)
    try:
        return await fn(pool)
    finally:
        await pool.close()


def read_password() -> str:
    """Typed twice without echo on a terminal; one line from a pipe otherwise. Never an argument."""
    if sys.stdin.isatty():
        first = getpass.getpass("password: ")
        if getpass.getpass("again: ") != first:
            raise CliError("the two passwords differ", EXIT_REFUSED)
        return first
    return sys.stdin.readline().rstrip("\r\n")


def create_user(username: str) -> int:
    from relay_core.admin import users

    password = read_password()

    async def run(pool) -> int:
        try:
            await users.create_user(pool, username, password)
        except users.UserError as e:                 # includes UserExists
            raise CliError(str(e), EXIT_REFUSED) from None
        print("relay admin: created user %s" % username.strip())
        return EXIT_OK

    return asyncio.run(with_pool(run))


def keygen() -> int:
    """A new credential key pair. The private key goes only to the worker env files and to a copy kept off the box."""
    from relay_core import crypto

    pair = crypto.generate_keypair()
    print("# key id %s" % pair.key_id)
    print("%s=%s   # api.env and the admin's import tool" % (crypto.PUBLIC_KEY_VAR, pair.public))
    print("%s=%s   # worker env files only; keep a copy off the box" % (crypto.PRIVATE_KEY_VAR, pair.private))
    return EXIT_OK


def parse_wallets(items: list[str]) -> dict[tuple[str, str], tuple[int | None, int | None]]:
    """`provider:kind=N` (a day limit) or `provider:kind=unlimited`; a pair not named stays closed."""
    wallets: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for item in items:
        pair, _, limit = item.partition("=")
        provider, _, kind = pair.partition(":")
        if not (provider and kind and limit):
            raise CliError("--wallet wants provider:kind=N or provider:kind=unlimited, got %r" % item, EXIT_REFUSED)
        if limit == "unlimited":
            wallets[(provider, kind)] = (None, None)
        elif limit.isdigit():
            wallets[(provider, kind)] = (int(limit), None)
        else:
            raise CliError("--wallet limit must be a number or 'unlimited', got %r" % limit, EXIT_REFUSED)
    return wallets


def key_create(specs: list[Any], label: str, models: list[str], max_unfinished: int | None,
               wallet_args: list[str]) -> int:
    from relay_core import keys
    from relay_core.db import tx

    declared = [(s.name, kind) for s in specs for kind in s.caps.wallet_kinds]
    wallets = parse_wallets(wallet_args)
    unknown = sorted(set(wallets) - set(declared))
    if unknown:
        raise CliError("no such wallet: %s (declared: %s)" % (
            ", ".join("%s:%s" % p for p in unknown), ", ".join("%s:%s" % p for p in declared) or "none"),
            EXIT_REFUSED)

    async def run(pool) -> int:
        try:
            async with tx(pool) as conn:
                made = await keys.create_key(conn, label=label, models=models or (keys.ANY_MODEL,),
                                             max_unfinished=max_unfinished, declared=declared, wallets=wallets)
        except ValueError as e:
            raise CliError(str(e), EXIT_REFUSED) from None
        print("created key %s" % made.id)
        print("%s" % made.raw)                  # shown once: only its hash is stored
        return EXIT_OK

    return asyncio.run(with_pool(run))


def key_disable(key_id: str) -> int:
    """A key is disabled here, never deleted (only a key with no jobs may be, and that is the admin's call)."""
    from relay_core import keys
    from relay_core.db import tx

    try:
        wanted = uuid.UUID(key_id)
    except ValueError:
        raise CliError("%r is not a key id" % key_id, EXIT_REFUSED) from None

    async def run(pool) -> int:
        async with tx(pool) as conn:
            found = await keys.set_enabled(conn, wanted, False)
        if not found:
            raise CliError("no key %s" % wanted)
        print("disabled key %s" % wanted)
        return EXIT_OK

    return asyncio.run(with_pool(run))


def provider_mode(specs: list[Any], provider: str, drain: bool) -> int:
    from relay_core.jobs import ops
    from relay_core.settings import Settings

    if provider not in {s.name for s in specs}:
        raise CliError("no provider %r (built: %s)" % (provider, ", ".join(s.name for s in specs) or "none"),
                       EXIT_REFUSED)

    async def run(pool) -> int:
        settings = Settings(pool, os.environ["DATABASE_URL"], registry=settings_registry(specs))
        changed = await (ops.drain if drain else ops.resume)(settings, provider)
        print("%s %s%s" % ("draining" if drain else "resumed", provider, "" if changed else " (no change)"))
        return EXIT_OK

    return asyncio.run(with_pool(run))


def restore_mark(dump_time: str) -> int:
    """After a restore, with NO worker running (runbook): jobs that may have been sent after the dump
    become `unknown`. Refuses while a worker heartbeat is fresh."""
    from relay_core.db import session_options
    from relay_core.jobs import ops

    try:
        at = datetime.fromisoformat(dump_time)
    except ValueError:
        raise CliError("--dump-time %r is not an ISO 8601 time, e.g. 2026-10-03T02:00:00+07:00" % dump_time,
                       EXIT_REFUSED) from None
    if at.tzinfo is None:
        raise CliError("--dump-time needs a timezone offset, e.g. 2026-10-03T02:00:00+07:00", EXIT_REFUSED)

    async def run(pool) -> int:
        try:
            marked = await ops.restore_mark(pool, at)
        except ops.WorkersRunning as e:
            raise CliError(str(e), EXIT_FAILED) from None
        print("marked %d job(s) unknown" % marked)
        return EXIT_OK

    return asyncio.run(with_pool(run, options=session_options(statement_timeout_s=600, lock_timeout_s=30)))


def e2e(provider: str) -> int:
    """The live end-to-end run of one provider: its `e2e` module's `run()`. A provider without one refuses."""
    module = sys.modules[PROVIDERS[provider] + ".e2e"]       # loaded by `load_e2e` in main
    run = getattr(module, "run", None)
    if run is None:
        raise CliError("provider %s has no e2e run yet (built with the provider)" % provider,
                       EXIT_REFUSED)
    return int(asyncio.run(run(os.environ)))


# ---- entry --------------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="relay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="public and admin API (env: DATABASE_URL, CREDENTIAL_SEAL_PUBLIC_KEY, MINIO_*, PORT)")
    worker_help = "the worker of one provider (env WORKER_ID: unique per worker process, no '/')"
    for cmd, help_text in (("worker", worker_help), ("e2e", "live end-to-end run of one provider")):
        sub.add_parser(cmd, help=help_text).add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    sub.add_parser("migrate", help="apply the SQL migrations (the only DDL path)")
    admin = sub.add_parser("admin", help="admin users").add_subparsers(dest="admin_cmd", required=True)
    admin.add_parser("create-user", help="make an admin user; the password is read from the terminal, not argv") \
        .add_argument("username")
    sub.add_parser("keygen", help="a new credential key pair, printed once")
    key = sub.add_parser("key", help="API keys").add_subparsers(dest="key_cmd", required=True)
    create = key.add_parser("create", help="make a key; the raw key is printed once")
    create.add_argument("--label", default="")
    create.add_argument("--models", nargs="+", default=[], help="allow-list (default: every model)")
    create.add_argument("--max-unfinished", type=int, default=None)
    create.add_argument("--wallet", action="append", default=[], metavar="PROVIDER:KIND=N|unlimited",
                        help="open a wallet with a day limit; unnamed wallets stay closed")
    key.add_parser("disable", help="disable a key").add_argument("key_id")
    provider = sub.add_parser("provider", help="drain or resume a provider").add_subparsers(dest="mode", required=True)
    provider.add_parser("drain", help="stop claiming new work; open jobs still finish").add_argument("name")
    provider.add_parser("resume", help="claim again").add_argument("name")
    jobs = sub.add_parser("jobs", help="job operations").add_subparsers(dest="jobs_cmd", required=True)
    jobs.add_parser("restore-mark", help="after a restore, with no worker running").add_argument(
        "--dump-time", required=True, help="ISO 8601 time of the dump, with an offset")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "migrate":
            return migrate()
        if args.cmd == "serve":
            importlib.import_module("relay_core")
            specs = built_specs(load_specs())
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            install_log_redaction()
            return asyncio.run(serve_api(specs, os.environ))
        elif args.cmd == "worker":
            return worker(args.provider)
        elif args.cmd == "e2e":
            load_e2e(args.provider)
            return e2e(args.provider)
        elif args.cmd == "keygen":
            return keygen()
        elif args.cmd == "admin":
            return create_user(args.username)
        elif args.cmd == "key":
            specs = built_specs(load_specs())
            if args.key_cmd == "create":
                return key_create(specs, args.label, args.models, args.max_unfinished, args.wallet)
            return key_disable(args.key_id)
        elif args.cmd == "provider":
            return provider_mode(built_specs(load_specs()), args.name, args.mode == "drain")
        elif args.cmd == "jobs":
            return restore_mark(args.dump_time)
    except CliError as e:
        print("relay %s: %s" % (args.cmd, e), file=sys.stderr)
        return e.code
    except Exception as e:                       # a database or store failure: one line, no trace, no secrets
        print("relay %s: %s: %s" % (args.cmd, type(e).__name__, (str(e).strip().splitlines() or [""])[0]),
              file=sys.stderr)
        return EXIT_FAILED
    return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
