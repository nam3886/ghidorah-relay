#!/usr/bin/env python3
"""Find secret values in places they must not be, and print COUNTS only (stdlib only).

  secret-scan.py hashes --secrets FILE... --key KEYFILE --out HASHFILE
      At intake: write a keyed-hash list of every value in the secret files (HMAC-SHA256, the key is made 0600 if the
      file does not exist). Keep both files 0600; the secret files can then be deleted and the scans go on.
  secret-scan.py scan (--secrets FILE... | --hashes HASHFILE --key KEYFILE) [--path P]... [--stdin LABEL]
      Count, per target, how many of the values appear in it. Targets: files and directories by path (the deploy
      directory, the build context, shell histories) and ONE stream on stdin with a label, so these feed in with no
      secret on any command line:
        docker logs relay-api-1 2>&1 | secret-scan.py scan --secrets S --stdin api-logs
        docker inspect relay-worker-dola-1 | secret-scan.py scan --hashes H --key K --stdin inspect
        ps -eo args | secret-scan.py scan --secrets S --stdin process-args
  Exit status: 0 nothing found, 1 something found, 2 usage or an unreadable input.

What is a value: in a JSON line, the `value` of every cookie (and every string under a key named `value` or `password`);
in any other line, the whole line and the user and the password of a proxy (host:port:user:password, or
user:password@host:port with or without a scheme; never the host), and the base64 of `user:password` (the form of a
Proxy-Authorization header). A value under 8 characters is not scanned; the number skipped is printed.
Fixed strings are compared while the secret files are present; with --hashes the data is cut into tokens (runs
without whitespace, quotes, commas or brackets) and every window of a stored length is hashed and compared. A value
that itself holds a separator is also stored as its pieces of 8 characters or more (the count is printed by `hashes`).
The output is a label and numbers (values, occurrences, unreadable, bytes): never a value, a prefix, an offset or a
file name inside a directory. A file or directory that cannot be read, or a link that is not followed, is counted as
`unreadable`, never as clean (exit 2 when nothing was found).
Secret and key files must be 0600 (no group or other access). Nothing is read from the command line but paths.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from urllib.parse import unquote

MIN_LEN = 8
TOKEN = re.compile(rb"[^\s\"'<>,;()\[\]{}]+")
KEYS = ("value", "password")
AT_TAIL = re.compile(r"[^:@/]+:\d+")          # the importer's rule (scripts/import-accounts.py)


class Usage(Exception):
    pass


def private(path: str) -> str:
    """The path, once its file is known to be readable by the owner only."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        raise Usage("cannot read an input file") from None
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise Usage("an input file is not a regular file with mode 0600")
    return path


