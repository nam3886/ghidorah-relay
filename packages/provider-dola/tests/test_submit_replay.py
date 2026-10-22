"""The submit path on the three recorded dola captures (spike S1: text-to-video, image-to-video with its gate re-send,
image): the receipt, the replies in order, the gate, the acceptance and the image HOLD, read from a REAL `NetRecorder`
fed the recorded responses. The last test replays the captures in Chrome against the kit's local replay server.

Also here: waiting for the reply instead of reading once (BSUB03) and choosing the conversation by the sent text
(BSUB05)."""

import json
import sys
from pathlib import Path

import pytest
from kit_c import (
    ACCEPTED,
    CONV,
    FIXTURES,
    GOOD_PROBE,
    SENT,
    Scene,
    chain_body,
    make_job,
    media_ref,
    sync,
)

from relay_core.providers import Accepted, Balance, Completed, Success
from relay_core.testing.mutation import mutation
from relay_dola.engine import resolver
from relay_dola.engine.submit import _Run, body_carries, is_full_read

SUB = "packages/provider-dola/src/relay_dola/engine/submit.py"
S1 = FIXTURES / "s1-network"
NAMES = ["t2v", "i2v", "image"]


def capture(name: str) -> dict:
    return json.loads((S1 / ("%s.json" % name)).read_text(encoding="utf-8"))


def chain_reads(cap: dict) -> list[dict]:
    return [h for h in cap["http"] if h["path"] == "/im/chain/single" and h["status"] == 200]


def _texts(msg: dict):
    """Every text a message holds, read independently of the engine's own readers."""
    content = msg.get("content")
    content = json.loads(content) if isinstance(content, str) else content
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        yield content["text"]
    if isinstance(content, list):
        for block in content:
            text = ((block.get("content") or {}).get("text_block") or {}).get("text")
            if text:
                yield text


def sent_text_of(cap: dict) -> str:
    """What the engine typed: the text up to and including the marker (dola's bubble adds the chips after it)."""
    marker = cap["marker"]
    for read in chain_reads(cap):
        body = json.loads(read["body"])
        for msg in body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]:
            for text in _texts(msg):
                if marker in text:
                    return text[:text.index(marker) + len(marker)]
    raise AssertionError("the capture holds no message with its marker")


def image_urls_in(body: dict) -> list[str]:
    """The clean image of every creation in the `content` of every message (read by shape, not by the engine's reader;
    a message's other fields repeat the same blocks and must not be counted again)."""
    urls = []
    for msg in body["downlink_body"]["pull_singe_chain_downlink_body"]["messages"]:
        content = msg.get("content")
        blocks = json.loads(content) if isinstance(content, str) and content.startswith("[") else []
        for block in blocks:
            for creation in ((block.get("content") or {}).get("creation_block") or {}).get("creations") or []:
                raw = (creation.get("image") or {}).get("image_ori_raw")
                if isinstance(raw, dict) and raw.get("url"):
                    urls.append(raw["url"])
    return urls


class Replay:
    """One capture as the page lived it. Each send releases the reads that followed it in the capture (by the capture's
    own send times); with `trickle` only the first of them at once and one more per engine sleep, the way a slow dola
    answers. `body()` is the latest FULL read (the page's incremental reads are not what the engine's own fetch asks
    for: it always asks with the largest anchor)."""

    def __init__(self, feeder, cap: dict, *, trickle: bool = False, drop_last: int = 0):
        self.feeder, self.conv, self.trickle = feeder, cap["conversation_id"], trickle
        reads = chain_reads(cap)
        self.all_reads = reads[:len(reads) - drop_last] if drop_last else reads
        self.sends_at = cap["sends"]
        self.sends: list[str] = []
        self.queue: list[dict] = []
        self.latest: dict | None = None
        self.latest_full: dict | None = None
        self.ticks = self.fed = 0
        self.reads = 0                # what FakeDriver counts as the engine's own fetches

    @property
    def thread(self) -> list:
        return [True] if self.latest_full else []

    def body(self) -> dict:
        return json.loads(self.latest_full["body"])

    async def _feed(self, read: dict) -> None:
        self.latest = read
        if is_full_read(read["req"]):
            self.latest_full = read
        self.fed += 1
        await self.feeder.feed(read["path"], read["req"], read["body"])

    async def user_sent(self, text: str) -> None:
        n = len(self.sends)
        self.sends.append(text)
        start = self.sends_at[n]
        end = self.sends_at[n + 1] if n + 1 < len(self.sends_at) else float("inf")
        phase = [r for r in self.all_reads if start <= r["t"] < end]
        if self.trickle:
            self.queue = phase
            await self.tick(counted=False)
        else:
            for read in phase:
                await self._feed(read)

    async def tick(self, counted: bool = True) -> None:
        self.ticks += counted
        if self.queue:
            await self._feed(self.queue.pop(0))


