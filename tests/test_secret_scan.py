"""scripts/secret-scan.py: counts of planted fake values per target, and never a value in any output.

Every value here is generated for the test. The scanner is never pointed at a real secret file in this repo's tests.
"""

import ast
import base64
import json
import os
import secrets
import subprocess
import sys

from relay_core.testing.mutation import mutation

from guards import REPO

SCAN = "scripts/secret-scan.py"


def fake(n: int = 20) -> str:
    return "FAKE" + secrets.token_hex(n // 2)


class Rig:
    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.cookie, self.cookie2 = fake(), fake(24)
        self.user, self.password, self.host = "u" + fake(10), "p" + fake(14), "h" + secrets.token_hex(6) + ".example"
        self.short = "sh0rt"
        self.secrets = self.write("secrets.jsonl", (
            '{"cookies": [{"name": "a", "value": "%s", "domain": ".example.test"}, {"name": "b", "value": "%s"}, '
            '{"name": "c", "value": "%s"}]}\n%s:8000:%s:%s\n' % (self.cookie, self.cookie2, self.short,
                                                                   self.host, self.user, self.password)))

    def write(self, name: str, text: str, mode: int = 0o600):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def scan(self, *args, stdin: str | None = None):
        return subprocess.run([sys.executable, str(REPO / SCAN), *map(str, args)], input=stdin, capture_output=True,
                              text=True, cwd=self.dir)

    def everything(self, run) -> str:
        return run.stdout + run.stderr


def numbers(run) -> dict[str, tuple[int, int]]:
    out = {}
    for line in run.stdout.splitlines():
        if ": values=" in line:
            label, rest = line.split(": values=")
            values, occ = rest.split(" occurrences=")
            out[label] = (int(values), int(occ.split()[0]))
    return out


def counts(run, label: str) -> dict[str, int]:
    """Every number printed for a label: values, occurrences, unreadable, bytes."""
    for line in run.stdout.splitlines():
        if line.startswith(label + ": "):
            return {k: int(v) for k, v in (part.split("=") for part in line[len(label) + 2:].split())}
    raise AssertionError("no line for %r in %r" % (label, run.stdout))


def test_a_planted_cookie_and_proxy_login_are_counted_per_target_and_the_status_says_found(tmp_path):
    r = Rig(tmp_path)
    log = r.write("api.log", "x %s y\n%s again\nuser=%s pw=%s\n" % (r.cookie, r.cookie, r.user, r.password))
    clean = r.write("clean.log", "nothing to see here\n")
    run = r.scan("scan", "--secrets", r.secrets, "--path", log, "--path", clean)
    assert numbers(run) == {str(log): (3, 4), str(clean): (0, 0)}, run.stdout
    assert run.returncode == 1
    assert r.scan("scan", "--secrets", r.secrets, "--path", clean).returncode == 0


@mutation(SCAN, "return 1 if found else (2 if unread else 0)", "return 0", expect="status says nothing was found")
def test_the_exit_status_is_one_when_a_value_was_found(tmp_path):
    r = Rig(tmp_path)
    run = r.scan("scan", "--secrets", r.secrets, "--stdin", "lbl", stdin="a %s b" % r.cookie)
    assert run.returncode == 1, "status says nothing was found: %r" % run.returncode


def test_the_output_never_contains_a_planted_value(tmp_path):
    r = Rig(tmp_path)
    log = r.write("l.log", "%s %s %s %s\n" % (r.cookie, r.cookie2, r.user, r.password))
    runs = [r.scan("scan", "--secrets", r.secrets, "--path", log, "--stdin", "s", stdin=r.cookie),
            r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h")]
    runs.append(r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--path", log))
    for run in runs:
        for value in (r.cookie, r.cookie2, r.user, r.password, r.host):
            assert value not in r.everything(run), "a planted value was printed"


@mutation(SCAN, 'raise Usage("usage error (see the header of scripts/secret-scan.py)")', "raise Usage(message)",
          expect="a planted value reached the output")
def test_no_option_takes_a_secret_and_a_usage_error_never_echoes_what_was_typed(tmp_path):
    r = Rig(tmp_path)
    for args in (("scan", "--value", r.cookie), ("scan", "--secret=" + r.cookie), ("scan", r.cookie),
                 ("hashes", "--secrets", r.secrets, "--keyy", r.cookie)):
        run = r.scan(*args)
        assert run.returncode == 2, args
        assert r.cookie not in r.everything(run), "a planted value reached the output: %r" % (args[0],)


@mutation(SCAN, "keep = {v.encode() for v in values if len(v) >= MIN_LEN}", "keep = {v.encode() for v in values}",
          expect="a short value was scanned")
def test_a_value_under_eight_characters_is_not_scanned_and_the_skip_is_counted(tmp_path):
    r = Rig(tmp_path)
    run = r.scan("scan", "--secrets", r.secrets, "--stdin", "lbl", stdin="here: %s\n" % r.short)
    assert numbers(run)["lbl"] == (0, 0), "a short value was scanned: %r" % run.stdout
    assert "skipped: 1 value(s) under 8 characters" in run.stdout, run.stdout


@mutation(SCAN, "if not stat.S_ISREG(mode) or mode & 0o077:", "if not stat.S_ISREG(mode):",
          expect="a group-readable secret file was accepted")
def test_a_secret_or_key_file_with_group_or_other_access_is_refused(tmp_path):
    r = Rig(tmp_path)
    open_secrets = r.write("open.jsonl", r.secrets.read_text(), mode=0o640)
    run = r.scan("scan", "--secrets", open_secrets, "--stdin", "lbl", stdin="x")
    assert run.returncode == 2, "a group-readable secret file was accepted"
    r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h")
    (r.dir / "k").chmod(0o644)
    run = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl", stdin="x")
    assert run.returncode == 2, "a group-readable key file was accepted"


def test_the_proxy_user_and_password_are_values_and_the_host_is_not(tmp_path):
    r = Rig(tmp_path)
    run = r.scan("scan", "--secrets", r.secrets, "--stdin", "lbl", stdin="connect to %s port 8000\n" % r.host)
    assert numbers(run)["lbl"] == (0, 0), "the host was scanned: %r" % run.stdout
    run = r.scan("scan", "--secrets", r.secrets, "--stdin", "lbl", stdin="%s %s" % (r.user, r.password))
    assert numbers(run)["lbl"] == (2, 2)


@mutation(SCAN, 'creds = line.split(":", 2)[2]                              # host:port:user:password',
          'creds = line                                                # host:port:user:password',
          expect="the host was scanned")
def test_a_proxy_line_gives_its_user_and_password_but_not_its_host(tmp_path):
    r = Rig(tmp_path)
    run = r.scan("scan", "--secrets", r.secrets, "--stdin", "lbl", stdin="only the host %s here\n" % r.host)
    assert numbers(run)["lbl"] == (0, 0), "the host was scanned: %r" % run.stdout


@mutation(SCAN, "if os.path.isdir(path):", "if False:", expect="a directory was not walked")
def test_a_directory_is_walked_and_counted_under_its_own_label(tmp_path):
    r = Rig(tmp_path)
    deploy = tmp_path / "deploy" / "sub"
    deploy.mkdir(parents=True)
    (deploy / "a.txt").write_text("one %s\n" % r.cookie)
    (tmp_path / "deploy" / "b.txt").write_text("two %s %s\n" % (r.cookie, r.cookie2))
    run = r.scan("scan", "--secrets", r.secrets, "--path", tmp_path / "deploy")
    assert numbers(run) == {str(tmp_path / "deploy"): (2, 3)}, "a directory was not walked: %r" % run.stdout
    assert "a.txt" not in run.stdout and "sub" not in run.stdout        # no file name inside a directory


@mutation(SCAN, "targets.append((args.stdin, sys.stdin.buffer.read()))", "targets.append((args.stdin, b''))",
          expect="the stream on stdin was not scanned")
def test_a_stream_on_stdin_is_scanned_under_its_label(tmp_path):
    r = Rig(tmp_path)
    run = r.scan("scan", "--secrets", r.secrets, "--stdin", "docker-logs", stdin="line %s end\n" % r.cookie2)
    assert numbers(run) == {"docker-logs": (1, 1)}, "the stream on stdin was not scanned: %r" % run.stdout


@mutation(SCAN, "if h in wanted:", "if False:", expect="a hashed value was not found")
def test_with_the_secret_files_deleted_the_keyed_hashes_still_find_every_value(tmp_path):
    r = Rig(tmp_path)
    made = r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h")
    assert made.returncode == 0 and "7 written, 1 skipped" in made.stdout, made.stdout
    r.secrets.unlink()
    text = "a %s b\nuser:%s:end\n{\"k\":\"%s\"} %s\n" % (r.cookie, r.user, r.cookie2, r.password)
    run = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl", stdin=text)
    assert numbers(run) == {"lbl": (4, 4)}, "a hashed value was not found: %r" % run.stdout
    clean = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl", stdin="nothing here\n")
    assert numbers(clean) == {"lbl": (0, 0)} and clean.returncode == 0


@mutation(SCAN, "os.fchmod(fd, 0o600)", "os.fchmod(fd, 0o644)", expect="the key or hash file is not 0600")
def test_the_key_and_the_hash_list_are_written_0600_and_an_existing_key_is_kept(tmp_path):
    r = Rig(tmp_path)
    r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h")
    for name in ("k", "h"):
        assert (r.dir / name).stat().st_mode & 0o777 == 0o600, "the key or hash file is not 0600"
    key = (r.dir / "k").read_text()
    r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h2")
    assert (r.dir / "k").read_text() == key


def test_a_hash_list_made_with_another_key_finds_nothing(tmp_path):
    r = Rig(tmp_path)
    r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h")
    other = r.write("k2", secrets.token_hex(32))
    run = r.scan("scan", "--hashes", r.dir / "h", "--key", other, "--stdin", "lbl", stdin=r.cookie)
    assert numbers(run) == {"lbl": (0, 0)}


def test_the_scanner_has_no_secret_in_its_own_command_line_and_needs_one_of_secrets_or_hashes(tmp_path):
    r = Rig(tmp_path)
    assert r.scan("scan", "--stdin", "lbl", stdin="x").returncode == 2
    assert r.scan("scan", "--secrets", r.secrets, "--hashes", r.dir / "h", "--stdin", "l", stdin="x").returncode == 2


def test_it_is_stdlib_only():
    text = (REPO / SCAN).read_text(encoding="utf-8")
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Import)}
    mods |= {n.module.split(".")[0] for n in ast.walk(ast.parse(text)) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)


# ---- a target that cannot be read is never "clean" -------------------------------------------------------
def not_root():
    return os.geteuid() != 0


@mutation(SCAN, "                except OSError:\n                    unreadable.append(1)",
          "                except OSError:\n                    pass",
          expect="an unreadable file was reported as clean")
def test_an_unreadable_file_is_counted_and_the_status_is_not_zero(tmp_path):
    if not not_root():
        return
    r = Rig(tmp_path)
    locked = r.write("locked.log", "has %s\n" % r.cookie, mode=0o000)
    run = r.scan("scan", "--secrets", r.secrets, "--path", locked)
    assert counts(run, str(locked))["unreadable"] == 1 and run.returncode == 2, \
        "an unreadable file was reported as clean"
    locked.chmod(0o600)


@mutation(SCAN, "onerror=lambda _e: unreadable.append(1)", "onerror=None",
          expect="an unreadable directory was reported as clean")
def test_an_unreadable_subdirectory_is_counted_and_a_link_that_is_not_followed_too(tmp_path):
    if not not_root():
        return
    r = Rig(tmp_path)
    top = tmp_path / "top"
    (top / "locked").mkdir(parents=True)
    (top / "locked" / "a.log").write_text(r.cookie)
    (top / "ok.log").write_text("fine\n")
    (top / "link.log").symlink_to(top / "ok.log")
    (top / "locked").chmod(0o000)
    try:
        run = r.scan("scan", "--secrets", r.secrets, "--path", top)
    finally:
        (top / "locked").chmod(0o700)
    assert counts(run, str(top))["unreadable"] == 2 and run.returncode == 2, \
        "an unreadable directory was reported as clean: %r" % run.stdout


def test_a_found_value_wins_over_an_unreadable_target_in_the_status_and_both_are_printed(tmp_path):
    if not not_root():
        return
    r = Rig(tmp_path)
    locked = r.write("locked.log", "x", mode=0o000)
    run = r.scan("scan", "--secrets", r.secrets, "--path", locked, "--stdin", "s", stdin=r.cookie)
    locked.chmod(0o600)
    assert run.returncode == 1 and counts(run, "s")["occurrences"] == 1 and counts(run, str(locked))["unreadable"] == 1


def test_the_bytes_scanned_are_printed_so_an_empty_stream_cannot_pass_for_clean(tmp_path):
    r = Rig(tmp_path)
    assert counts(r.scan("scan", "--secrets", r.secrets, "--stdin", "empty", stdin=""), "empty")["bytes"] == 0
    assert counts(r.scan("scan", "--secrets", r.secrets, "--stdin", "some", stdin="12345"), "some")["bytes"] == 5


# ---- proxy lines in the @ form ---------------------------------------------------------------------------
def at_rig(tmp_path, line_of):
    tmp_path.mkdir(parents=True, exist_ok=True)
    r = Rig(tmp_path)
    r.at_user, r.at_pass = "au" + fake(8), "ap" + fake(14)
    r.at_file = r.write("at.txt", line_of(r) + "\n")
    return r


@mutation(SCAN, 'creds = line.rsplit("@", 1)[0].split("://")[-1]', 'creds = line.split(":", 2)[2]',
          expect="the user and password of an @ proxy line were not scanned")
def test_the_user_and_password_of_an_at_form_proxy_line_are_values_in_both_modes(tmp_path):
    forms = (lambda r: "%s:%s@%s:8080" % (r.at_user, r.at_pass, r.host),
             lambda r: "http://%s:%s@%s:8080" % (r.at_user, r.at_pass, r.host))
    for form in forms:
        r = at_rig(tmp_path / ("f%d" % forms.index(form)), form)
        for planted in (r.at_user, r.at_pass):
            run = r.scan("scan", "--secrets", r.at_file, "--stdin", "lbl", stdin="log: %s\n" % planted)
            assert numbers(run) == {"lbl": (1, 1)}, "the user and password of an @ proxy line were not scanned"
        r.scan("hashes", "--secrets", r.at_file, "--key", r.dir / "k", "--out", r.dir / "h")
        r.at_file.unlink()
        for planted in (r.at_user, r.at_pass):
            run = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl",
                         stdin="log: %s\n" % planted)
            assert numbers(run) == {"lbl": (1, 1)}, "the user and password of an @ proxy line were not scanned (hashed)"
        host = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl", stdin=r.host)
        assert numbers(host) == {"lbl": (0, 0)}


