"""`@mutation(file, original, replacement)`: a mutation `scripts/mutate.py` applies to prove a test is a real guard.

The marker is a no-op at test time. It only records metadata on the test function, and `scripts/mutate.py`
reads it from the source (it never imports the tests), so `file`, `original` and `replacement` must be
string literals or module-level string constants of the same test file.
`file` is relative to the repo root; `original` must occur in it exactly once.
"""


def mutation(file: str, original: str, replacement: str):
    def mark(test):
        test.relay_mutations = [*getattr(test, "relay_mutations", []), (file, original, replacement)]
        return test

    return mark