def replay_scene(name: str, *, trickle: bool = False, drop_last: int = 0, new_composer: bool = False, job=None, **kw):
    cap = capture(name)
    scene = Scene(job=job, new_composer=new_composer,
                  world_factory=lambda feeder: Replay(feeder, cap, trickle=trickle, drop_last=drop_last), **kw)
    return cap, scene


# ---- the receipt -----------------------------------------------------------------------------------------------------
async def feed_all(scene, cap) -> None:
    for read in chain_reads(cap):
        await scene.session.feeder.feed(read["path"], read["req"], read["body"])


def raw_run(scene) -> _Run:
    run = _Run(scene.ctx, scene.job, scene.ctx.settings, scene.deps)
    run.session, run.page, run.since = scene.session, scene.session.page, 0.0
    return run


@pytest.mark.parametrize("name", NAMES)
@sync
async def test_the_receipt_is_the_first_chain_read_that_carries_the_whole_sent_text(name):
    cap = capture(name)
    scene = Scene(job=make_job(sent_text=sent_text_of(cap)))
    other = chain_body([("user", "a different order [4100]")], "999")
    await scene.session.feeder.chain(other, "999")          # a read of ANOTHER conversation first: not ours
    await feed_all(scene, cap)
    run = raw_run(scene)
    assert await run._receipt(set()) == cap["conversation_id"], "the conversation id was not taken from the request"


@pytest.mark.parametrize("name", NAMES)
@sync
async def test_the_replies_come_in_the_order_the_page_received_them(name):
    cap = capture(name)
    scene = Scene(job=make_job(sent_text=sent_text_of(cap)))
    run, seen, expected, full = raw_run(scene), [], [], ""
    for read in chain_reads(cap):
        await scene.session.feeder.feed(read["path"], read["req"], read["body"])
        seen.append(await run._reply(cap["conversation_id"]))
        if is_full_read(read["req"]):
            full = resolver.conversation_text(json.loads(read["body"]))
        expected.append(full)        # a partial read of the page changes nothing: only full reads are judged
    assert seen == expected, "a reply was read out of order, or the latest full read was not the newest"
    assert any(seen), "the capture holds no reply at all: the test would prove nothing"


@mutation(SUB, "    return any(resolver.carries_text(t, sent_text, prompt) for t in texts)",
          "    return any(sent_text[:20] in t for t in texts)", expect="whole sent text")
@sync
async def test_a_read_that_carries_only_the_start_of_the_sent_text_is_not_the_receipt():
    prompt = "a paper lantern rising over a quiet lake, slow camera tilt, no people"
    scene = Scene(job=make_job(prompt=prompt))
    sent = scene.job.sent_text
    other = chain_body([("user", sent[:40])], "555")        # the first 40 characters of ours, and then something else
    await scene.session.feeder.chain(other, "555")
    run = raw_run(scene)
    assert await run._receipt(set(GOOD_PROBE["ids"])) is None, (
        "a read with only the start of the sent text, whole sent text"
    )
    assert not body_carries(other, sent, prompt)
    assert body_carries(chain_body([("user", sent + ", 16:9")], "555"), sent, prompt)


# ---- the whole submit on the captures --------------------------------------------------------------------------------
async def run_replay(name, **kw):
    cap = capture(name)
    kind = "image" if name == "image" else "video"
    images = (media_ref(0),) if name == "i2v" else ()

    async def read_media(ref):
        return b"img"
    job = kw.pop("job", None) or make_job(kind, sent_text=sent_text_of(cap), images=images)
    cap, scene = replay_scene(name, job=job, read_media=read_media, **kw)
    return cap, scene, await scene.run()


