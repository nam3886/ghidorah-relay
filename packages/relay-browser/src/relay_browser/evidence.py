"""Evidence (contracts §6.4): what an operator needs to see why a job went wrong, never a secret.

- after-send JPEG (q60) for every job that got a reply;
- on a UI error, or when no receipt arrived: PNG, the page HTML (at most 2 M characters, served as text so it can never
  run) and the network log.

Text goes through the redactor the CALLER passes (`relay_core.redact.redact_text`): the kit holds no secret patterns of
its own and does not import the core. A screenshot is pixels, so it is not redacted; the HTML and log are. Capturing
is diagnostics only: a failure to capture is logged and skipped, it never fails the job.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from .net import NetRecorder, redact_cut

log = logging.getLogger("relay_browser.evidence")

HTML_MAX_CHARS = 2_000_000
TEXT_MIME = "text/plain; charset=utf-8"


@dataclass(frozen=True)
class Evidence:
    name: str
    data: bytes
    mime: str


class EvidenceWriter:
    def __init__(self, redact: Callable[[str], str], recorder: NetRecorder | None = None, *,
                 html_max_chars: int = HTML_MAX_CHARS, clock: Callable[[], float] = time.time):
        self._redact, self._recorder, self._html_max, self._clock = redact, recorder, html_max_chars, clock

    def _stamp(self) -> int:
        return int(self._clock() * 1000)

    async def after_send(self, page) -> Evidence | None:
        """The page right after the site answered the send: the sent message, any question it asked, its receipt."""
        try:
            data = await page.screenshot(type="jpeg", quality=60, full_page=False)
        except Exception as exc:      # noqa: BLE001
            log.warning("after-send snapshot failed: %s", str(exc)[:80])
            return None
        return Evidence("after-send-%d.jpg" % self._stamp(), data, "image/jpeg")

    async def ui_error(self, page, tag: str) -> list[Evidence]:
        """PNG, HTML (as text) and the network log, for a UI error or a send that got no receipt."""
        stem = "ui-error-%s-%d" % (re.sub(r"[^a-z0-9]+", "_", tag.lower())[:40], self._stamp())
        out: list[Evidence] = []
        try:
            out.append(Evidence(stem + ".png", await page.screenshot(full_page=False), "image/png"))
        except Exception as exc:      # noqa: BLE001
            log.warning("ui-error snapshot failed: %s", str(exc)[:80])
        try:
            # Redacted before the cut (see `redact_cut`) and in a thread: a whole page would stall the event loop.
            html = await asyncio.to_thread(redact_cut, await page.content() or "", self._html_max, self._redact)
            out.append(Evidence(stem + ".html.txt", html.encode("utf-8"), TEXT_MIME))
        except Exception as exc:      # noqa: BLE001
            log.warning("ui-error html failed: %s", str(exc)[:80])
        if self._recorder is not None:
            try:
                dump = await self._recorder.dump_async(self._redact)
                out.append(Evidence(stem + ".network.txt", dump.encode("utf-8"), TEXT_MIME))
            except Exception as exc:      # noqa: BLE001 - a failing redactor must not replace the real UI error
                log.warning("ui-error network log failed: %s", str(exc)[:80])
        return out
