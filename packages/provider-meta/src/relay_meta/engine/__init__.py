"""meta ProviderEngine: worker only, drives a browser.

Built: the account check, the image submit (held in the session) and the fetch. Video submit comes next; no command
kind exists yet."""

from relay_meta.engine import check as _check
from relay_meta.engine import fetch as _fetch
from relay_meta.engine import submit as _submit


class MetaEngine:
    def __init__(self, settings):
        self.settings = settings

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings)

    def fetch(self, ctx, out):
        return _fetch.fetch(ctx, out)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings)

    async def run_command(self, ctx, kind, args):
        raise ValueError("meta runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return MetaEngine(settings)
