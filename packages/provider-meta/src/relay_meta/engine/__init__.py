"""meta ProviderEngine: worker only, drives a browser.

Built: the account check, the image and video submit (held in the session), the reconcile of an unknown send (it
reopens the saved chat and never sends), the poll that hands what it found to the fetcher, and the fetch. No command
kind exists yet."""

from relay_meta.engine import check as _check
from relay_meta.engine import fetch as _fetch
from relay_meta.engine import reconcile as _reconcile
from relay_meta.engine import submit as _submit


class MetaEngine:
    def __init__(self, settings):
        self.settings = settings

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings)

    async def poll(self, ctx, job):
        return await _reconcile.poll(ctx, job, self.settings)

    async def reconcile(self, ctx, job):
        return await _reconcile.reconcile(ctx, job, self.settings)

    def fetch(self, ctx, out):
        return _fetch.fetch(ctx, out)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings)

    async def run_command(self, ctx, kind, args):
        raise ValueError("meta runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return MetaEngine(settings)
