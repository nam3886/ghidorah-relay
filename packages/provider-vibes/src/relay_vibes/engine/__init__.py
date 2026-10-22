"""vibes ProviderEngine: worker only, pure HTTP (no browser).

Each method is a thin delegate to the module that owns the behaviour. The settings view is bound once at build time,
and so is the process's checkpoint rests (`Rests`), which every call of an account consults first."""

from relay_vibes.engine import batch as _batch
from relay_vibes.engine import check as _check
from relay_vibes.engine import submit as _submit
from relay_vibes.engine.http import Rests


class VibesEngine:
    def __init__(self, settings):
        self.settings = settings
        self.rests = Rests(settings=settings)

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings, self.rests)

    async def poll(self, ctx, job):
        return await _batch.poll(ctx, job, self.settings, self.rests)

    async def reconcile(self, ctx, job):
        return await _batch.reconcile(ctx, job, self.settings, self.rests)

    def fetch(self, ctx, out):
        return _batch.fetch(ctx, out, self.settings, self.rests)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings, rests=self.rests)

    async def run_command(self, ctx, kind, args):
        raise ValueError("vibes runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return VibesEngine(settings)
