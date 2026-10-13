"""scripts/import-accounts.py against a stand-in admin server on loopback. All values are generated for the test.

The real end-to-end import (sealing, parsing, the account rows) is in
packages/relay-core/tests/api/test_import_script.py; here the server only records what the script sends and fails
on demand.
"""

import importlib.util
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from relay_core.testing.mutation import mutation

from guards import REPO

SCRIPT = "scripts/import-accounts.py"
spec = importlib.util.spec_from_file_location("import_accounts", REPO / SCRIPT)
imp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(imp)


class Server:
    """Records every request. `import_mode`:
    ok | lost (no answer, nothing applied) | applied_then_lost (no answer) | applied_500 | applied_garbage (a bad status
    line) | applied_cut (an answer cut short) | applied_then_down (applied, no answer, and the list is gone too) |
    redirect | short (one line missing) | echo (a 500 that repeats the body, nothing applied)."""

    def __init__(self, login_status=200, rejected=(), import_mode="ok"):
        self.calls, self.login_status, self.rejected = [], login_status, list(rejected)
        self.import_mode, self.base_count, self.accounts = import_mode, 5, []
        self.down, self.get_status, self.login_raw, self.break_list_after_import = False, 200, None, False
        outer = self

        class H(BaseHTTPRequestHandler):
            def reply(self, status, out, headers=()):
                data = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(status)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                outer.calls.append((self.path, self.headers.get("X-Admin-Session"), None))
                if outer.down:
                    self.connection.close()
                elif outer.get_status != 200 or (outer.break_list_after_import and any(
                        c[0] == "/admin/accounts/import" for c in outer.calls)):
                    self.reply(outer.get_status, {"total": 7})            # a number that must not be believed
                else:
                    self.reply(200, {"items": list(reversed(outer.accounts)),
                                     "total": outer.base_count + len(outer.accounts)})

            def apply(self, body):
                now = time.time() * 1000
                n = len(body["items"]) - len(outer.rejected)
                outer.accounts += [{"label": i["label"], "created_ms": now} for i in body["items"][:n]]
                return n

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["content-length"]))
                body = json.loads(raw)
                outer.calls.append((self.path, self.headers.get("X-Admin-Session"), body))
                mode = outer.import_mode
                if self.path == "/admin/login":
                    if outer.login_raw is not None:
                        self.reply(200, outer.login_raw)
                    else:
                        self.reply(outer.login_status, {"token": outer.token} if outer.login_status == 200 else {})
                elif self.path == "/admin/logout":
                    self.reply(200, {"ok": True})
                elif mode == "redirect":
                    self.reply(302, {}, [("Location", "/elsewhere")])
                elif mode == "short":
                    self.reply(200, {"imported": [{"index": 0}, {"index": 1}], "rejected": []})
                elif mode == "echo":
                    self.reply(500, {"detail": raw.decode()})
                elif mode == "lost":
                    self.connection.close()
                elif mode in ("applied_then_lost", "applied_then_down"):
                    self.apply(body)
                    outer.down = mode == "applied_then_down"
                    self.connection.close()
                elif mode == "applied_500":
                    self.apply(body)
                    self.reply(500, {"detail": "boom"})
                elif mode == "applied_garbage":
                    self.apply(body)
                    self.wfile.write(b"garbage\r\n\r\n")
                    self.connection.close()
                elif mode == "applied_cut":
                    self.apply(body)
                    self.wfile.write(b"HTTP/1.0 200 OK\r\ncontent-length: 500\r\n\r\n{\"imported\"")
                    self.connection.close()
                else:
                    n = self.apply(body)
                    self.reply(200, {"imported": [{"index": i} for i in range(n)], "rejected": outer.rejected})

            def log_message(self, *a):
                pass

        self.token = "TOK" + secrets.token_hex(12)
        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()

    def paths(self):
        return [c[0].split("?")[0] for c in self.calls]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    class Rig:
        pass

    r = Rig()
    r.cookies = ["c" + secrets.token_hex(12) for _ in range(3)]
    r.users = ["u" + secrets.token_hex(5) for _ in range(3)]
    r.passwords = ["p" + secrets.token_hex(8) for _ in range(3)]
    r.hosts = ["10.1.2.%d" % i for i in range(3)]
    r.admin_password = "A" + secrets.token_hex(10)
    r.cookie_file = tmp_path / "jars.jsonl"
    r.proxy_file = tmp_path / "proxies.txt"
    r.cookie_file.write_text("".join(json.dumps({"cookies": [{"name": "sid", "value": v}]}) + "\n" for v in r.cookies))
    r.proxy_file.write_text("".join("%s:%d:%s:%s\n" % (h, 8000 + i, u, p)
                                    for i, (h, u, p) in enumerate(zip(r.hosts, r.users, r.passwords, strict=True))))
    for f in (r.cookie_file, r.proxy_file):
        f.chmod(0o600)
    monkeypatch.setattr(imp, "ask_password", lambda: r.admin_password)
    r.server = Server()
    yield r
    r.server.close()


