#!/usr/bin/env python3
"""Import cookie jars and their proxies into a running relay with one POST /admin/accounts/import (stdlib only).

  import-accounts.py --cookies FILE --proxies FILE [--provider dola] [--label-prefix n11] [--base http://127.0.0.1:8100]
                     [--username NAME] [--dry-run]

Line i of the cookie file (JSON with a "cookies" list) pairs with line i of the proxy file (`host:port:user:password`,
or already `user:password@host:port`); the two files must have the same number of lines. The admin password is read from
the terminal without echo (getpass; no terminal, no run), the session token lives in memory for this run and is never
written (the run logs out at the end), and the server is only reached on loopback: the connection ignores http_proxy and
refuses every redirect. Both files must be 0600. The output is counts and the server's known rejection reasons (anything
else prints as "other"), never a cookie, a proxy line, the password or the token. --dry-run parses the files and prints
the counts without any network call. Every account gets a label (`--label-prefix`, default `imp-` plus the start time);
the account count is printed before and after the import. Before posting, a label that already exists stops the run
(exit 2, nothing sent), so an older account can never be taken for this import. The import runs alone (the outcome check
reads the newest 200 accounts). If the answer to the import is lost (no answer, a cut-off or
garbled answer, a 5xx), the script reads the accounts again and counts those with the posted labels created since the
request started: that decides "applied" or "not applied" (never re-run before reading that line). Exit status: 0 all
imported, 1 some rejected, 2 refused or failed (nothing applied that we know of), 3 outcome unknown (list the accounts
before running again), 4 the answer was lost but the import WAS applied (do not run it again).
"""

from __future__ import annotations

import argparse
import getpass
import http.client
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from urllib.parse import quote

LOOPBACK = ("127.0.0.1", "localhost", "::1")
MAX_ITEMS = 200               # the server's limit for one import
AT_FORM_TAIL = re.compile(r"[^:@/]+:\d+")          # what follows the last @ in user:password@host:port
REASONS = ("credential_unparseable", "proxy_invalid")     # what the import can answer; the rest prints as "other"


class Refused(Exception):
    code = 2


class Unknown(Refused):
    code = 3


class Applied(Refused):
    code = 4


class Unsure(Exception):
    """The request was sent and the answer did not come back: the server may or may not have applied it."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None                                   # the session header must never follow


# no ProxyHandler entries: http_proxy / HTTPS_PROXY in the environment must not see the password or the token
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


def private_lines(path: str) -> list[str]:
    try:
        mode = os.stat(path).st_mode
    except OSError:
        raise Refused("an input file cannot be read") from None
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise Refused("an input file is not a regular file with mode 0600")
    try:
        with open(path, encoding="utf-8") as f:
            return [ln.strip() for ln in f if ln.strip()]
    except (OSError, UnicodeDecodeError):
        raise Refused("an input file cannot be read as UTF-8 text") from None


def proxy_of(line: str) -> str:
    if "@" in line and AT_FORM_TAIL.fullmatch(line.rsplit("@", 1)[1]):
        return line                                    # already user:password@host:port (URL rules: sent as it is)
    parts = line.split(":", 3)
    if len(parts) != 4 or not parts[1].isdigit():
        raise Refused("a proxy line is not host:port:user:password")
    host, port, user, password = parts
    return "%s:%s@%s:%s" % (quote(user, safe=""), quote(password, safe=""), host, port)


def build_items(cookie_lines: list[str], proxy_lines: list[str], prefix: str) -> list[dict]:
    if len(cookie_lines) != len(proxy_lines):
        raise Refused("the files differ in length: %d cookie lines, %d proxy lines"
                      % (len(cookie_lines), len(proxy_lines)))
    if not 0 < len(cookie_lines) <= MAX_ITEMS:
        raise Refused("between 1 and %d lines are imported at once" % MAX_ITEMS)
    items = []
    for i, (cookies, proxy) in enumerate(zip(cookie_lines, proxy_lines, strict=True)):
        try:
            line = json.loads(cookies)
            jar = line["cookies"]
        except (ValueError, KeyError, TypeError):
            raise Refused('cookie line %d is not JSON with a "cookies" list' % (i + 1)) from None
        item = {"credential": json.dumps({"cookies": jar}), "proxy": proxy_of(proxy), "label": "%s-%02d" % (prefix, i)}
        email = line.get("email")
        if isinstance(email, str) and email.strip():
            item["identity"] = email.strip()          # the account's identity (gemini: its Google account email)
        items.append(item)
    return items


def call(base: str, path: str, body: dict | None, token: str | None = None, sent: bool = False) -> tuple[int, dict]:
    """One request. With `sent`, a failure after the request left is Unsure (the server may have applied it)."""
    headers = {"content-type": "application/json"}
    if token:
        headers["X-Admin-Session"] = token
    req = urllib.request.Request(base + path, method="GET" if body is None else "POST",
                                 data=None if body is None else json.dumps(body).encode(), headers=headers)
    try:
        with OPENER.open(req, timeout=120) as r:
            answer = json.load(r)
            return r.status, answer if isinstance(answer, dict) else {}
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        if sent:
            raise Unsure from None
        raise Refused("the server could not be reached") from None


def ask_password() -> str:
    if not sys.stdin.isatty():
        raise Refused("the password is asked on a terminal: run this through ssh -t")
    return getpass.getpass("admin password: ")


def labelled_since(base: str, provider: str, token: str, labels: set[str], since_ms: float) -> int | None:
    """How many accounts with one of these labels were created since `since_ms`; None when that cannot be read."""
    status, body = call(base, "/admin/accounts?provider=%s&limit=%d" % (urllib.parse.quote(provider), MAX_ITEMS),
                        None, token)
    rows = body.get("items")
    if status != 200 or not isinstance(rows, list):
        return None
    return sum(1 for r in rows if isinstance(r, dict) and r.get("label") in labels
               and isinstance(r.get("created_ms"), (int, float)) and r["created_ms"] >= since_ms)


def existing_labels(base: str, provider: str, token: str) -> set[str] | None:
    """Every label the provider's accounts carry now, read page by page; None when the list cannot be read."""
    found: set[str] = set()
    offset = 0
    while True:
        status, body = call(base, "/admin/accounts?provider=%s&limit=%d&offset=%d"
                            % (urllib.parse.quote(provider), MAX_ITEMS, offset), None, token)
        rows, total = body.get("items"), body.get("total")
        if status != 200 or not isinstance(rows, list) or not isinstance(total, int):
            return None
        found.update(r["label"] for r in rows if isinstance(r, dict) and isinstance(r.get("label"), str))
        offset += MAX_ITEMS
        if not rows or offset >= total:
            return found


