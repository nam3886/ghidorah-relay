"""Reconcile: what became of a job whose send outcome is unknown (`Ambiguous` after `mark_sent`), and the poll that
delivers what a reconcile found.

The submit persisted the conversation id (`external_ref`, also `provider_state.conversation_id`) before its one data
frame. Reconcile reads `GET /prompt/<uuid>` through the account's proxy: a server-rendered page holding the chat's
media links and `streamingState`. It only GETs (no socket, no upload, no frame), so nothing can be sent again. The
page load takes the proxy IP's gate like any other www.meta.ai page.
- signed in, every answer DONE and final links of the job's kind with no preview or unknown link left: `Found`
  (taken), with the links in `provider_state.outputs`;
- anything else: `StillUnknown`, `answered` when the chat's own page was read. `NotSent` is never returned: a chat
  with no answer may still be one meta is working on, and a re-send would charge twice. The render deadline fails
  such a job with its refund.

Jobs the browser engine left unknown carry the same ref (the `/prompt/<uuid>` of their chat) and reconcile the same.

`Found` moves the job to `waiting`; the poll then hands the recorded links to the fetcher, reading nothing upstream.
"""

import re
from urllib.parse import urlsplit

from relay_core.net import host_allowed
from relay_core.providers import Done, Found, Output, Pending, PollResult, StillUnknown
from relay_meta.constants import CHAT_PATH, MEDIA_HOSTS
from relay_meta.engine.web import GateClosed, NetError, Web, real_session
from relay_meta.engine.wire import is_final, link_class, media_outputs

_UUID = re.compile(r"[0-9a-z]{8}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{12}")
_STATE = re.compile(r'\\"streamingState\\":\\"([A-Za-z_]+)\\"')
_LINK = re.compile(r"https://[A-Za-z0-9.-]+/[^\s\"'<>\\]+")
# fbcdn links every prompt page shows that are no result: the avatar (it shares `/t39.`), the map tiles, and the
# home page's chat-list thumbnails, suggestion art and static files (fixtures/meta/http-261105/README.md)
_NOT_RESULTS = ("/t39.30808-6/", "/maps/", "/t0.83350-7/", "/v/t6/", "/rsrc.php/")


def parse_prompt_page(html: str, conversation: str, kind: str,
                      n: int | None = None) -> tuple[bool, tuple[str, ...], int]:
    """-> (done, finals, previews_left) of a `/prompt/<conversation>` page. `done`: every `streamingState` is DONE
    (and one is), on a page that names the conversation (else it is not its chat). `finals`: the distinct generated
    links of `kind`, in page order; the input, the avatar and the other kind are never finals.
    `previews_left`: 0 once the page holds the `n` finals paid for (a leftover preview, such as the edit's draft that
    stands as its own artifact, is ignored). Else every preview (for a job with no `n`: previews less finals, as the
    frame reader counts), plus every fbcdn link of a class not known, so the result stays unknown."""
    text = (html or "").replace("\\u0026", "&").replace("&amp;", "&").replace("\\/", "/")
    states = _STATE.findall(text)
    done = bool(states) and all(s == "DONE" for s in states) and conversation in text
    finals: dict[str, str] = {}
    previews: set[str] = set()
    unknown: set[str] = set()
    for url in _LINK.findall(text):
        cls, path = link_class(url), urlsplit(url).path
        if cls == kind:
            finals.setdefault(path, url)
        elif cls == "preview":
            previews.add(path)
        elif (cls == "other" and host_allowed(url, MEDIA_HOSTS, default_port_only=True)
              and not any(m in path for m in _NOT_RESULTS)):
            unknown.add(path)
    if n and len(finals) >= n:
        return done, tuple(finals.values()), 0
    left = len(previews) if n else max(0, len(previews) - len(finals))
    return done, tuple(finals.values()), left + len(unknown)


async def reconcile(ctx, job, settings, sessions, *, make_session=real_session):
    """See the module text. `make_session` is the seam for tests."""
    kind = job.params.get("kind")
    conversation = job.external_ref or (job.provider_state or {}).get("conversation_id")
    if kind not in ("image", "video") or not isinstance(conversation, str) or not _UUID.fullmatch(conversation):
        return StillUnknown()
    account = ctx.account
    try:
        async with Web(account, settings, sessions, ctx, make_session=make_session) as web:
            page = await web.page(CHAT_PATH + conversation)
    except (GateClosed, NetError):
        return StillUnknown(answered=False)
    if page.state != "in":
        return StillUnknown(answered=False)
    sessions.keep(account.id, account.credential_gen, page)     # a page load is scarce: its token serves the submit
    n = job.params.get("n")
    done, finals, left = parse_prompt_page(page.html, conversation, kind, n)
    if done and finals and not left:
        outputs = [{"ref": o.ref, "mime": o.mime} for o in media_outputs(finals, conversation, kind, n)]
        return Found(conversation, {"conversation_id": conversation, "outputs": outputs})
    return StillUnknown(answered=conversation in page.html)


async def poll(ctx, job, settings) -> PollResult:
    """The links a reconcile found, handed to the fetcher; reads nothing upstream. A job with none recorded (it was
    never found) is a poll that did not answer: the render deadline bounds it."""
    state = job.provider_state or {}
    kind = "image" if job.params.get("kind") == "image" else "video"
    outs = list(state.get("outputs") or ())
    if not outs or not all(isinstance(o, dict) and is_final(str(o.get("ref") or ""), kind) for o in outs):
        return PollResult(Pending("no result was recorded for this meta job", answered=False))
    conversation = state.get("conversation_id") or job.external_ref
    return PollResult(Done(tuple(Output(o["ref"], str(o.get("mime") or ""), i, meta={"conversation_id": conversation})
                                 for i, o in enumerate(outs))))
