"""gemini ProviderEngine: worker only, pure HTTP (no browser).

Each method is a thin delegate to the module that owns the behaviour. Bound once at build time: the settings view and
the process's `Sessions` (each account's newest cookie jar and its rotation lock).
"""

from relay_gemini.engine import check as _check
from relay_gemini.engine import results as _results
from relay_gemini.engine import submit as _submit
from relay_gemini.engine.http import Sessions


class GeminiEngine:
    def __init__(self, settings):
        self.settings = settings
        self.sessions = Sessions()

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings, self.sessions)

    async def poll(self, ctx, job):
        return await _results.poll(ctx, job, self.settings, self.sessions)

    async def reconcile(self, ctx, job):
        return await _results.reconcile(ctx, job, self.settings, self.sessions)

    def fetch(self, ctx, out):
        return _results.fetch(ctx, out, self.settings, self.sessions)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings, self.sessions)

    async def run_command(self, ctx, kind, args):
        raise ValueError("gemini runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return GeminiEngine(settings)
