"""muse ProviderEngine: worker only, plain HTTP and a Noise tunnel over a WebSocket (no browser).

Each method is a thin delegate to the module that owns the behaviour; the settings view is bound at build time."""

from relay_muse.engine import check as _check
from relay_muse.engine import results as _results
from relay_muse.engine import submit as _submit


class MuseEngine:
    def __init__(self, settings):
        self.settings = settings

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings)

    async def poll(self, ctx, job):
        return await _results.poll(ctx, job, self.settings)

    async def reconcile(self, ctx, job):
        return await _results.reconcile(ctx, job, self.settings)

    def fetch(self, ctx, out):
        return _results.fetch(ctx, out, self.settings)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings)

    async def run_command(self, ctx, kind, args):
        raise ValueError("muse runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return MuseEngine(settings)