def run(r, capsys, *extra, base=None):
    code = imp.main(["--cookies", str(r.cookie_file), "--proxies", str(r.proxy_file), "--base", base or r.server.url,
                     "--label-prefix", "n11", *extra])
    out = capsys.readouterr()
    return code, out.out + out.err


def test_the_jars_pair_with_their_proxies_in_one_post_and_the_output_is_counts_only(rig, capsys):
    code, shown = run(rig, capsys)
    assert code == 0
    assert rig.server.paths() == ["/admin/login", "/admin/accounts", "/admin/accounts", "/admin/accounts/import",
                                  "/admin/accounts", "/admin/logout"]
    login, upload = rig.server.calls[0], rig.server.calls[3]
    assert login[2]["password"] == rig.admin_password and upload[1] == rig.server.token
    body = upload[2]
    assert body["provider"] == "dola" and [i["label"] for i in body["items"]] == ["n11-00", "n11-01", "n11-02"]
    for i, item in enumerate(body["items"]):
        assert item["proxy"] == "%s:%s@%s:%d" % (rig.users[i], rig.passwords[i], rig.hosts[i], 8000 + i)
        assert json.loads(item["credential"]) == {"cookies": [{"name": "sid", "value": rig.cookies[i]}]}
    assert "imported: 3, rejected: 0" in shown and "accounts before: 5" in shown and "accounts after: 8" in shown
    assert rig.server.calls[-1][0] == "/admin/logout" and rig.server.calls[-1][1] == rig.server.token
    for secret in (*rig.cookies, *rig.users, *rig.passwords, *rig.hosts, rig.admin_password, rig.server.token):
        assert secret not in shown, "a secret was printed"


@mutation(SCRIPT, 'return "%s:%s@%s:%s" % (quote(user, safe=""), quote(password, safe=""), host, port)',
          'return "%s:%s@%s:%s" % (host, port, user, password)',
          expect="the proxy was not user:password@host:port")
def test_a_host_port_user_password_line_becomes_user_password_at_host_port(rig, capsys):
    run(rig, capsys)
    items = rig.server.calls[3][2]["items"]
    assert items[0]["proxy"] == "%s:%s@%s:8000" % (rig.users[0], rig.passwords[0], rig.hosts[0]), \
        "the proxy was not user:password@host:port"


@mutation(SCRIPT, "if host not in LOOPBACK:", "if False:", expect="a non-loopback server was reached")
def test_a_server_that_is_not_on_loopback_is_refused_before_anything_is_asked_or_sent(rig, capsys, monkeypatch):
    asked = []
    monkeypatch.setattr(imp, "ask_password", lambda: asked.append(1) or "x")
    code, shown = run(rig, capsys, base="http://192.0.2.1:8100")
    assert code == 2 and not asked and not rig.server.calls, "a non-loopback server was reached"
    assert imp.main(["--cookies", "a", "--proxies", "b", "--base", "http://localhost.evil.example"]) == 2


@mutation(SCRIPT, "if len(cookie_lines) != len(proxy_lines):", "if False:", expect="is shorter than argument")
def test_files_of_different_lengths_are_refused(rig, capsys):
    rig.proxy_file.write_text(rig.proxy_file.read_text().rsplit("\n", 2)[0] + "\n")
    code, shown = run(rig, capsys)
    assert code == 2 and not rig.server.calls, "files of different lengths were paired"
    assert "2 proxy lines" in shown


@mutation(SCRIPT, "if not stat.S_ISREG(mode) or mode & 0o077:", "if not stat.S_ISREG(mode):",
          expect="a group-readable input file was read")
def test_an_input_file_with_group_or_other_access_is_refused(rig, capsys):
    rig.cookie_file.chmod(0o644)
    code, _ = run(rig, capsys)
    assert code == 2 and not rig.server.calls, "a group-readable input file was read"


