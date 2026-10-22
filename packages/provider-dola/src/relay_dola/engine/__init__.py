"""dola ProviderEngine: worker only, drives a browser.

The package imports relay_browser only through its submodules (the kit package imports nothing). Each method is a thin
delegate to the module that owns the behaviour; the settings view is bound once at build time."""

from relay_dola.engine import check as _check
from relay_dola.engine import commands as _commands
from relay_dola.engine import fetch as _fetch
from relay_dola.engine import poll as _poll
from relay_dola.engine import reconcile as _reconcile
from relay_dola.engine import submit as _submit


class DolaEngine:
    def __init__(self, settings):
        self.settings = settings

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings)

    async def poll(self, ctx, job):
        return await _poll.poll(ctx, job, self.settings)

    async def reconcile(self, ctx, job):
        return await _reconcile.reconcile(ctx, job, self.settings)

    def fetch(self, ctx, out):
        return _fetch.fetch(ctx, out, self.settings)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings)

    async def probe_proxy(self, proxy):
        return await _check.probe_proxy(proxy)

    async def run_command(self, ctx, kind, args):
        return await _commands.run_command(ctx, kind, args, self.settings)


def build_engine(spec, settings):
    return DolaEngine(settings)
