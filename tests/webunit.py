"""Run Vitest specs of apps/admin-web from source, for the mutation markers on the TypeScript (scripts/mutate.py).

A marked test is a plain Python test that calls `vitest("tests/unit/lib/operation-key.spec.ts")`; the marker names the
.ts file under apps/admin-web/src and the anchor in it:

    @mutation("apps/admin-web/src/lib/operation-key.ts", "status >= 500", "status > 500", expect="keeps the key")
    @pytest.mark.web_unit
    def test_operation_key_rules():
        vitest("tests/unit/lib/operation-key.spec.ts")

`expect` is a string of the failure: the last lines of a failed run are a list `FAILED <spec> > <describe> > <test
title>`, one per failed test, taken from Vitest's JSON report, so a marker can expect the exact test that must fail
(an unrelated failure, such as a compile error, lists `(the file did not run)` and no test title). The spec runs in
the web-src image (MUTATE_WEB_IMAGE, set by mutate.py) with this tree's src/ and tests/ mounted read-only over the
image's copy: the node_modules are the image's, so the mutated source runs with no rebuild. The web_unit mark keeps
these tests out of the plain pytest run (`web.sh check` already runs every spec); mutate.py runs them by node id.
"""

import json
import os
import shlex
import subprocess

from guards import REPO

APP = REPO / "apps" / "admin-web"
CONTAINER_APP = "/web/apps/admin-web"
LIMIT_S = 240
REPORT = "/tmp/vitest-report.json"
SENTINEL = "@@VITEST-JSON@@"


def vitest(*specs: str) -> None:
    image = os.environ.get("MUTATE_WEB_IMAGE")
    assert image, "MUTATE_WEB_IMAGE is not set: these tests run through scripts/mutate.py, which builds the web image"
    assert specs, "name at least one spec"
    cpus = ["--cpuset-cpus", os.environ["RELAY_GATE_CPUSET"]] if os.environ.get("RELAY_GATE_CPUSET") else []
    # `timeout` inside the container: a run killed from outside (mutate.py stops the pytest group) leaves no container
    # behind for longer than that.
    # Vitest also writes its JSON report to a file, which is printed after SENTINEL, so the failed tests can be listed
    # exactly whatever the length of the console output.
    inner = ("timeout %d npm run test -- %s --reporter=default --reporter=json --outputFile.json=%s; rc=$?; "
             "echo %s; cat %s 2>/dev/null; exit $rc"
             % (LIMIT_S - 20, " ".join(shlex.quote(s) for s in specs), REPORT, SENTINEL, REPORT))
    cmd = ["docker", "run", "--rm", *cpus, "-e", "NO_COLOR=1", "-e", "FORCE_COLOR=0",
           "-v", "%s:%s/src:ro" % (APP / "src", CONTAINER_APP), "-v", "%s:%s/tests:ro" % (APP / "tests", CONTAINER_APP),
           "-w", CONTAINER_APP, image, "sh", "-c", inner]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=LIMIT_S, check=False)
    if run.returncode != 0:
        console, report = split_report(run.stdout)
        tail = (console + run.stderr)[-3000:]
        raise AssertionError("vitest failed (%s):\n%s\n%s" % (", ".join(specs), tail, "\n".join(failed_tests(report))))


def split_report(stdout: str) -> tuple[str, str]:
    """(console output, JSON report text) of a run: the report follows the SENTINEL line, if the run got that far."""
    console, _, report = stdout.partition(SENTINEL + "\n")
    return console, report


def failed_tests(report: str) -> list[str]:
    """`FAILED <spec> > <describe> > ... > <test title>` for every failed test of a Vitest JSON report, one line each.
    A spec file that failed before running any test (a compile error, a bad import) is `FAILED <spec> > (the file did
    not run)`: it names no test, so a marker that expects a test title is not satisfied by it."""
    try:
        results = json.loads(report)["testResults"]
    except (ValueError, KeyError, TypeError):
        return ["(no Vitest report: the run ended before it wrote one)"]
    lines = []
    for result in results:
        spec = result.get("name", "?").removeprefix(CONTAINER_APP + "/")
        failed = [t for t in result.get("assertionResults", []) if t.get("status") == "failed"]
        for t in failed:
            lines.append(" > ".join(["FAILED " + spec, *t.get("ancestorTitles", []), t.get("title", "?")]))
        if result.get("status") == "failed" and not failed:
            lines.append("FAILED %s > (the file did not run)" % spec)
    return lines