def account_count(base: str, provider: str, token: str) -> int | None:
    status, body = call(base, "/admin/accounts?provider=%s&limit=1" % urllib.parse.quote(provider), None, token)
    total = body.get("total")
    return total if isinstance(total, int) else None


def run(args: argparse.Namespace) -> int:
    host = urllib.parse.urlparse(args.base).hostname
    if host not in LOOPBACK:
        raise Refused("the server must be on loopback (127.0.0.1)")
    started = time.time()
    prefix = args.label_prefix or time.strftime("imp-%Y%m%d%H%M%S", time.localtime(started))
    items = build_items(private_lines(args.cookies), private_lines(args.proxies), prefix)
    labels = {i["label"] for i in items}
    print("lines: %d cookie jars paired with %d proxies" % (len(items), len(items)))
    if args.dry_run:
        print("dry run: nothing sent")
        return 0
    token = None
    try:
        status, body = call(args.base, "/admin/login", {"username": args.username, "password": ask_password()})
        if status != 200 or not isinstance(body.get("token"), str):
            raise Refused("login refused (status %d)" % status)
        token = body["token"]
        have = existing_labels(args.base, args.provider, token)
        if have is None:
            raise Refused("the existing accounts could not be read, so the labels cannot be checked: nothing was sent")
        taken = len(labels & have)
        if taken:
            raise Refused("%d of the labels to post already exist: use another --label-prefix, nothing was sent"
                          % taken)
        before = account_count(args.base, args.provider, token)
        print("accounts before: %s" % ("unknown" if before is None else before))
        try:
            status, body = call(args.base, "/admin/accounts/import", {"provider": args.provider, "items": items},
                                token, sent=True)
            if status >= 500:
                raise Unsure                                  # the server may have applied it before it failed
        except Unsure:
            try:
                found = labelled_since(args.base, args.provider, token, labels, started * 1000 - 2000)
            except Refused:
                found = None
            if found is None:
                raise Unknown("outcome unknown: the answer to the import was lost and the accounts could not be read; "
                              "list the accounts with the label prefix %s before running again" % prefix) from None
            if found == 0:
                raise Refused("the answer was lost and no account with the posted labels exists: the import was NOT "
                              "applied, running it again is safe") from None
            raise Applied("the answer was lost and %d accounts with the posted labels exist: the import WAS applied, "
                          "do not run it again" % found) from None
        after = account_count(args.base, args.provider, token) if status == 200 else None
    finally:
        if token:
            try:
                call(args.base, "/admin/logout", {}, token)
            except Refused:
                pass
        token = None
        items_count = len(items)
        items.clear()
    if status != 200:
        raise Refused("the import was refused (status %d)" % status)
    done = body.get("imported") if isinstance(body.get("imported"), list) else []
    refused = body.get("rejected") if isinstance(body.get("rejected"), list) else []
    rejected = Counter(r.get("reason") if isinstance(r, dict) and r.get("reason") in REASONS else "other"
                       for r in refused)
    print("imported: %d, rejected: %d" % (len(done), sum(rejected.values())))
    for reason, n in sorted(rejected.items()):
        print("  rejected %s: %d" % (reason, n))
    print("accounts after: %s" % ("unknown" if after is None else after))
    if len(done) + len(refused) != items_count:
        raise Refused("the answer does not account for every line (%d of %d): check the account list before running "
                      "again" % (len(done) + len(refused), items_count))
    return 1 if rejected else 0


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("usage error (see the header of scripts/import-accounts.py)")   # argparse would echo it


def main(argv: list[str]) -> int:
    ap = Parser(prog="import-accounts.py", allow_abbrev=False)
    ap.add_argument("--cookies", required=True)
    ap.add_argument("--proxies", required=True)
    ap.add_argument("--provider", default="dola")
    ap.add_argument("--label-prefix", default="")
    ap.add_argument("--base", default="http://127.0.0.1:8100")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--dry-run", action="store_true")
    try:
        return run(ap.parse_args(argv))
    except Refused as e:
        print("import-accounts: %s" % e, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
