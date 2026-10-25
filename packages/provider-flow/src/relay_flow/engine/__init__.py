"""flow ProviderEngine: worker only, pure HTTP (no browser).

Each method is a thin delegate to the module that owns the behaviour. Bound once at build time: the settings view, the
process's `Sessions` (each account's newest cookie jar and its rotation lock) and the reCAPTCHA token source, whose
solver keys are read from the environment once and removed from it.
"""

import os

from relay_flow.constants import CAPTCHA_KEY_VARS
from relay_flow.engine import check as _check
from relay_flow.engine import results as _results
from relay_flow.engine import submit as _submit
from relay_flow.engine.captcha import Tokens
from relay_flow.engine.http import Sessions


class FlowEngine:
    def __init__(self, settings, captcha_keys: dict[str, str | None]):
        self.settings = settings
        self.sessions = Sessions()
        self.tokens = Tokens(settings, captcha_keys)

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings, self.sessions, self.tokens)

    async def poll(self, ctx, job):
        return await _results.poll(ctx, job, self.settings, self.sessions)

    async def reconcile(self, ctx, job):
        return await _results.reconcile(ctx, job, self.settings, self.sessions)

    def fetch(self, ctx, out):
        return _results.fetch(ctx, out)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings, self.sessions)

    async def run_command(self, ctx, kind, args):
        raise ValueError("flow runs no command of kind %r" % kind)


def build_engine(spec, settings, env=os.environ):
    """The keys leave the environment here, so nothing started later can read them there."""
    return FlowEngine(settings, {source: (env.pop(var, None) or "").strip() or None
                                 for source, var in CAPTCHA_KEY_VARS.items()})