def test_the_password_is_asked_from_the_terminal_and_no_option_takes_a_secret(rig, capsys):
    for flag in ("--password", "--token"):
        code = imp.main(["--cookies", str(rig.cookie_file), "--proxies", str(rig.proxy_file), flag, rig.admin_password])
        shown = capsys.readouterr()
        assert code == 2 and rig.admin_password not in shown.out + shown.err
    assert not rig.server.calls


def test_a_refused_login_stops_before_the_import(rig, capsys):
    rig.server.login_status = 401
    code, shown = run(rig, capsys)
    assert code == 2 and "login refused (status 401)" in shown
    assert rig.server.paths() == ["/admin/login"]
    assert rig.admin_password not in shown


def test_rejections_are_counted_by_their_fixed_reason_and_set_the_exit_status(rig, capsys):
    rig.server.rejected = [{"index": 1, "reason": "credential_unparseable"}, {"index": 2, "reason": "proxy_invalid"}]
    code, shown = run(rig, capsys)
    assert code == 1 and "imported: 1, rejected: 2" in shown
    assert "rejected credential_unparseable: 1" in shown and "rejected proxy_invalid: 1" in shown


def test_a_dry_run_parses_and_counts_without_any_network_call_or_password(rig, capsys, monkeypatch):
    monkeypatch.setattr(imp, "ask_password", lambda: pytest.fail("the password was asked in a dry run"))
    code, shown = run(rig, capsys, "--dry-run")
    assert code == 0 and "3 cookie jars paired with 3 proxies" in shown and not rig.server.calls


def test_a_malformed_cookie_line_is_refused_without_quoting_it(rig, capsys):
    rig.cookie_file.write_text('{"nope": 1}\n' * 3)
    code, shown = run(rig, capsys)
    assert code == 2 and "cookie line 1" in shown and "nope" not in shown


# ---- the connection: no proxy, no redirect ------------------------------------------------------------------------
@mutation(SCRIPT, "urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)",
          "urllib.request.build_opener(NoRedirect)", expect="the password went through the proxy of the environment")
def test_an_http_proxy_in_the_environment_never_sees_the_password_or_the_token(rig, capsys, monkeypatch):
    spy = Server()
    try:
        for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
            monkeypatch.setenv(name, spy.url)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        fresh_spec = importlib.util.spec_from_file_location("import_accounts_env", REPO / SCRIPT)
        fresh = importlib.util.module_from_spec(fresh_spec)
        fresh_spec.loader.exec_module(fresh)              # the opener reads the environment when it is built
        monkeypatch.setattr(fresh, "ask_password", lambda: rig.admin_password)
        code = fresh.main(["--cookies", str(rig.cookie_file), "--proxies", str(rig.proxy_file), "--base",
                           rig.server.url, "--label-prefix", "n11"])
        capsys.readouterr()
        assert spy.calls == [], "the password went through the proxy of the environment"
        assert code == 0 and rig.server.paths()[0] == "/admin/login"
    finally:
        spy.close()


@mutation(SCRIPT, "        return None                                   # the session header must never follow",
          "        return super().redirect_request(*args, **kwargs)", expect="a redirect was followed")
def test_a_redirect_is_refused_and_the_session_header_is_not_forwarded(rig, capsys):
    rig.server.import_mode = "redirect"
    target = Server()
    try:
        code, shown = run(rig, capsys)
        assert code == 2 and "the import was refused (status 302)" in shown, "a redirect was followed"
        assert target.calls == []
    finally:
        target.close()


# ---- the proxy line -------------------------------------------------------------------------------------------------
USERS = ["user1", "us@er", "us#er"]
PASSWORDS = ["plain1234", "has%41pct", "has#hash", "has/slash", "has?query", "has:colon", "has@at", "has space;,x"]


@mutation(SCRIPT, 'quote(password, safe="")', "password", expect="a proxy line was rewritten wrongly")
@mutation(SCRIPT, 'quote(user, safe="")', "user", expect="a proxy line was rewritten wrongly")
@mutation(SCRIPT, 'if "@" in line and AT_FORM_TAIL', 'if "@" in line or AT_FORM_TAIL',
          expect="a proxy line was rewritten wrongly")
def test_a_proxy_user_and_password_round_trip_through_the_servers_parser():
    from relay_core.admin.routes_common import parse_proxy
    for password in PASSWORDS:
        for user in USERS:
            try:
                host, port, auth = parse_proxy(imp.proxy_of("203.0.113.9:8080:%s:%s" % (user, password)))
            except Exception:         # any failure to rewrite or parse is the same finding
                pytest.fail("a proxy line was rewritten wrongly: %r" % password)
            assert (host, port, auth) == ("203.0.113.9", 8080, "%s:%s" % (user, password)), \
                "a proxy line was rewritten wrongly: %r" % password
    assert parse_proxy(imp.proxy_of("bob:s3cret@203.0.113.9:8080")) == ("203.0.113.9", 8080, "bob:s3cret")


