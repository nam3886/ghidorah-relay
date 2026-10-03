"""`@mutation(file, original, replacement)`: a mutation `scripts/mutate.py` applies to prove a test is a real guard.

The marker is a no-op at test time. It only records metadata on the test function, and `scripts/mutate.py`
reads it from the source (it never imports the tests), so `file`, `original` and `replacement` must be
string literals or module-level string constants of the same test file.
`expect` (optional keyword) is a substring the test's failure output must contain: a mutation counts as killed only
when the test fails that way, not on a helper timeout or an unrelated error. Without it the test must still FAIL
(an assertion or error inside the marked test, never a collection error).
`file` is relative to the repo root; `original` must occur in it exactly once.
"""


def mutation(file: str, original: str, replacement: str, *, expect: str | None = None):
    def mark(test):
        test.relay_mutations = [*getattr(test, "relay_mutations", []), (file, original, replacement, expect)]
        return test

    return mark
