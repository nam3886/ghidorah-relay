"""meta ProviderEngine: worker only, drives a browser.

Only the account check is built; submit and fetch come with the next step, and no command kind exists yet."""

from relay_meta.engine import check as _check

NOT_BUILT = "meta %s is not built yet"


class MetaEngine:
    def __init__(self, settings):
        self.settings = settings

    async def submit(self, ctx, job):
        raise NotImplementedError(NOT_BUILT % "submit")

    def fetch(self, ctx, out):
        raise NotImplementedError(NOT_BUILT % "fetch")

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings)

    async def run_command(self, ctx, kind, args):
        raise ValueError("meta runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return MetaEngine(settings)
