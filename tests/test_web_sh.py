"""scripts/web.sh on the Mac side of `format`: what is pulled back from the box is applied only to files that still have
the content that was pushed. box.sh and rsync are stubs (nothing here needs Node, Docker or the box); the container run
itself is shown by the commands in the round report."""

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

from relay_core.testing.mutation import mutation

from guards import REPO

WEB = "scripts/web.sh"

RSYNC_STUB = """\
#!/usr/bin/env bash
# `rsync ... box:relay-runs/FOLDER/.web-format/ DEST/`: copies what the pretend box holds into DEST
dest="${@: -1}"
cp -R "@OUT@/." "$dest"
"""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def planted(tmp_path: Path, pushed: dict[str, str], mac_now: dict[str, str], formatted: dict[str, str]) -> Path:
    """A tree with web.sh and a stub box.sh, the Mac copies of the files, and the box's answer."""
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    shutil.copy(REPO / WEB, tree / WEB)
    (tree / "scripts" / "box.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (tree / "scripts" / "box.sh").chmod(0o755)
    for rel, text in mac_now.items():
        path = tree / "apps" / "admin-web" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    out = tmp_path / "box-out"
    (out / "files").mkdir(parents=True)
    rows = []
    for rel, text in formatted.items():
        (out / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / "files" / rel).write_text(text, encoding="utf-8")
        rows.append("%s  %s\n" % (sha(pushed[rel]), rel))
    (out / "MANIFEST").write_text("".join(rows), encoding="utf-8")
    bins = tmp_path / "bin"
    bins.mkdir()
    (bins / "rsync").write_text(RSYNC_STUB.replace("@OUT@", str(out)), encoding="utf-8")   # web.sh runs rsync env -i
    (bins / "rsync").chmod(0o755 | stat.S_IEXEC)
    return tree


def format_run(tree: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {"PATH": "%s:%s" % (tmp_path / "bin", os.environ["PATH"]), "HOME": str(tmp_path),
           "TMPDIR": str(tmp_path)}
    return subprocess.run(["bash", str(tree / WEB), "--tree", "dev9", "format"], cwd=tree, env=env,
                          capture_output=True, text=True)


PUSHED = {"src/a.ts": "a  =1\n", "src/b.ts": "b  =1\n"}
FORMATTED = {"src/a.ts": "a = 1\n", "src/b.ts": "b = 1\n"}


@mutation(WEB, "    if now != pushed:", "    if False:", expect="a file edited during the run was overwritten")
def test_format_overwrites_a_file_only_if_it_still_has_its_pushed_content_and_lists_the_others(tmp_path):
    mac = {**PUSHED, "src/b.ts": "b  =1 // edited while the formatter ran\n"}
    tree = planted(tmp_path, PUSHED, mac, FORMATTED)
    run = format_run(tree, tmp_path)
    app = tree / "apps" / "admin-web"
    assert (app / "src/a.ts").read_text(encoding="utf-8") == "a = 1\n", \
        "an untouched file was not reformatted: " + run.stdout + run.stderr
    assert (app / "src/b.ts").read_text(encoding="utf-8") == mac["src/b.ts"], \
        "a file edited during the run was overwritten"
    assert run.returncode == 1 and "src/b.ts changed during format, rerun" in run.stdout, run.stdout + run.stderr
    assert "1 file(s) reformatted, 1 skipped" in run.stdout, run.stdout


@mutation(WEB, "    shutil.copyfile(os.path.join(got, \"files\", rel), dest)\n    done += 1", "    done += 1",
          expect="a file was not reformatted")
def test_format_without_a_race_reformats_every_changed_file_and_says_how_many(tmp_path):      # the negative control
    tree = planted(tmp_path, PUSHED, PUSHED, FORMATTED)
    run = format_run(tree, tmp_path)
    app = tree / "apps" / "admin-web"
    assert run.returncode == 0, run.stdout + run.stderr
    for rel, text in FORMATTED.items():
        assert (app / rel).read_text(encoding="utf-8") == text, "a file was not reformatted: " + rel
    assert "2 file(s) reformatted, 0 skipped" in run.stdout, run.stdout



def run_web(tmp_path: Path, *args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    """web.sh on the Mac side with a box.sh stub that records what it is asked to run."""
    tree = planted(tmp_path, {}, {}, {})
    (tree / "scripts" / "box.sh").write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$TMPDIR/box.args"\n',
                                             encoding="utf-8")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    run = subprocess.run(["bash", str(tree / WEB), *args], cwd=tree, env=env, capture_output=True, text=True)
    sent = tmp_path / "box.args"
    return run, sent.read_text(encoding="utf-8").split() if sent.exists() else []


@mutation(WEB, '${s#"$APP/"}', "$s", expect="a spec path with the app prefix was forwarded as is")
@mutation(WEB, './scripts/web.sh "$cmd" "$@"', './scripts/web.sh "$cmd"', expect="the spec files were dropped")
def test_test_forwards_the_spec_files_relative_to_the_app(tmp_path):
    specs = ("apps/admin-web/tests/unit/a.spec.ts", "tests/unit/b.spec.ts")
    run, sent = run_web(tmp_path, "--tree", "dev9", "test", *specs)
    assert run.returncode == 0, run.stdout + run.stderr
    assert sent[-4:] == ["./scripts/web.sh", "test", "tests/unit/a.spec.ts", "tests/unit/b.spec.ts"], \
        "the spec files were dropped, or a spec path with the app prefix was forwarded as is: %r" % sent


@mutation(WEB, '[ $# -gt 0 ] || die "test needs at least one spec file"', "true",
          expect="test ran with no spec (the whole suite)")
@mutation(WEB, '[[ "$s" =~ ^[A-Za-z0-9_][A-Za-z0-9_./-]*$ ]] ||', "true ||",
          expect="a vitest flag was accepted as a spec")
@mutation(WEB, '  [ $# -eq 0 ] || die "one subcommand only"', "  true", expect="extra arguments were accepted")
def test_test_needs_a_spec_refuses_flags_and_the_other_subcommands_take_no_argument(tmp_path):
    cases = [(("test",), "at least one spec", "test ran with no spec (the whole suite)"),
             (("test", "--watch"), "not a spec file path", "a vitest flag was accepted as a spec"),
             (("test", "a.ts; rm"), "not a spec file path", "a vitest flag was accepted as a spec"),
             (("check", "tests/unit/a.spec.ts"), "one subcommand only", "extra arguments were accepted")]
    for n, (args, text, bug) in enumerate(cases):
        run, sent = run_web(tmp_path / str(n), *args)
        assert run.returncode == 2 and text in run.stderr and not sent, \
            "%s: %r was not refused: %s" % (bug, args, run.stdout + run.stderr)
