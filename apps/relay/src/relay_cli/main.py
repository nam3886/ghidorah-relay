"""`relay serve | worker --provider X | migrate | admin` (contracts §2).

Imports happen per subcommand, because they are the process boundary: `serve` loads relay_core and
every provider's spec and never an engine; `worker --provider X` loads X's engine, and so pulls in
relay_browser only when X is a browser provider.
"""

import argparse
import importlib
import sys
from types import ModuleType

# The provider registry is an explicit list; entry-point discovery is not used (contracts §2).
PROVIDERS = {"dola": "relay_dola", "vibes": "relay_vibes", "meta": "relay_meta"}


def load_specs() -> dict[str, ModuleType]:
    return {name: importlib.import_module(pkg + ".spec") for name, pkg in PROVIDERS.items()}


def load_engine(provider: str) -> ModuleType:
    importlib.import_module(PROVIDERS[provider] + ".spec")
    return importlib.import_module(PROVIDERS[provider] + ".engine")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="relay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="public and admin API")
    worker = sub.add_parser("worker", help="the worker of one provider")
    worker.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    sub.add_parser("migrate", help="apply the SQL migrations (the only DDL path)")
    sub.add_parser("admin", help="provisioning commands")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        importlib.import_module("relay_core")
        load_specs()
    elif args.cmd == "worker":
        load_engine(args.provider)
    print("relay %s: not built yet" % args.cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