@mutation(SCAN, "out.add(base64.b64encode(pair.encode()).decode())", "pass",
          expect="the Proxy-Authorization form was not scanned")
def test_the_base64_form_of_the_proxy_login_is_a_value_too(tmp_path):
    r = Rig(tmp_path)
    header = base64.b64encode(("%s:%s" % (r.user, r.password)).encode()).decode()
    run = r.scan("scan", "--secrets", r.secrets, "--stdin", "lbl", stdin="Proxy-Authorization: Basic %s\n" % header)
    assert numbers(run) == {"lbl": (1, 1)}, "the Proxy-Authorization form was not scanned"


# ---- hashed values with a separator ----------------------------------------------------------------------
@mutation(SCAN, "keep |= {piece for piece in TOKEN.findall(v) if len(piece) >= MIN_LEN}", "pass",
          expect="a value with a separator was not found by its hashes")
def test_a_value_that_holds_a_separator_is_found_by_its_pieces_after_the_files_are_gone(tmp_path):
    r = Rig(tmp_path)
    odd = "first%sword;second%spart,third%sbit" % (fake(6), fake(6), fake(6))
    secrets_file = r.write("odd.jsonl", '{"cookies": [{"name": "a", "value": %s}]}\n' % json.dumps(odd))
    made = r.scan("hashes", "--secrets", secrets_file, "--key", r.dir / "k", "--out", r.dir / "h")
    assert "1 values hold a separator" in made.stdout, made.stdout
    secrets_file.unlink()
    run = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl", stdin="log %s end\n" % odd)
    assert counts(run, "lbl")["occurrences"] >= 1 and run.returncode == 1, \
        "a value with a separator was not found by its hashes"
    assert odd not in made.stdout + made.stderr


