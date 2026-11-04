"""gpt ProviderEngine: worker only. Plain HTTP over curl_cffi through each account's proxy; no browser.

Each method is a thin delegate to the module that owns the behaviour. Bound once at build time: the settings view and
the process's `Sessions` (each account's access token, a lock per account and the home page). The modules' own
defaults are the real ones (curl_cffi sessions, the Turnstile solver, the wall clock); their keyword seams are for
tests.
"""

from relay_core.redact import register_cookie_names
from relay_gpt.constants import SESSION_COOKIE
from relay_gpt.engine import check as _check
from relay_gpt.engine import results as _results
from relay_gpt.engine import submit as _submit
from relay_gpt.engine.web import Sessions

register_cookie_names(SESSION_COOKIE, SESSION_COOKIE + ".0", SESSION_COOKIE + ".1")


class GptEngine:
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
        raise ValueError("gpt runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return GptEngine(settings)
