"""grok ProviderEngine: worker only. Plain HTTP over curl_cffi and the Imagine WebSocket over aiohttp, through each
account's proxy; a text to image is sent from a Chrome page instead while `grok.t2i_transport` is `browser`
(engine/browser.py).

Each method is a thin delegate to the module that owns the behaviour, bound once at build time to the settings view.
`poll` hands a reconcile's found outputs to the fetcher (core moves a `Found` job to `waiting`; only a poll delivers
it). The `x-statsig-id` generator (`grok.statsig_mode`, off by default: no header) is built per account and kept, so
its seed page is read once per `STATSIG_SEED_MAX_AGE_S`, not once per job; it is dropped when the mode goes back to
off. `make_session` is the test seam; the real one is a curl_cffi session.
"""

from relay_core.redact import register_cookie_names
from relay_grok.engine import check as _check
from relay_grok.engine import results as _results
from relay_grok.engine import statsig as _statsig
from relay_grok.engine import submit as _submit
from relay_grok.engine.drift import Unreadable, fetch_page
from relay_grok.engine.web import proxy_url, real_session

register_cookie_names("sso", "sso-rw", "x-userid")


class GrokEngine:
    def __init__(self, settings, *, make_session=real_session):
        self.settings = settings
        self.make_session = make_session
        self.signers: dict[tuple, object] = {}

    def statsig(self, account):
        """The account's `x-statsig-id` generator, None when `grok.statsig_mode` is off. An unreadable page is a page
        with no seed (no header, one log line), never a failed job."""
        async def page():
            try:
                return await fetch_page(account, self.settings, make_session=self.make_session)
            except Unreadable:
                return "", []
        key = (account.id, proxy_url(account.proxy))
        made = _statsig.factory(self.settings, page)
        if made is None:
            self.signers.pop(key, None)
            return None
        return self.signers.setdefault(key, made)

    async def submit(self, ctx, job):
        deps = _submit.Deps(make_session=self.make_session, statsig=self.statsig(ctx.account))
        return await _submit.submit(ctx, job, self.settings, deps=deps)

    async def poll(self, ctx, job):
        return await _results.poll(ctx, job, self.settings)

    async def reconcile(self, ctx, job):
        return await _results.reconcile(ctx, job, self.settings, make_session=self.make_session)

    def fetch(self, ctx, out):
        return _results.fetch(ctx, out, self.settings, make_session=self.make_session)

    async def check_account(self, ctx):
        return await _check.check_account(ctx, self.settings, make_session=self.make_session)

    async def run_command(self, ctx, kind, args):
        raise ValueError("grok runs no command of kind %r" % kind)


def build_engine(spec, settings):
    return GrokEngine(settings)
