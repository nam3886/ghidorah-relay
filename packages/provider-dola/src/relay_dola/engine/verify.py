"""Login state, read from INSIDE the dola tab (ported from dola_v4 `verify.py`; the video check there was dead code).

Login is trusted only from signals inside the tab: calling `recent_conv` over HTTP from another machine once returned
5 conversations for a cookie jar the browser could not use at all (contracts v4 section 2.4). The probe below runs in
the page with `page.evaluate`; from outside it means nothing.

The same probe lists the conversations of an account: reconcile needs that list, and it is the only reliable way to
learn whether a send reached dola.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from relay_core.providers import Cooldown, Suspect
from relay_dola.constants import LOGIN_DOUBT_GAP_S, LOGIN_DOUBT_REST_S

# How many conversations the probe reads. PUBLIC because reconcile must know it: a list of exactly this length was cut,
# so "my conversation is not in it" is no proof the send never happened (a busy account pushes the newest out of it).
CONV_LIST_LIMIT = 30

LOGIN_PROBE_JS = """async()=>{
  const qp=new URLSearchParams({aid:'495671',real_aid:'495671',device_platform:'web',
    language:'en',region:'VN',pkg_type:'release_version',version_code:'20800',web_platform:'browser'});
  const body={cmd:3200,uplink_body:{pull_recent_conv_chain_uplink_body:{limit:__LIMIT__,message_count_per_conv:1,
    api_version:1,conv_version:0,direction:3,option:{not_need_message:true,need_complete_conversation:true}}},
    sequence_id:String(Date.now()),channel:2,version:'1'};
  let cells=-1, ids=[], hasMore=null;
  try{
    const r=await fetch('/im/chain/recent_conv?'+qp.toString(),{method:'POST',credentials:'include',
      headers:{'accept':'*/*','agw-js-conv':'str','content-type':'application/json; encoding=utf-8'},
      body:JSON.stringify(body)});
    const j=await r.json();
    const dl=(j.downlink_body||{}).pull_recent_conv_chain_downlink_body;
    if (dl) {
      const list = dl.cells || [];
      cells = list.length;
      hasMore = (dl.has_more===undefined || dl.has_more===null) ? null : !!dl.has_more;
      ids = list.map(c=>{const cc=c.conversation||{};return String(cc.conversation_id||cc.id||'');})
                .filter(Boolean);
    }
  }catch(e){ cells=-1; }
  const modal=/phone_login_entry_title_cn|Continue with Google/.test(document.body.innerText||'');
  const leaves=[...document.querySelectorAll('*')].filter(e=>e.childElementCount===0)
    .map(e=>(e.textContent||'').trim()).filter(Boolean);
  const name=leaves.filter(t=>/\\d{3}$/.test(t)&&t.length<=26&&/[A-Za-zÀ-ỹ]/.test(t))[0]||'';
  let passport=null;
  try{
    const pq=new URLSearchParams({aid:'495671',real_aid:'495671',device_platform:'web',language:'en',
      region:'VN',version_code:'20800','use-olympus-account':'1',web_platform:'browser'});
    const pr=await fetch('/passport/account/info/v2/?'+pq.toString(),{credentials:'include'});
    const pj=await pr.json(); const pd=pj.data||{};
    passport={message:String(pj.message||''), uid:!!(pd.user_id_str||pd.user_id),
              name:String(pd.name||pd.screen_name||pd.nickname||'')};
  }catch(e){ passport=null; }
  return {cells, ids, hasMore, name, modal, passport};
}""".replace("__LIMIT__", str(CONV_LIST_LIMIT))


def read_passport(p) -> tuple[str, str]:
    """(session, name) from `/passport/account/info/v2/` called inside the tab: session is `user`, `guest` or
    `unknown`. The direct answer to "who is this cookie", instead of guessing from the screen text (the old name
    heuristic gave an empty name for 6 of 13 accounts, and an account with no conversation yet was declared dead).

    A guest tab also gets HTTP 200, `message: "error"` and `data.name == "account_info_error"`: reading `name` without
    `message` would turn a guest into an account."""
    if not isinstance(p, dict) or "message" not in p:
        return "unknown", ""          # the call failed: nothing is concluded, and it is not called a guest
    if p.get("message") == "success" and p.get("uid"):
        return "user", str(p.get("name") or "")
    return "guest", ""


@dataclass
class LoginState:
    """The login state read from inside the tab."""

    cells: int           # conversations seen; -1 = the call failed
    account_name: str    # passport name (fallback: screen text); "" when unreadable
    modal_visible: bool
    session: str = "unknown"   # what passport says about the session: user / guest / unknown

    @property
    def logged_in(self) -> bool:
        """Usable? A conversation list means that very account; a brand-new account with none still counts when it
        shows a name; a visible login dialog means it surely is not in."""
        if self.modal_visible:
            return False
        if self.cells > 0:
            return True
        return self.cells == 0 and bool(self.account_name)

    @property
    def signed_out(self) -> bool:
        """dola says outright that this session belongs to nobody (measured 23/09 on guest tabs and on tabs carrying a
        dead cookie): no conversation AND passport answers an error. A failed call or a conversation list is a doubt,
        not a conclusion."""
        return self.session == "guest" and self.cells == 0 and not self.account_name

    @property
    def reason(self) -> str:
        if self.modal_visible:
            return "login dialog visible"
        if self.cells < 0:
            return "recent_conv call failed"
        if self.cells > 0:
            return "%d conversations%s" % (self.cells, (" - %s" % self.account_name) if self.account_name else "")
        if self.account_name:
            return "no conversations yet but shows name %s (new account)" % self.account_name
        if self.session == "guest":
            # cookies just injected and dola still says guest: the session died on dola's side (often a login
            # elsewhere); injecting the same jar again cannot help
            return "signed out: dola returns no account for this credential's cookies (export fresh cookies)"
        return "no conversations and could not read account name"


def login_state(probe: dict) -> LoginState:
    """A `LoginState` from what `LOGIN_PROBE_JS` returned."""
    session, name = read_passport(probe.get("passport"))
    return LoginState(cells=int(probe.get("cells", -1)), account_name=name or str(probe.get("name") or ""),
                      modal_visible=bool(probe.get("modal")), session=session)


def lost_identity_cookies(present: Iterable[str] | None, injected: Iterable[str]) -> list[str]:
    """The identity cookies that were injected into this browser and are gone now (sorted), or [] when they are all
    there or the browser could not be asked (`None`). Only the names `inject_cookies` verified count: a jar that never
    held an identity cookie cannot have lost it. A guest read made while one is missing is the browser's fault."""
    if present is None:
        return []
    have = set(present)
    return sorted(name for name in injected if name not in have)


def login_doubt_effects(now: datetime, reason: str) -> tuple[Suspect, Cooldown]:
    """What a login doubt does to an account (a guest read, a login dialog, no conversation and no name): one spaced
    strike, and a rest after which the account is checked again. Only the third counted strike, from separate opens at
    least `LOGIN_DOUBT_GAP_S` apart with no logged-in read between them, ends in `signed_out` (the strike threshold)."""
    return (Suspect("not logged in: %s" % reason, min_gap_s=LOGIN_DOUBT_GAP_S),
            Cooldown("login doubt, to be checked again", now + timedelta(seconds=LOGIN_DOUBT_REST_S), recheck=True))


def cookies_lost_effects(now: datetime) -> tuple[Cooldown]:
    """The browser lost the cookies it was given: no strike, but the same rest and recheck as a login doubt, so an
    account whose session is really gone is not claimed again every minute."""
    return (Cooldown("cookies lost in the browser", now + timedelta(seconds=LOGIN_DOUBT_REST_S), recheck=True),)
