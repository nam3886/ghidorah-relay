"""`relay serve | worker --provider X | migrate | admin | e2e --provider X` (contracts §2).

Imports happen per subcommand, because they are the process boundary: `serve` loads relay_core and
every provider's spec and never an engine; `worker --provider X` loads X's engine, and so pulls in
relay_browser only when X is a browser provider; `e2e --provider X` loads the same plus X's e2e module.
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


def load_e2e(provider: str) -> ModuleType:
    load_engine(provider)
    return importlib.import_module(PROVIDERS[provider] + ".e2e")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="relay")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="public and admin API")
    for cmd, help_text in (("worker", "the worker of one provider"), ("e2e", "live end-to-end run of one provider")):
        sub.add_parser(cmd, help=help_text).add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    sub.add_parser("migrate", help="apply the SQL migrations (the only DDL path)")
    sub.add_parser("admin", help="provisioning commands")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        importlib.import_module("relay_core")
        load_specs()
    elif args.cmd == "worker":
        load_engine(args.provider)
    elif args.cmd == "e2e":
        load_e2e(args.provider)
    print("relay %s: not built yet" % args.cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