@sync
async def test_t2v_dola_gates_and_accepts_in_one_reply_so_nothing_is_sent_again():
    cap, scene, res = await run_replay("t2v")
    assert scene.log.count("send") == 1 and scene.ctx.fences == 0, (
        "an order dola took was sent again (a second paid order)"
    )
    assert res.job.ref == cap["conversation_id"] and res.job.taken is True
    assert (res.job.facts.upstream_price, res.job.facts.upstream_model) == (2, "Dreamina Seedance 2.5")
    assert res.account == (Success(), Balance("video", 2, "reported"))
    assert scene.ctx.refs == [(cap["conversation_id"], {})]


@pytest.mark.parametrize("new_composer", [True, False])
@sync
async def test_i2v_the_gate_is_pushed_once_in_the_same_conversation_and_the_new_part_is_the_acceptance(new_composer):
    cap, scene, res = await run_replay("i2v", new_composer=new_composer)
    assert scene.world.sends == [sent_text_of(cap)] * 2, "the gate was pushed with a different text, or not at all"
    assert scene.log[scene.log.index("report_ref"):] == ["report_ref", "rebuild", "attach", "fence", "send"], \
        "the re-send must rebuild the composer, re-attach the references and fence right before sending"
    assert isinstance(res.job, Accepted) and res.job.taken is True and res.job.ref == cap["conversation_id"]
    assert res.job.facts.upstream_model == "Dreamina Seedance 2.5" and res.job.facts.upstream_price is None
    assert res.account == (Success(),), "dola reported no balance in this answer: none may be recorded"


@sync
async def test_image_the_session_holds_until_the_clean_images_arrive_then_completes():
    cap, scene, res = await run_replay("image", trickle=True, hold_s=60)
    wanted = image_urls_in(json.loads(chain_reads(cap)[-1]["body"]))
    assert wanted, "the capture's last read holds no image: the test would prove nothing"
    assert isinstance(res.job, Completed) and [o.ref for o in res.job.outputs] == wanted
    assert scene.world.ticks > 0, "the session did not wait: the images were there at the first read"
    assert res.account == (Success(),) and scene.ctx.evidence_items[0][2] == "image/jpeg"


@sync
async def test_image_without_the_images_before_the_hold_ends_takes_the_receipt_and_leaves_it_to_the_poll():
    cap, scene, res = await run_replay("image", trickle=True, drop_last=1, hold_s=4)
    assert res.job == Accepted(cap["conversation_id"], taken=True), "no images yet: the poll must fetch them"


# ---- the page's partial reads and the customer's own words (the prompt can say anything) ---
def partial_reads(cap: dict) -> list[dict]:
    return [r for r in chain_reads(cap) if not is_full_read(r["req"])]


@pytest.mark.parametrize("name", ["t2v", "i2v", "image"])
@sync
async def test_the_captures_page_reads_hold_the_customers_words_in_partial_reads_and_none_of_them_is_judged(name):
    cap = capture(name)
    partials = partial_reads(cap)
    assert partials, "the capture has no incremental read: the test would prove nothing"
    scene = Scene(job=make_job(sent_text=sent_text_of(cap)))
    run = raw_run(scene)
    for read in partials:
        await scene.session.feeder.feed(read["path"], read["req"], read["body"])
    assert await run._reply(cap["conversation_id"]) == "", "a partial page read was taken as dola's reply"
    assert any(cap["marker"] in json.dumps(json.loads(r["body"])) for r in partials), (
        "no partial read carries the customer's marker: the capture would not show the defect"
    )
    for read in partials:      # and the customer's words are never dola's, whatever read they come in
        assert cap["marker"] not in resolver.conversation_text(json.loads(read["body"]))


FULL_ONLY_RIGHT = ("            if (ev.path == SINGLE_PATH and ev.status == 200 and is_full_read(ev.req)\n"
                   "                    and conversation_id_of(ev.req) == conversation):")
FULL_ONLY_WRONG = ("            if (ev.path == SINGLE_PATH and ev.status == 200\n"
                   "                    and conversation_id_of(ev.req) == conversation):")


@mutation(SUB, FULL_ONLY_RIGHT, FULL_ONLY_WRONG, expect="partial page read")
@sync
async def test_a_partial_page_read_is_never_what_a_reply_is_judged_on():
    scene = Scene()
    run = raw_run(scene)
    body = chain_body([("user_blocks", SENT), ("assistant", "SECRET-PARTIAL")])
    await scene.session.feeder.chain(body, CONV, anchor=5)
    assert await run._reply(CONV) == "", "a partial page read was judged (partial page read)"
    await scene.session.feeder.chain(chain_body([("user_blocks", SENT), ("assistant", "full")]), CONV)
    assert await run._reply(CONV) == "full", "the full read must be the one that counts"
    await scene.session.feeder.chain(body, CONV, anchor=6)         # a later partial read does not replace it
    assert await run._reply(CONV) == "full"