def test_a_long_token_is_scanned_across_what_used_to_be_a_cut_point(tmp_path):
    r = Rig(tmp_path)
    blob = "x" * (65536 - 7) + r.cookie + "y" * 100            # the value straddles byte 65536 of one token
    r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h")
    run = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl", stdin=blob)
    assert numbers(run) == {"lbl": (1, 1)}, "a value across a cut point was missed"


# ---- the files it writes and the errors it prints --------------------------------------------------------
@mutation(SCAN, "    os.fchmod(fd, 0o600)", "    pass", expect="an existing hash list kept its old mode")
def test_a_hash_list_that_already_exists_is_made_0600_again(tmp_path):
    r = Rig(tmp_path)
    old = r.write("h", "old\n", mode=0o644)
    r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", old)
    assert old.stat().st_mode & 0o777 == 0o600, "an existing hash list kept its old mode"


def test_deeply_nested_json_in_a_secret_file_is_a_plain_line_and_not_a_crash(tmp_path):
    r = Rig(tmp_path)
    deep = r.write("deep.txt", "[" * 200000 + "\n")
    run = r.scan("hashes", "--secrets", deep, "--key", r.dir / "k", "--out", r.dir / "h")
    assert run.returncode == 0 and "Traceback" not in run.stderr, run.stderr[-200:]


