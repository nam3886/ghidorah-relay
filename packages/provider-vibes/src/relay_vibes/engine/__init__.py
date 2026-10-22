"""vibes ProviderEngine: worker only, pure HTTP (no browser).

Only the account check is built; submit, poll and fetch come with the next step, and no command kind exists yet."""

from relay_vibes.engine import check as _check

NOT_BUILT = "vibes %s is not built yet"


class VibesEngine:
    def __init__(self, settings):
        self.settings = settings

    async def submit(self, ctx, job):
        raise NotImplementedError(NOT_BUILT % "submit")

    async def poll(self, ctx, job):
        raise NotImplementedError(NOT_BUILT % "poll")

    def fetch(self, ctx, out):
        raise NotImplementedError(NOT_BUILT % "fetch")

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings)

    async def run_command(self, ctx, kind, args):
        raise ValueError("vibes runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return VibesEngine(settings)