POISON = {
    "out of credit": "Poster text: You've reached today's image limit. You've reached the daily limit for video.",
    "timed script": "Shot list: 0-5 seconds a lantern rises, 5-10 seconds the lake glows, 10-15 seconds fade out.",
    "please confirm": "Please confirm the soft light and would you like to proceed with a calm mood?",
}


def poisoned_capture(name: str, phrase: str) -> dict:
    cap = json.loads((S1 / ("%s.json" % name)).read_text(encoding="utf-8"))
    for h in cap["http"]:
        if isinstance(h.get("body"), str) and cap["marker"] in h["body"]:
            h["body"] = h["body"].replace(cap["marker"], phrase + " " + cap["marker"])
    return cap


def outcome_of(scene, res):
    return (type(res.job).__name__, scene.log.count("send"), scene.ctx.fences, [type(e).__name__ for e in res.account])


@pytest.mark.parametrize("phrase", sorted(POISON))
@pytest.mark.parametrize("name", ["t2v", "i2v", "image"])
@sync
async def test_a_prompt_that_quotes_dolas_refusals_or_gates_changes_nothing(name, phrase, monkeypatch):
    # The customer's prompt rides back in the page's own reads (partial ones: as text blocks). Judged as dola's words
    # it rested every account's pool, or sent the order again before dola answered. Only the prompt differs.
    clean_cap, clean, clean_res = await run_replay(name, trickle=True, hold_s=60)
    monkeypatch.setattr(sys.modules[__name__], "capture", lambda n: poisoned_capture(n, POISON[phrase]))
    cap, scene, res = await run_replay(name, trickle=True, hold_s=60)
    assert outcome_of(scene, res) == outcome_of(clean, clean_res), "the customer's words changed the outcome"
    assert not any(type(e).__name__ == "Rest" for e in res.account), "the pool was rested"
    assert type(res.job).__name__ in ("Accepted", "Completed"), res.job
    assert [t for t, _, src in scene.ctx.events if src == "upstream" and POISON[phrase][:20] in t] == [], (
        "the customer's words were recorded as dola's"
    )


# ---- waiting for the reply instead of reading once (BSUB03) -------------------------------------------------------
async def counted_sleeps(scene):
    count = {"n": 0}
    inner = scene.deps.sleep

    async def sleep(seconds):
        count["n"] += 1
        await inner(seconds)
    scene.deps.sleep = sleep
    return count


@sync
async def test_the_reply_is_waited_for_not_read_once_and_the_wait_stops_when_dola_has_spoken():
    quiet = ("assistant", "")
    scene = Scene(job=make_job("image"), replies={1: []}, late=[quiet, quiet, quiet, "Done. Four images generated."],
                  hold_s=2)
    count = await counted_sleeps(scene)
    run = raw_run(scene)
    await run.session.feeder.chain(chain_body([("user", "x")]), CONV)      # it exists, with no reply yet
    scene.world.thread.append(("user", "x"))
    reply = await run._settled_reply(CONV)
    assert reply.startswith("Done."), "one read right after the conversation showed up is always empty"
    assert count["n"] == 4, "the wait must stop AT the first words, not after: %d sleeps" % count["n"]


@sync
async def test_a_mute_dola_is_given_up_on_after_a_bounded_wait():
    scene = Scene(job=make_job("image"))
    count = await counted_sleeps(scene)
    run = raw_run(scene)
    scene.world.thread.append(("user", "x"))
    assert await run._settled_reply(CONV) == ""
    assert count["n"] == 12, "the wait must be bounded (12 reads): %d" % count["n"]


# ---- the conversation is chosen by the sent text, never by the biggest id (BSUB05) --------------------------------
async def fallback(scene, ids, bodies, url="https://www.dola.com/chat"):
    run = raw_run(scene)
    scene.driver.probe_result = {**GOOD_PROBE, "ids": ids}
    scene.driver.bodies = bodies
    scene.driver.url = url
    return await run._fallback_receipt({"111", "222"})


