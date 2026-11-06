"""scripts/backup-db.sh with HOME a directory that plays the box user's home, and fake docker and curl on PATH that
record their calls and environments; openssl is the real one behind a wrapper that records its environment. flock,
sha256sum and date are real (the tests run on the box)."""

import fcntl
import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

from relay_core.testing.mutation import mutation

from guards import REPO

BACKUP = "scripts/backup-db.sh"
TOKEN = "123456789:TOKEN-VALUE-0042-xyz"
DUMP = b"PGDMP" + b"x" * 70000           # above the 64 KB a dump must hold

DOCKER = """\
#!/usr/bin/env bash
echo "DOCKER: $*" >> "@LOG@"
env >> "@DIR@/env.log"
body() { head -c "$1" /dev/zero | tr '\\0' x; }
case "${STUB_DUMP:-ok}" in
  ok) printf PGDMP; body 70000 ;;
  fail) exit 1 ;;
  garbage) body 70000 ;;                     # exit 0, no custom-format dump
  small) printf PGDMP; body 1000 ;;
  big) printf PGDMP; body 52000000 ;;        # encrypted: above 49 MB
esac
"""
OPENSSL = """\
#!/usr/bin/env bash
env >> "@DIR@/env.log"
[ -z "${STUB_OPENSSL_FAIL:-}" ] || exit 1
exec "@OPENSSL@" "$@"
"""
CURL = """\
#!/usr/bin/env bash
echo "CURL: $*" >> "@LOG@"
env >> "@DIR@/env.log"
n=$(ls "@DIR@"/curl-stdin-* 2>/dev/null | wc -l)
cat > "@DIR@/curl-stdin-$n"                       # the -K - config
echo "$*" > "@DIR@/curl-args-$n"
case "${STUB_CURL:-ok}" in
  ok) echo '{"ok":true,"result":{}}' ;;
  refuse) echo '{"ok":false,"error_code":400,"description":"Bad Request"}' ;;
  down) url=$(sed -n 's/^url = "\\(.*\\)"$/\\1/p' "@DIR@/curl-stdin-$n")
        echo "curl: (7) Failed to connect to $url" >&2; exit 7 ;;
esac
"""


class Box:
    def __init__(self, tmp: Path):
        self.dir, self.home, self.log = tmp, tmp / "home", tmp / "calls.log"
        self.out = self.home / "relay-backups" / "daily"
        cfg = self.home / "relay-backup-cfg"
        cfg.mkdir(parents=True, mode=0o700)
        (cfg / "telegram.env").write_text("TELEGRAM_BOT_TOKEN=%s\nTELEGRAM_CHAT_ID=-1001234\n" % TOKEN)
        (cfg / "backup.key").write_text("a passphrase for the test\n")
        for f in cfg.iterdir():
            f.chmod(0o600)
        self.key = cfg / "backup.key"
        (self.home / "relay-deploy" / "deploy").mkdir(parents=True)
        (self.home / "relay-deploy" / "deploy" / ".env").write_text("POSTGRES_PASSWORD=x\nGIT_SHA=abc1234\n")
        self.bin = tmp / "bin"
        self.bin.mkdir()
        real = shutil.which("openssl")
        for name, text in (("docker", DOCKER), ("curl", CURL), ("openssl", OPENSSL)):
            p = self.bin / name
            p.write_text(text.replace("@LOG@", str(self.log)).replace("@DIR@", str(tmp)).replace("@OPENSSL@", real))
            p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def run(self, **env: str) -> subprocess.CompletedProcess:
        env = {"HOME": str(self.home), "PATH": "%s:%s" % (self.bin, os.environ["PATH"]), **env}
        return subprocess.run(["bash", str(REPO / BACKUP)], env=env, capture_output=True, text=True, timeout=60)

    def backups(self) -> list[Path]:
        return sorted(self.out.glob("relay-*.dump.enc")) if self.out.exists() else []

    def calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def envs(self) -> str:
        return (self.dir / "env.log").read_text() if (self.dir / "env.log").exists() else ""

    def curl_stdin(self) -> list[str]:
        return [p.read_text() for p in sorted(self.dir.glob("curl-stdin-*"))]

    def decrypt(self, f: Path) -> bytes:
        return subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
                               "-pass", "file:%s" % self.key, "-in", str(f)], capture_output=True, check=True).stdout