# ---- an answer that does not come back ------------------------------------------------------------------------------
@mutation(SCRIPT, "            if found == 0:", "            if False:", expect="a lost answer was reported as applied")
def test_a_lost_answer_is_decided_by_the_posted_labels_and_says_whether_running_again_is_safe(rig, capsys):
    rig.server.import_mode = "lost"
    code, shown = run(rig, capsys)
    assert code == 2 and "NOT applied" in shown and "running it again is safe" in shown, \
        "a lost answer was reported as applied: " + shown
    rig.server.import_mode = "applied_then_lost"
    rig.server.calls.clear()
    code, shown = run(rig, capsys)
    assert code == 4 and "3 accounts with the posted labels" in shown and "do not run it again" in shown, shown
    assert rig.server.calls[-1][0] == "/admin/logout"


@mutation(SCRIPT, 'r.get("label") in labels', "True", expect="another operator's accounts decided the outcome")
def test_an_account_added_by_someone_else_in_the_meantime_does_not_make_a_lost_import_look_applied(rig, capsys):
    rig.server.import_mode = "lost"
    rig.server.accounts += [{"label": "someone-else", "created_ms": time.time() * 1000 + 5}] * 3
    code, shown = run(rig, capsys)
    assert code == 2 and "NOT applied" in shown, "another operator's accounts decided the outcome: " + shown


@mutation(SCRIPT, "        if taken:", "        if False:", expect="a label that already exists was posted again")
def test_a_label_that_already_exists_stops_the_run_before_anything_is_posted(rig, capsys):
    rig.server.accounts += [{"label": "n11-01", "created_ms": time.time() * 1000 - 1000}]     # one second before
    code, shown = run(rig, capsys)
    assert code == 2 and "already exist" in shown and "nothing was sent" in shown, \
        "a label that already exists was posted again: " + shown
    assert "/admin/accounts/import" not in rig.server.paths() and rig.server.calls[-1][0] == "/admin/logout"
    assert "n11-01" not in shown


@mutation(SCRIPT, "        if have is None:", "        if False:",
          expect="the run went on without reading the existing accounts")
def test_a_list_of_existing_accounts_that_cannot_be_read_stops_the_run_before_anything_is_posted(rig, capsys):
    rig.server.get_status = 500
    try:
        code, shown = run(rig, capsys)
    except Exception:             # a traceback is the finding
        pytest.fail("the run went on without reading the existing accounts")
    assert code == 2 and "cannot be checked" in shown and "/admin/accounts/import" not in rig.server.paths(), \
        "the run went on without reading the existing accounts: " + shown


@mutation(SCRIPT, "            if status >= 500:\n                raise Unsure",
          "            if False:\n                raise Unsure", expect="a 5xx was taken for a refusal")
@mutation(SCRIPT, "ValueError, http.client.HTTPException):", "ValueError):", expect="a broken answer was not caught")
def test_a_5xx_a_garbled_answer_and_a_cut_answer_are_ambiguous_and_decided_by_the_labels(rig, capsys):
    for mode in ("applied_500", "applied_garbage", "applied_cut"):
        rig.server.import_mode = mode
        rig.server.accounts.clear()
        try:
            code, shown = run(rig, capsys)
        except Exception:         # a traceback is the finding
            pytest.fail("a broken answer was not caught: " + mode)
        assert code == 4 and "WAS applied" in shown and "Traceback" not in shown, \
            "a 5xx was taken for a refusal (%s): %r" % (mode, shown)
    rig.server.import_mode = "echo"                       # a 500 that applied nothing
    rig.server.accounts.clear()
    code, shown = run(rig, capsys)
    assert code == 2 and "NOT applied" in shown
    for secret in (*rig.cookies, *rig.users, *rig.passwords, rig.admin_password, rig.server.token):
        assert secret not in shown, "an error message printed a secret"


@mutation(SCRIPT, "            except Refused:\n                found = None",
          "            except Refused:\n                raise",
          expect="a lost answer with the server down said it could not be reached")
def test_a_lost_answer_with_the_server_down_afterwards_is_unknown_with_its_own_exit_code(rig, capsys):
    rig.server.import_mode = "applied_then_down"
    code, shown = run(rig, capsys)
    assert code == 3 and "outcome unknown" in shown and "n11" in shown, \
        "a lost answer with the server down said it could not be reached: " + shown