@sync
async def test_the_fallback_picks_the_conversation_that_carries_our_text_not_the_biggest_id():
    scene = Scene()
    ours = chain_body([("user", SENT)], "100")
    theirs = chain_body([("user", SENT.replace("[7100]", "[4100]"))], "900")
    # ids do not grow with time (14 of 35 consecutive pairs fell): ours is the SMALLER one here
    assert await fallback(scene, ["111", "222", "900", "100"], {"900": theirs, "100": ours}) == "100"
    assert await fallback(scene, ["111", "222", "900"], {"900": theirs}) is None, (
        "a readable foreign conversation was adopted"
    )
    assert await fallback(scene, ["111", "222", "900", "100"], {"900": None, "100": None}) is None, \
        "nothing readable: nothing may be adopted (the order stays unknown for reconcile), not the biggest id"
    assert await fallback(scene, ["111", "222"], {}) is None


@sync
async def test_the_fallback_takes_the_id_from_the_page_url_only_when_it_is_new():
    scene = Scene()
    new_id = "38400000000000999"
    # every candidate would carry our text, so only the "new, numeric" rules can refuse the old and the placeholder
    ours = {i: chain_body([("user", SENT)], i) for i in (new_id, "111", "local_abc123")}
    assert await fallback(scene, ["111", "222"], ours, "https://www.dola.com/chat/" + new_id) == new_id
    assert await fallback(scene, ["111", "222"], ours, "https://www.dola.com/chat/111") is None, (
        "an old conversation was adopted"
    )
    assert await fallback(scene, ["111", "222"], ours, "https://www.dola.com/chat/local_abc123") is None, \
        "an image's placeholder URL is not a conversation id"


@sync
async def test_a_receipt_that_only_the_fallback_can_find_is_reported_as_the_engines_own_finding():
    scene = Scene(job=make_job(sent_text=SENT), polls=False, replies={1: [ACCEPTED]})
    scene.driver.url = "https://www.dola.com/chat/38400000000000001"
    res = await scene.run()
    assert isinstance(res.job, Accepted) and res.job.ref == "38400000000000001"
    assert any("not by the network read" in text and src == "engine" for text, st, src in scene.ctx.events)


# ---- the captures in real Chrome -------------------------------------------------------------------------------------
KIT_TESTS = Path(__file__).resolve().parents[2] / "relay-browser" / "tests"


@pytest.fixture
def token():
    from relay_core.budget import BrowserBudget, Purpose
    t = BrowserBudget(4).try_acquire(Purpose.SUBMIT)
    yield t
    t.release()


@pytest.mark.browser
@pytest.mark.parametrize("name", NAMES)
@sync
async def test_a_real_chrome_replay_gives_the_engine_the_same_receipt(name, tmp_path, token, monkeypatch):
    """The recorded responses are served to a real Chrome, whose `NetRecorder` produces the events the engine reads: the
    request body (the conversation id) and the response body (the sent text) must both come through."""
    import dataclasses

    from relay_browser.session import open_session
    from relay_core.redact import redact_text
    from relay_dola.engine.site import DOLA_SITE
    monkeypatch.syspath_prepend(str(KIT_TESTS))
    monkeypatch.setenv("RELAY_BROWSER_HEADLESS", "1")
    from browserkit import Account, settle
    from fixtures_server import ReplayServer
    cap = capture(name)
    site = dataclasses.replace(DOLA_SITE, host="127.0.0.1", profile_root=str(tmp_path / "profiles"), viewport=None,
                               refuse_hosts=(), telemetry_url_patterns=())
    async with ReplayServer(name) as server:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact_text) as session:
            await session.page.goto(server.url())
            await session.page.wait_for_function("window.__replayDone === true", timeout=30000)
            await settle(session.recorder, len(chain_reads(cap)))
            scene = Scene(job=make_job(sent_text=sent_text_of(cap)))
            scene.session = session                      # the engine reads the REAL recorder
            run = raw_run(scene)
            run.session, run.page = session, session.page
            assert await run._receipt(set()) == cap["conversation_id"]
            assert await run._reply(cap["conversation_id"]) == resolver.conversation_text(
                json.loads(chain_reads(cap)[-1]["body"]))


def test_the_capture_files_are_the_ones_the_kit_replays():
    assert sorted(p.stem for p in S1.glob("*.json")) == sorted(NAMES)