@mutation(BACKUP, '-K - "$@" 2>&1)', '-K - "$@" "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/x" 2>&1)',
          expect="the token reached a command line")
@mutation(BACKUP, "umask 077\n", "umask 022\n", expect="the backup is not 0600")
@mutation(BACKUP, '-salt -pass "file:$KEY"', '-salt -pass pass:other', expect="the backup does not decrypt")
def test_a_backup_is_dumped_encrypted_kept_0600_and_sent_with_the_token_only_on_stdin(tmp_path):
    box = Box(tmp_path)
    run = box.run()
    assert run.returncode == 0, run.stderr
    [f] = box.backups()
    assert f.name.startswith("relay-") and f.name.endswith("-abc1234.dump.enc")
    assert stat.S_IMODE(f.stat().st_mode) == 0o600, "the backup is not 0600"
    try:
        plain = box.decrypt(f)
    except subprocess.CalledProcessError:
        plain = None
    assert plain == DUMP, "the backup does not decrypt"
    assert "exec -T postgres" in box.calls() and "pg_dump" in box.calls() and "-Fc" in box.calls()
    assert TOKEN not in box.calls(), "the token reached a command line"
    assert TOKEN not in run.stdout + run.stderr
    [config] = box.curl_stdin()
    assert "bot%s/sendDocument" % TOKEN in config and "chat_id=-1001234" in config
    args = (tmp_path / "curl-args-0").read_text()
    assert "document=@%s" % f in args and "GMT+7" in args and "sha abc1234" in args
    assert "sha256 %s" % hashlib.sha256(f.read_bytes()).hexdigest() in args
    assert not list(box.out.glob(".tmp-*")), "the plain dump was left behind"


@mutation(BACKUP, "[ ${#old[@]} -le $KEEP ] ||", "true ||", expect="more than 14 backups were kept")
def test_the_newest_14_backups_are_kept(tmp_path):
    box = Box(tmp_path)
    box.out.mkdir(parents=True)
    old = [box.out / ("relay-2020010%dT0000%02dZ-old.dump.enc" % (d, i)) for d in (1, 2) for i in range(8)]
    for p in old:
        p.write_bytes(b"x")
    run = box.run()
    assert run.returncode == 0, run.stderr
    kept = box.backups()
    assert len(kept) <= 14, "more than 14 backups were kept"
    assert kept == sorted(old)[3:] + [kept[-1]] and kept[-1].name.endswith("-abc1234.dump.enc")


@mutation(BACKUP, '&& [[ "$resp" == *\'"ok":true\'* ]] &&', "&& true &&", expect="a refused send passed")
def test_a_refused_send_fails_keeps_the_file_and_says_so_in_the_chat(tmp_path):
    box = Box(tmp_path)
    run = box.run(STUB_CURL="refuse")
    assert run.returncode == 1, "a refused send passed"
    assert len(box.backups()) == 1
    assert "Bad Request" in run.stderr and "kept at" in run.stderr
    assert any("sendMessage" in c for c in box.curl_stdin())


@mutation(BACKUP, 'echo "backup: $method: $(redact "$resp")"', 'echo "backup: $method: $resp"',
          expect="the token reached the output")
def test_a_send_that_cannot_connect_fails_without_printing_the_token(tmp_path):
    box = Box(tmp_path)
    run = box.run(STUB_CURL="down")
    assert run.returncode == 1 and len(box.backups()) == 1
    assert "Failed to connect" in run.stderr
    assert TOKEN not in run.stdout + run.stderr, "the token reached the output"


@mutation(BACKUP, '> "$tmp/relay.dump") || fail "pg_dump failed"', '> "$tmp/relay.dump") || true',
          expect="a failed pg_dump was not named")
