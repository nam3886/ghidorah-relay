"""qwen ProviderEngine: worker only. Plain HTTP for every call; Chrome (relay_browser) only to mint the anti-bot pair
(engine/mint.py), so the worker runs the browser image.

Each method is a thin delegate to the module that owns the behaviour. Bound once at build time: the settings view,
the process's `Sessions` (each account's newest credential and access token) and the `Minter` (one browser at a time).
"""

from relay_qwen.engine import check as _check
from relay_qwen.engine import results as _results
from relay_qwen.engine import submit as _submit
from relay_qwen.engine.http import Sessions
from relay_qwen.engine.mint import Minter


class QwenEngine:
    def __init__(self, settings, minter: Minter | None = None):
        self.settings = settings
        self.sessions = Sessions()
        self.minter = minter or Minter()

    async def submit(self, ctx, job):
        return await _submit.submit(ctx, job, self.settings, self.sessions, self.minter)

    async def poll(self, ctx, job):
        return await _results.poll(ctx, job, self.settings, self.sessions)

    async def reconcile(self, ctx, job):
        return await _results.reconcile(ctx, job, self.settings, self.sessions)

    def fetch(self, ctx, out):
        return _results.fetch(ctx, out)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings, self.sessions, self.minter)

    async def run_command(self, ctx, kind, args):
        raise ValueError("qwen runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return QwenEngine(settings)