def json_values(node, out: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k in KEYS and isinstance(v, str):
                out.add(v)
            else:
                json_values(v, out)
    elif isinstance(node, list):
        for v in node:
            json_values(v, out)


def values_of(path: str) -> set[str]:
    found: set[str] = set()
    try:
        with open(private(path), encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        raise Usage("secret input unreadable: a secret file is not valid UTF-8") from None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            json_values(json.loads(line), found)
            continue
        except (ValueError, RecursionError):
            pass
        found.add(line)
        found.update(proxy_values(line))
    return found


def proxy_values(line: str) -> set[str]:
    """The user, the password and base64(user:password) of a proxy line, as written and URL-decoded (the server
    decodes an @-form line); never the host. Same line rule as the importer, and one split at the first colon."""
    if "@" in line and AT_TAIL.fullmatch(line.rsplit("@", 1)[1]):
        creds = line.rsplit("@", 1)[0].split("://")[-1]           # user:password@host:port, with or without a scheme
    elif line.count(":") >= 3:
        creds = line.split(":", 2)[2]                              # host:port:user:password
    else:
        return set()
    out = {creds}
    for pair in {creds, unquote(creds)}:
        user, _, password = pair.partition(":")
        out.update((user, password))
        if password:
            out.add(base64.b64encode(pair.encode()).decode())
    return out


def split_short(values: set[str]) -> tuple[set[bytes], int]:
    keep = {v.encode() for v in values if len(v) >= MIN_LEN}
    return keep, len(values) - len(keep)


def mac(key: bytes, value: bytes) -> str:
    return hmac.new(key, value, hashlib.sha256).hexdigest()


def read_key(path: str) -> bytes:
    with open(private(path), "rb") as f:
        key = f.read().strip()
    if len(key) < 32:
        raise Usage("the key file is too short")
    return key


def write_private(path: str, data: bytes, exclusive: bool = False) -> None:
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    os.fchmod(fd, 0o600)                       # a file that already existed keeps its old mode otherwise
    with os.fdopen(fd, "wb") as f:
        f.write(data)


class Matcher:
    """Counts the distinct values found and their occurrences in a piece of data."""

    def __init__(self, fixed: set[bytes] | None = None, hashes: dict[int, set[str]] | None = None, key: bytes = b""):
        self.fixed, self.hashes, self.key = fixed or set(), hashes or {}, key

    def count(self, data: bytes) -> tuple[set[object], int]:
        distinct: set[object] = set()
        occurrences = 0
        for value in self.fixed:
            n = data.count(value)
            if n:
                distinct.add(value)
                occurrences += n
        if self.hashes:
            for token in TOKEN.finditer(data):
                t = token.group()
                for length, wanted in self.hashes.items():
                    for i in range(len(t) - length + 1):
                        h = mac(self.key, t[i:i + length])
                        if h in wanted:
                            distinct.add(h)
                            occurrences += 1
        return distinct, occurrences

    def clear(self) -> None:
        self.fixed.clear()
        self.hashes.clear()
        self.key = b""


def files_under(path: str, unreadable: list):
    if os.path.islink(path):
        unreadable.append(1)                                    # a linked target is not followed, whatever it points at
    elif os.path.isdir(path):
        for d, dirs, names in os.walk(path, onerror=lambda _e: unreadable.append(1)):
            unreadable.extend(1 for n in dirs if os.path.islink(os.path.join(d, n)))   # linked dirs: not followed
            for name in names:
                p = os.path.join(d, name)
                if os.path.islink(p) or not os.path.isfile(p):
                    unreadable.append(1)                        # not followed, so not scanned: never "clean"
                else:
                    yield p
    else:
        yield path


def scan_target(matcher: Matcher, path_or_data) -> tuple[int, int, int, int]:
    """(distinct values, occurrences, unreadable items, bytes scanned) for a path or for one piece of data."""
    distinct: set[object] = set()
    occurrences = size = 0
    unreadable: list = []
    if isinstance(path_or_data, bytes):
        chunks = [path_or_data]
    else:
        def read():
            for p in files_under(path_or_data, unreadable):
                try:
                    with open(p, "rb") as f:
                        yield f.read()
                except OSError:
                    unreadable.append(1)
        chunks = read()
    for data in chunks:
        d, n = matcher.count(data)
        distinct |= d
        occurrences += n
        size += len(data)
    return len(distinct), occurrences, len(unreadable), size


def cmd_hashes(args) -> int:
    values: set[str] = set()
    for p in args.secrets:
        values |= values_of(p)
    keep, skipped = split_short(values)
    split = 0
    for v in sorted(keep):
        if not TOKEN.fullmatch(v):                              # a separator inside: the data will hold it in pieces
            split += 1
            keep |= {piece for piece in TOKEN.findall(v) if len(piece) >= MIN_LEN}
    if not os.path.exists(args.key):
        write_private(args.key, secrets.token_hex(32).encode(), exclusive=True)
    key = read_key(args.key)
    lines = sorted("%d %s" % (len(v), mac(key, v)) for v in keep)
    write_private(args.out, ("\n".join(lines) + "\n").encode())
    print("hashes: %d written, %d skipped (under %d characters), %d values hold a separator and are also stored "
          "in pieces" % (len(lines), skipped, MIN_LEN, split))
    return 0


def cmd_scan(args) -> int:
    if bool(args.secrets) == bool(args.hashes):
        raise Usage("give --secrets, or --hashes with --key")
    skipped = 0
    if args.secrets:
        values: set[str] = set()
        for p in args.secrets:
            values |= values_of(p)
        fixed, skipped = split_short(values)
        matcher = Matcher(fixed=fixed)
    else:
        if not args.key:
            raise Usage("--hashes needs --key")
        wanted: dict[int, set[str]] = {}
        with open(private(args.hashes), encoding="ascii") as f:
            for line in f:
                if line.strip():
                    n, h = line.split()
                    wanted.setdefault(int(n), set()).add(h)
        matcher = Matcher(hashes=wanted, key=read_key(args.key))
    found = unread = 0
    try:
        targets = []
        for p in args.path:
            if not os.path.exists(p):
                raise Usage("a target path does not exist")
            targets.append((p, p))
        if args.stdin:
            targets.append((args.stdin, sys.stdin.buffer.read()))
        for label, what in targets:
            distinct, occurrences, bad, size = scan_target(matcher, what)
            print("%s: values=%d occurrences=%d unreadable=%d bytes=%d" % (label, distinct, occurrences, bad, size))
            found, unread = found + occurrences, unread + bad
        print("skipped: %d value(s) under %d characters" % (skipped, MIN_LEN))
    finally:
        matcher.clear()
    return 1 if found else (2 if unread else 0)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Usage("usage error (see the header of scripts/secret-scan.py)")    # argparse would echo it


def main(argv: list[str]) -> int:
    ap = Parser(prog="secret-scan.py", description=__doc__.split("\n")[0], allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hashes")
    h.add_argument("--secrets", nargs="+", required=True)
    h.add_argument("--key", required=True)
    h.add_argument("--out", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--secrets", nargs="+")
    s.add_argument("--hashes")
    s.add_argument("--key")
    s.add_argument("--path", action="append", default=[])
    s.add_argument("--stdin", metavar="LABEL")
    try:
        args = ap.parse_args(argv)
        return cmd_hashes(args) if args.cmd == "hashes" else cmd_scan(args)
    except Usage as e:
        print("secret-scan: %s" % e, file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("secret-scan: an input could not be read", file=sys.stderr)       # never the exception text
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
