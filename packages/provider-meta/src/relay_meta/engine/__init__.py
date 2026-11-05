"""meta ProviderEngine: worker only, drives a browser.

Built: the account check, the image and video submit (held in the session), the reconcile of an unknown send (it
reopens the saved chat and never sends), the poll that hands what it found to the fetcher, and the fetch. No command
kind exists yet."""

import importlib


def _mod(name):
    # Imported on first use, so the HTTP modules (`wire`, ...) load while the browser modules still sit beside them.
    return importlib.import_module("relay_meta.engine." + name)


class MetaEngine:
    def __init__(self, settings):
        self.settings = settings

    async def submit(self, ctx, job):
        return await _mod("submit").submit(ctx, job, self.settings)

    async def poll(self, ctx, job):
        return await _mod("reconcile").poll(ctx, job, self.settings)

    async def reconcile(self, ctx, job):
        return await _mod("reconcile").reconcile(ctx, job, self.settings)

    def fetch(self, ctx, out):
        return _mod("fetch").fetch(ctx, out)

    async def check_account(self, ctx):
        return await _mod("check").check_account(ctx, self.settings)

    async def run_command(self, ctx, kind, args):
        raise ValueError("meta runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return MetaEngine(settings)