@mutation(SCAN, 'except (OSError, ValueError):\n        print("secret-scan: an input could not be',
          'except (OSError, ValueError) as e:\n        print("secret-scan: %s" % e + "',
          expect="an error message repeated an input")
def test_an_error_while_reading_an_input_prints_a_fixed_message_and_never_the_input(tmp_path):
    r = Rig(tmp_path)
    r.scan("hashes", "--secrets", r.secrets, "--key", r.dir / "k", "--out", r.dir / "h")
    broken = r.write("broken", "%s somehash\n" % r.password)       # the length is not a number: int() names the text
    run = r.scan("scan", "--hashes", broken, "--key", r.dir / "k", "--stdin", "lbl", stdin="x")
    assert run.returncode == 2 and r.password not in r.everything(run), "an error message repeated an input"


# ---- the importer's shapes through the scanner (user and password planted alone, both line forms, both modes) ------
SHAPE_PASSWORDS = ["Zq9plainpass1", "Zq9has%41pct", "Zq9has#hash", "Zq9has/slash", "Zq9has?query", "Zq9has:colon",
                   "Zq9has@atsign", "Zq9haslongpiece space1234;,xyz9abcdef"]
SHAPE_USERS = ["usr1234567", "mail.user@example.com"]


@mutation(SCAN, 'user, _, password = pair.partition(":")', 'user, _, password = pair.rpartition(":")',
          expect="a user or a password with a colon or an @ was missed")
