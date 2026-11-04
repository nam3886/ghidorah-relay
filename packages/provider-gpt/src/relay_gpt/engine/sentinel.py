"""The anti-bot steps before the send, all free: chat-requirements prepare -> proof of work -> Turnstile -> finalize,
then the conversation prepare that gives the conduit token (FINDINGS F2-F4).

prepare asking for `arkose` or `force_login` is a `Challenge` (the relay never does either over HTTP). The Turnstile
answer is always made (`solve_dx` in a thread); when the solver fails the send goes on with an empty answer
(accepted today, F4) and `Tokens.solved` is False, so the submit warns `gpt.turnstile_unsolved` (design 1c). No
token is logged or put in an error: they are all secrets.
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from relay_gpt.engine.pow import config, requirements_token, solve
from relay_gpt.engine.turnstile import solve_dx
from relay_gpt.engine.web import BadAnswer, Challenge, Page, Web

log = logging.getLogger(__name__)

PREPARE_PATH = "/backend-api/sentinel/chat-requirements/prepare"
FINALIZE_PATH = "/backend-api/sentinel/chat-requirements/finalize"
CONDUIT_PATH = "/backend-api/f/conversation/prepare"


@dataclass
class Tokens:
    chat: str            # finalize's token: OpenAI-Sentinel-Chat-Requirements-Token
    proof: str           # the proof of work: OpenAI-Sentinel-Proof-Token ("" when none was asked)
    turnstile: str       # the Turnstile answer ("" when none was asked or the solver failed)
    solved: bool         # False: a Turnstile was asked and the solver failed

    def headers(self) -> dict[str, str]:
        h = {"OpenAI-Sentinel-Chat-Requirements-Token": self.chat}
        if self.proof:
            h["OpenAI-Sentinel-Proof-Token"] = self.proof
        if self.turnstile:
            h["OpenAI-Sentinel-Turnstile-Token"] = self.turnstile
        return h


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


async def requirements(web: Web, page: Page, *, clock: Callable[[], float] = time.time,
                       rand: Callable[[], float] = random.random,
                       solve_turnstile: Callable[[str, str], str] = solve_dx) -> Tokens:
    """The sentinel tokens of one send. Raises Challenge, BadAnswer (and the Web's SignedOut/NetError)."""
    cfg = config(web.device_id, page.build, page.sdk_url, now=clock(), rand=rand)    # one config per send
    p = requirements_token(cfg)
    status, prep = await web.post_json(PREPARE_PATH, {"p": p})
    if status != 200 or not isinstance(prep, dict):
        raise BadAnswer(status, "the sentinel prepare")
    if _dict(prep.get("arkose")).get("required") is True:
        raise Challenge("arkose")
    if prep.get("force_login") is True:
        raise Challenge("force_login")
    pow_ = _dict(prep.get("proofofwork"))
    proof = ""
    if pow_.get("required") is True:
        seed, difficulty = pow_.get("seed"), pow_.get("difficulty")
        if not isinstance(seed, str) or not isinstance(difficulty, str):
            raise BadAnswer(status, "the sentinel prepare (no proof-of-work seed)")
        proof, _ = await asyncio.to_thread(solve, seed, difficulty, cfg, clock=clock)
    turnstile, solved = "", True
    dx = _dict(prep.get("turnstile")).get("dx")
    if isinstance(dx, str) and dx:
        try:
            turnstile = await asyncio.to_thread(solve_turnstile, p, dx)
        except Exception as e:      # noqa: BLE001 - design 1c: a failed solver never stops the send
            log.warning("gpt account %s: the Turnstile answer could not be made (%s); sent empty",
                        web.account.id, type(e).__name__)
            turnstile, solved = "", False
    status, fin = await web.post_json(FINALIZE_PATH, {"prepare_token": prep.get("prepare_token") or "",
                                                      "proof_token": proof, "turnstile_token": turnstile})
    token = _dict(fin).get("token")
    if status != 200 or not isinstance(token, str) or not token:
        raise BadAnswer(status, "the sentinel finalize")
    return Tokens(token, proof, turnstile, solved)


async def conduit(web: Web, tokens: Tokens, body: dict) -> str:
    """`POST /backend-api/f/conversation/prepare` with the sentinel headers: its conduit token, "" when it names none.
    Not 200 -> BadAnswer."""
    status, answer = await web.post_json(CONDUIT_PATH, body, tokens.headers())
    if status != 200:
        raise BadAnswer(status, "the conversation prepare")
    token = _dict(answer).get("conduit_token")
    return token if isinstance(token, str) else ""
