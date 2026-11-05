"""meta ProviderEngine: worker only. Plain HTTP and one gateway WebSocket per job over curl_cffi, through each
account's proxy; no browser.

Each method is a thin delegate to the module that owns the behaviour. Bound once at build time: the settings view and
the process's `Sessions` (each account's token, a lock per account and the page gate per proxy IP), shared by the
submit, the check and the reconcile. The modules' own defaults are the real ones; their keyword seams are for tests.
"""

from relay_core.redact import register_cookie_names
from relay_meta.engine import check as _check
from relay_meta.engine import fetch as _fetch
from relay_meta.engine import reconcile as _reconcile
from relay_meta.engine import submit as _submit
from relay_meta.engine.web import Sessions

register_cookie_names("ecto_1_sess", "abra_sess", "rd_challenge")


class MetaEngine:
    def __init__(self, settings):
        self.settings = settings
        self.sessions = Sessions()

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings, self.sessions)

    async def poll(self, ctx, job):
        return await _reconcile.poll(ctx, job, self.settings)

    async def reconcile(self, ctx, job):
        return await _reconcile.reconcile(ctx, job, self.settings, self.sessions)

    def fetch(self, ctx, out):
        return _fetch.fetch(ctx, out)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings, self.sessions)

    async def run_command(self, ctx, kind, args):
        raise ValueError("meta runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return MetaEngine(settings)