@mutation(SCAN, 'if "@" in line and AT_TAIL.fullmatch(line.rsplit("@", 1)[1]):', 'if "@" in line:',
          expect="a user or a password with a colon or an @ was missed")
def test_every_importer_shape_of_user_and_password_is_found_alone_in_both_line_forms_and_both_modes(tmp_path):
    forms = (lambda u, p, h: "%s:8000:%s:%s" % (h, u, p), lambda u, p, h: "%s:%s@%s:8000" % (u, p, h))
    n = 0
    for password in SHAPE_PASSWORDS:
        for user in SHAPE_USERS:
            for form in forms:
                n += 1
                d = tmp_path / ("c%d" % n)
                d.mkdir()
                r = Rig(d)
                f = r.write("line.txt", form(user, password, r.host) + "\n")
                r.scan("hashes", "--secrets", f, "--key", d / "k", "--out", d / "h")
                for planted in (user, password):
                    fixed = r.scan("scan", "--secrets", f, "--stdin", "lbl", stdin="log %s end\n" % planted)
                    assert fixed.returncode == 1, "a user or a password with a colon or an @ was missed: %r" % planted
                f.unlink()
                for planted in (user, password):
                    hashed = r.scan("scan", "--hashes", d / "h", "--key", d / "k", "--stdin", "lbl",
                                    stdin="log %s end\n" % planted)
                    assert hashed.returncode == 1, \
                        "a user or a password with a colon or an @ was missed (hashed): %r" % planted


