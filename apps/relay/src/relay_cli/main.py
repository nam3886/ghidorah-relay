"""`relay serve | worker --provider X | migrate | admin | e2e --provider X` (contracts §2).

Imports happen per subcommand, because they are the process boundary: `serve` loads relay_core and
every provider's spec and never an engine; `worker --provider X` loads X's engine, and so pulls in
relay_browser only when X is a browser provider; `e2e --provider X` loads the same plus X's e2e module.
"""

import argparse
import importlib
import os
import sys
from types import ModuleType

# The provider registry is an explicit list; entry-point discovery is not used (contracts §2).
PROVIDERS = {"dola": "relay_dola", "vibes": "relay_vibes", "meta": "relay_meta"}


def load_specs() -> dict[str, ModuleType]:
    return {name: importlib.import_module(pkg + ".spec") for name, pkg in PROVIDERS.items()}


def load_engine(provider: str) -> ModuleType:
    importlib.import_module(PROVIDERS[provider] + ".spec")
    return importlib.import_module(PROVIDERS[provider] + ".engine")


def load_e2e(provider: str) -> ModuleType:
    load_engine(provider)
    return importlib.import_module(PROVIDERS[provider] + ".e2e")


def worker(provider: str) -> int:
    """Run the worker of one provider until SIGTERM/SIGINT or its error limit; the exit code is the process's.

    A provider package offers `spec.SPEC` (its ProviderSpec) and `engine.build_engine(spec, settings)`; the engine
    module is imported only here, so a browser provider pulls in the browser kit and no other process does.

    `WORKER_ID` (default `worker-<provider>`) names the process's heartbeat row: it must be unique per worker
    process and contain no "/"; a worker that finds a "/" in it refuses to start (exit 2)."""
    import asyncio
    import logging

    from relay_core.jobs.worker import EXIT_REFUSED, serve

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = load_engine(provider)
    spec_module = importlib.import_module(PROVIDERS[provider] + ".spec")
    spec, build = getattr(spec_module, "SPEC", None), getattr(engine, "build_engine", None)
    if spec is None or build is None:
        print("relay worker: provider %s has no SPEC and build_engine yet" % provider, file=sys.stderr)
        return EXIT_REFUSED
    return asyncio.run(serve(spec, build, os.environ))


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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="relay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="public and admin API")
    worker_help = "the worker of one provider (env WORKER_ID: unique per worker process, no '/')"
    for cmd, help_text in (("worker", worker_help), ("e2e", "live end-to-end run of one provider")):
        sub.add_parser(cmd, help=help_text).add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    sub.add_parser("migrate", help="apply the SQL migrations (the only DDL path)")
    sub.add_parser("admin", help="provisioning commands")
    args = ap.parse_args(argv)

    if args.cmd == "migrate":
        return migrate()
    if args.cmd == "serve":
        importlib.import_module("relay_core")
        load_specs()
    elif args.cmd == "worker":
        return worker(args.provider)
    elif args.cmd == "e2e":
        load_e2e(args.provider)
    print("relay %s: not built yet" % args.cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
