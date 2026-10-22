"""vibes' error codes and warning codes.

A code is registered (`relay_core.errors.register`) together with the first running code that uses it: the registry
refuses a code nobody assigns. The account check ends no job, so vibes registers none yet; its codes arrive with submit.
Warning codes are not rules: a warning never ends a job, it rides on a result as `Warning(code, text)`.
"""

from relay_core.errors import Rule

CODES: dict[str, Rule] = {}

# Warnings (not rules).
WARN_PRICE_FALLBACK = "vibes.price_fallback"