def test_a_lost_answer_whose_list_cannot_be_read_says_the_outcome_is_unknown(rig, capsys):
    rig.server.import_mode = "lost"
    rig.server.break_list_after_import = True                  # readable before the import, gone after it
    code, shown = run(rig, capsys)
    assert code == 3 and "outcome unknown" in shown


@mutation(SCRIPT, "answer if isinstance(answer, dict) else {}", "answer", expect="a non-object answer was used")
def test_an_answer_that_is_not_a_json_object_is_refused_with_a_fixed_message(rig, capsys):
    rig.server.login_raw = b"[1, 2]"
    try:
        code, shown = run(rig, capsys)
    except Exception:             # a traceback is the finding
        pytest.fail("a non-object answer was used")
    assert code == 2 and "login refused" in shown and "Traceback" not in shown, "a non-object answer was used"
    rig.server.login_raw = b"not json at all"
    code, shown = run(rig, capsys)
    assert code == 2 and "could not be reached" in shown and "not json" not in shown



@mutation(SCRIPT, 'or time.strftime("imp-', 'or time.strftime("x-', expect="the default label prefix changed")
def test_without_a_label_prefix_every_account_still_gets_a_distinct_label_to_find_it_by(rig, capsys):
    code = imp.main(["--cookies", str(rig.cookie_file), "--proxies", str(rig.proxy_file), "--base", rig.server.url])
    capsys.readouterr()
    labels = [i["label"] for i in rig.server.calls[3][2]["items"]]
    assert code == 0 and len(set(labels)) == 3 and all(label.startswith("imp-") for label in labels), \
        "the default label prefix changed"


# ---- what is printed ------------------------------------------------------------------------------------------
@mutation(SCRIPT, 'r.get("reason") in REASONS else "other"', 'r.get("reason") else "other"',
          expect="an unknown reason was printed")
def test_only_known_rejection_reasons_are_printed_and_the_rest_is_other(rig, capsys):
    leak = "pw-" + secrets.token_hex(8)
    rig.server.rejected = [{"index": 1, "reason": leak}, {"index": 2, "reason": "proxy_invalid"}]
    code, shown = run(rig, capsys)
    assert code == 1 and leak not in shown, "an unknown reason was printed"
    assert "rejected other: 1" in shown and "rejected proxy_invalid: 1" in shown


@mutation(SCRIPT, "if len(done) + len(refused) != items_count:", "if False:", expect="a short answer exited 0")
def test_an_answer_that_does_not_account_for_every_line_is_not_a_success(rig, capsys):
    rig.server.import_mode = "short"
    code, shown = run(rig, capsys)
    assert code == 2 and "does not account for every line (2 of 3)" in shown, "a short answer exited 0"


def test_no_terminal_means_no_password_prompt(monkeypatch, capsys):
    monkeypatch.setattr(imp.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(imp.getpass, "getpass", lambda prompt="": pytest.fail("a password was read without a terminal"))
    with pytest.raises(imp.Refused):
        imp.ask_password()


@mutation(SCRIPT, 'call(args.base, "/admin/logout", {}, token)', "pass", expect="the session was not logged out")
def test_the_token_and_the_items_are_dropped_and_the_session_is_logged_out_even_when_the_import_fails(rig, capsys):
    rig.server.import_mode = "redirect"
    run(rig, capsys)
    assert rig.server.paths()[-1] == "/admin/logout" and rig.server.calls[-1][1] == rig.server.token, \
        "the session was not logged out"


def test_an_input_that_is_not_utf8_text_is_refused_with_a_fixed_message(rig, capsys):
    rig.cookie_file.write_bytes(b"\xff\xfe\x00bad\n" * 3)
    rig.cookie_file.chmod(0o600)
    code, shown = run(rig, capsys)
    assert code == 2 and "cannot be read as UTF-8 text" in shown and not rig.server.calls


@mutation(SCRIPT, "if not 0 < len(cookie_lines) <= MAX_ITEMS:", "if False:",
          expect="more lines than the server takes were sent")
def test_more_lines_than_one_import_takes_are_refused_before_any_request(rig, capsys):
    rig.cookie_file.write_text('{"cookies": []}\n' * 201)
    rig.proxy_file.write_text("10.0.0.1:80:u:p\n" * 201)
    code, shown = run(rig, capsys)
    assert code == 2 and not rig.server.calls and "between 1 and 200" in shown, \
        "more lines than the server takes were sent"