def test_a_failed_dump_writes_no_backup_and_says_so(tmp_path):
    box = Box(tmp_path)
    run = box.run(STUB_DUMP="fail")
    assert run.returncode == 1 and box.backups() == []
    assert "pg_dump failed" in run.stderr, "a failed pg_dump was not named"
    [config] = box.curl_stdin()
    assert "sendMessage" in config


@mutation(BACKUP, '[ "$(head -c 5 "$tmp/relay.dump")" = PGDMP ] ||', "true ||", expect="a dump that is no dump passed")
def test_a_dump_without_the_custom_format_header_fails(tmp_path):
    box = Box(tmp_path)
    run = box.run(STUB_DUMP="garbage")
    assert run.returncode == 1 and "no custom-format dump" in run.stderr, "a dump that is no dump passed"
    assert box.backups() == []


@mutation(BACKUP, '-ge $MIN_DUMP ] ||', "-ge 0 ] ||", expect="a dump under 64 KB passed")
def test_a_dump_under_64_kb_fails(tmp_path):
    box = Box(tmp_path)
    run = box.run(STUB_DUMP="small")
    assert run.returncode == 1 and "under 64 KB" in run.stderr, "a dump under 64 KB passed"
    assert box.backups() == []


@mutation(BACKUP, '[ "$size" -le $MAX_SEND ] ||', "true ||", expect="a file above 49 MB was sent")
def test_a_backup_above_49_mb_is_kept_but_not_sent(tmp_path):
    box = Box(tmp_path)
    run = box.run(STUB_DUMP="big")
    configs = box.curl_stdin()
    assert not any("sendDocument" in c for c in configs), "a file above 49 MB was sent"
    assert run.returncode == 1 and len(box.backups()) == 1
    assert "above the 49 MB" in run.stderr
    assert len(configs) == 1 and "sendMessage" in configs[0]


@mutation(BACKUP, """trap 'rm -rf "$tmp"' EXIT""", "true", expect="the plain dump was left behind")
def test_a_failed_encryption_leaves_no_file_and_no_plain_dump(tmp_path):
    box = Box(tmp_path)
    run = box.run(STUB_OPENSSL_FAIL="1")
    assert run.returncode == 1 and "encryption failed" in run.stderr and box.backups() == []
    assert not list(box.out.glob(".tmp-*")), "the plain dump was left behind"


@mutation(BACKUP, 'rm -rf "$OUT"/.tmp-*\n', "", expect="a killed run's plain dump was left behind")
def test_the_temp_folders_of_a_killed_run_are_removed(tmp_path):
    box = Box(tmp_path)
    stale = box.out / ".tmp-AbC123"
    stale.mkdir(parents=True)
    (stale / "relay.dump").write_bytes(DUMP)
    run = box.run()
    assert run.returncode == 0, run.stderr
    assert not stale.exists(), "a killed run's plain dump was left behind"


@mutation(BACKUP, "TELEGRAM_BOT_TOKEN=$(cfg TELEGRAM_BOT_TOKEN)", "export TELEGRAM_BOT_TOKEN=$(cfg TELEGRAM_BOT_TOKEN)",
          expect="the token reached a child's environment")
@mutation(BACKUP, "unset TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID ", "true ",
          expect="the token reached a child's environment")
def test_the_token_reaches_no_child_environment(tmp_path):
    box = Box(tmp_path)
    run = box.run(TELEGRAM_BOT_TOKEN=TOKEN)        # even one the caller exported
    assert run.returncode == 0, run.stderr
    envs = box.envs()
    assert envs.count("PATH=") >= 3                # docker, openssl and curl dumped theirs
    assert TOKEN not in envs, "the token reached a child's environment"


@mutation(BACKUP, "flock -n 9 ||", "true ||", expect="a second run was not refused")
def test_a_second_run_is_refused_while_one_holds_the_lock(tmp_path):
    box = Box(tmp_path)
    box.out.mkdir(parents=True)
    with open(box.out / "backup.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        run = box.run()
    assert run.returncode == 75, "a second run was not refused"
    assert box.backups() == [] and box.calls() == ""