@mutation(SCAN, "for pair in {creds, unquote(creds)}:", "for pair in {creds}:",
          expect="a URL-decoded proxy login was not scanned")
def test_the_url_decoded_user_password_and_basic_pair_of_a_quoted_proxy_line_are_values_too(tmp_path):
    r = Rig(tmp_path)
    f = r.write("line.txt", "alice1234:Zq9long%40pass%3Aword@" + r.host + ":8080\n")
    decoded = "Zq9long@pass:word"
    header = base64.b64encode(("alice1234:" + decoded).encode()).decode()
    for planted in (decoded, header):
        run = r.scan("scan", "--secrets", f, "--stdin", "lbl", stdin="seen %s\n" % planted)
        assert numbers(run) == {"lbl": (1, 1)}, "a URL-decoded proxy login was not scanned"
    r.scan("hashes", "--secrets", f, "--key", r.dir / "k", "--out", r.dir / "h")
    f.unlink()
    for planted in (decoded, header):
        run = r.scan("scan", "--hashes", r.dir / "h", "--key", r.dir / "k", "--stdin", "lbl",
                     stdin="seen %s\n" % planted)
        assert run.returncode == 1, "a URL-decoded proxy login was not scanned (hashed)"


@mutation(SCAN, 'with open(private(path), encoding="utf-8") as f:',
          'with open(private(path), encoding="utf-8", errors="replace") as f:',
          expect="a secret file that is not UTF-8 was read as clean")
def test_a_secret_file_that_is_not_valid_utf8_is_refused_never_read_as_clean(tmp_path):
    r = Rig(tmp_path)
    bad = r.write("bad.jsonl", "")
    bad.write_bytes(b"Zq9caf\xff\xfe" + fake().encode() + b"\n")
    bad.chmod(0o600)
    for args in (("scan", "--secrets", bad, "--stdin", "lbl"),
                 ("hashes", "--secrets", bad, "--key", r.dir / "k", "--out", r.dir / "h")):
        run = r.scan(*args, stdin="x")
        assert run.returncode == 2 and "secret input unreadable" in run.stderr, \
            "a secret file that is not UTF-8 was read as clean"


@mutation(SCAN, "unreadable.extend(1 for n in dirs if os.path.islink(os.path.join(d, n)))", "pass",
          expect="a linked directory was reported as scanned")
def test_a_symlink_to_a_directory_inside_a_scanned_directory_is_counted_not_followed(tmp_path):
    r = Rig(tmp_path)
    top = tmp_path / "top"
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "a.log").write_text(r.cookie)
    top.mkdir()
    (top / "ok.log").write_text("fine\n")
    (top / "linkdir").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    run = r.scan("scan", "--secrets", r.secrets, "--path", top)
    assert counts(run, str(top))["unreadable"] == 1 and run.returncode == 2, \
        "a linked directory was reported as scanned: %r" % run.stdout


@mutation(SCAN, "    if os.path.islink(path):\n        unreadable.append(1)",
          "    if False:\n        unreadable.append(1)", expect="a path that is a link was followed")
def test_a_path_that_is_itself_a_symlink_is_counted_not_followed_for_a_file_and_for_a_directory(tmp_path):
    r = Rig(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.log").write_text(r.cookie)
    (tmp_path / "file.log").write_text(r.cookie)
    (tmp_path / "linkfile").symlink_to(tmp_path / "file.log")
    (tmp_path / "linkdir").symlink_to(real, target_is_directory=True)
    for link in ("linkfile", "linkdir"):
        run = r.scan("scan", "--secrets", r.secrets, "--path", tmp_path / link)
        assert counts(run, str(tmp_path / link)) == {"values": 0, "occurrences": 0, "unreadable": 1, "bytes": 0} \
            and run.returncode == 2, "a path that is a link was followed: %s %r" % (link, run.stdout)
