"""`NetRecorder` (coverage M4 recorder part, R67, E01 marker): it reads what the page receives and
never intercepts. The replay tests give a real Chrome the three recorded dola captures (spike S1) and demand what the
provider needs: the marker and the conversation id from the first matching `/im/chain/single`, and the replies in
order. The pure tests drive the recorder with stand-in page objects."""

import asyncio
import re

import pytest
from browserkit import (
    PLANTED_SECRET,
    Account,
    big_redact,
    cookie_text,
    json_text,
    loop_gap_during,
    redact,
    settle,
    site_for,
    sync,
)
from fixtures_server import ReplayServer, SiteServer

from relay_browser.net import REDACT_MARGIN_CHARS, NetRecorder
from relay_browser.session import open_session
from relay_core.testing.mutation import mutation

N = "packages/relay-browser/src/relay_browser/net.py"
SINGLE = "/im/chain/single"


# ---- the recorded captures in a real Chrome --------------------------------------------------------------------------
async def replay(name: str, tmp_path, token, **site_fields):
    """Run the capture in Chrome; returns (server, events of the recorder)."""
    site = site_for(tmp_path, capture_paths=(r"^/im/",), capture_ws_hosts=("127.0.0.1",), **site_fields)
    async with ReplayServer(name) as server:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.wait_for_function("window.__replayDone === true", timeout=30000)
            await settle(s.recorder, len(server.steps) + len(server.ws_frames))
            return server, s.recorder.events(), s.recorder


@pytest.mark.browser
@mutation(N, "if pattern.search(e.path) and (body_contains is None or body_contains in e.body):",
          "if (body_contains is None or body_contains in e.body):", expect="not a chain read")
@mutation(N, "if pattern.search(e.path) and (body_contains is None or body_contains in e.body):",
          "if pattern.search(e.path):", expect="not the marker's chain read")
@pytest.mark.parametrize("name", ["t2v", "i2v", "image"])
@sync
async def test_the_recorder_finds_the_marker_and_the_conversation_id_in_the_first_matching_chain_read(
        name, tmp_path, token):
    server, events, recorder = await replay(name, tmp_path, token)
    capture = server.capture
    first = recorder.first(SINGLE + "$", body_contains=capture["marker"])
    assert first is not None and first.path == SINGLE, "not a chain read"
    assert capture["marker"] in first.body, "not the marker's chain read"
    assert re.search(r'"conversation_id":"(\d+)"', first.body).group(1) == capture["conversation_id"]
    expected = next(s for s in server.steps if s["path"] == SINGLE and capture["marker"] in s["body"])
    assert first.body == expected["body"], "not the first chain read that holds the marker"
    assert first.req == expected["req"], "the request body was not captured"
    assert recorder.first(SINGLE + "$", body_contains="no-such-marker-0000") is None, \
        "not the marker's chain read: a body that holds no marker was returned"


@pytest.mark.browser
@pytest.mark.parametrize("name", ["t2v", "i2v", "image"])
@sync
async def test_the_replies_come_in_the_order_the_page_received_them(name, tmp_path, token):
    server, events, _ = await replay(name, tmp_path, token)
    http = [e for e in events if e.kind == "http"]
    assert [e.path for e in http] == [s["path"] for s in server.steps]
    assert [e.body for e in http if e.path == SINGLE] == [s["body"] for s in server.steps if s["path"] == SINGLE]
    assert all(e.status == 200 and e.method == "POST" for e in http)
    assert [e.seq for e in events] == sorted(e.seq for e in events)


@pytest.mark.browser
@mutation(N, 'text = bytes(payload).decode("latin-1") if binary else str(payload)',
          'text = bytes(payload).decode("utf-8", "replace") if binary else str(payload)', expect="binary frame")
@pytest.mark.parametrize("name", ["t2v", "i2v", "image"])
@sync
async def test_websocket_frames_are_captured_with_their_bytes(name, tmp_path, token):
    server, events, _ = await replay(name, tmp_path, token)
    frames = [(e.binary, e.body) for e in events if e.kind == "ws"]
    assert frames == [(w["binary"], w["text"]) for w in server.capture["ws"]], "a binary frame was changed or lost"


@pytest.mark.browser
@mutation(N, "if not any(p.search(path) for p in self._paths):", "if False:", expect="undeclared path")
@mutation(N, 'if (parts.hostname or "").lower() not in self._ws_hosts:', "if False:",
          expect="undeclared WebSocket host")
@sync
async def test_only_declared_paths_and_websocket_hosts_are_captured(tmp_path, token):
    site = site_for(tmp_path, capture_paths=(r"^/nothing",), capture_ws_hosts=("other.test",))
    async with ReplayServer("t2v") as server:
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.wait_for_function("window.__replayDone === true", timeout=30000)
            await asyncio.sleep(0.3)
            assert s.recorder.events() == [], "an undeclared path or an undeclared WebSocket host was captured"


@pytest.mark.browser
@sync
async def test_a_streamed_reply_is_captured_for_its_error_code(tmp_path, token):
    """dola's /chat/completion carries no conversation id and no reply, but its SSE body holds the throttle or region
    `error_code` (710022002 high demand, 710022003 region, 710022004 rate limit)."""
    sse = 'data: {"event_type": 2005, "event_data": "{\\"error_code\\": 710022004}"}\n\ndata: [DONE]\n\n'
    site = site_for(tmp_path, capture_paths=(r"^/chat/completion$",))
    async with SiteServer() as server:
        server.add("GET", "/", "<title>app</title>")
        server.add("POST", "/chat/completion", sse, "text/event-stream")
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.page.evaluate("fetch('/chat/completion', {method: 'POST', body: '{}'}).then(r => r.text())")
            ev = await s.recorder.wait_for(lambda e: "710022004" in e.body, 10)
    assert ev is not None and ev.path == "/chat/completion" and "710022004" in ev.body, \
        "the SSE error code was not captured"


# ---- stand-in page objects -------------------------------------------------------------------------------------------
class FakeRequest:
    def __init__(self, method="POST", post_data=""):
        self.method, self.post_data = method, post_data


class FakeResponse:
    def __init__(self, url, body="", status=200, post_data="", gate=None, error=None):
        self.url, self.status, self.request = url, status, FakeRequest(post_data=post_data)
        self._body, self._gate, self._error = body, gate, error

    async def text(self):
        if self._gate is not None:
            await self._gate.wait()
        if self._error:
            raise RuntimeError(self._error)
        return self._body


class FakeWs:
    def __init__(self, url):
        self.url, self.handlers = url, {}

    def on(self, event, handler):
        self.handlers[event] = handler


class FakePage:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


def attached(**kw):
    page, recorder = FakePage(), NetRecorder(**kw)
    recorder.attach(page)
    return page, recorder


@sync
async def test_a_body_read_late_does_not_jump_the_queue():
    page, recorder = attached(body_paths=[r"^/a"])
    gate = asyncio.Event()
    page.handlers["response"](FakeResponse("http://x.test/a/slow", "first", gate=gate))
    page.handlers["response"](FakeResponse("http://x.test/a/fast", "second"))
    await asyncio.sleep(0.05)
    assert [e.body for e in recorder.events()] == ["second"]          # the first is still being read
    gate.set()
    await asyncio.sleep(0.05)
    assert [e.body for e in recorder.events()] == ["first", "second"], "events are not in the order received"


@sync
async def test_a_body_that_cannot_be_read_is_an_event_with_the_reason():
    page, recorder = attached(body_paths=[r"^/a"])
    page.handlers["response"](FakeResponse("http://x.test/a", error="No data found for resource\nmore"))
    await asyncio.sleep(0.05)
    [ev] = recorder.events()
    assert ev.body == "" and ev.error == "No data found for resource" and ev.ready
    assert "read-failed: No data found for resource" in recorder.dump(redact)


@sync
async def test_wait_for_returns_an_event_that_arrives_later_and_none_on_timeout():
    page, recorder = attached(body_paths=[r"^/a"])
    waiting = asyncio.ensure_future(recorder.wait_for(lambda e: "ready" in e.body, timeout=5))
    await asyncio.sleep(0.05)
    assert not waiting.done()
    page.handlers["response"](FakeResponse("http://x.test/a", "not yet"))
    page.handlers["response"](FakeResponse("http://x.test/a", "ready now"))
    got = await waiting
    assert got is not None and got.body == "ready now"
    assert await recorder.wait_for(lambda e: "never" in e.body, timeout=0.2) is None, "a timeout is not None"
    assert (await recorder.wait_for(lambda e: True, timeout=0.2)).body == "not yet"      # already there: no wait


@sync
async def test_events_since_a_moment_leave_out_what_came_before():
    page, recorder = attached(body_paths=[r"^/a"])
    page.handlers["response"](FakeResponse("http://x.test/a", "before"))
    await asyncio.sleep(0.05)
    mark = recorder.now()
    page.handlers["response"](FakeResponse("http://x.test/a", "after"))
    await asyncio.sleep(0.05)
    assert [e.body for e in recorder.events(since=mark)] == ["after"]
    assert recorder.first("/a", since=mark).body == "after"
    assert [e.body for e in recorder.events()] == ["before", "after"]


@mutation(N, "        for task in list(self._tasks):\n            task.cancel()\n", "        pass\n",
          expect="did not stop a read")
@sync
async def test_close_stops_reads_that_are_still_in_flight():
    page, recorder = attached(body_paths=[r"^/a"])
    gate = asyncio.Event()
    page.handlers["response"](FakeResponse("http://x.test/a", "never", gate=gate))
    await asyncio.sleep(0.05)
    (read,) = recorder._tasks
    assert not read.done(), "premise: the read is waiting for its body"
    recorder.close()
    await asyncio.sleep(0.05)
    assert read.cancelled(), "close did not stop a read that is still in flight"
    assert not recorder._tasks, "a stopped read is still held"
    [ev] = recorder.events()
    assert ev.error == "cancelled" and ev.body == "", "a stopped read looks like a real empty reply: %r" % ev


# ---- redaction -------------------------------------------------------------------------------------------------------
@mutation(N, 'out.append(redact("\\n".join(lines)))', 'out.append("\\n".join(lines))', expect="planted secret")
@sync
async def test_a_dump_holds_no_planted_secret_in_a_request_a_body_or_a_frame():
    page, recorder = attached(body_paths=[r"^/a"], ws_hosts=["x.test"])
    page.handlers["response"](FakeResponse(
        "http://x.test/a", '{"note": "Set-Cookie: sessionid=%s; Path=/", "ok": 1}' % PLANTED_SECRET,
        post_data="Cookie: sessionid=%s; theme=dark" % PLANTED_SECRET))
    ws = FakeWs("ws://x.test/gateway")
    page.handlers["websocket"](ws)
    ws.handlers["framereceived"]("Authorization: Bearer %s" % PLANTED_SECRET)
    ws.handlers["framereceived"](b"\x00sessionid=%s\x00" % PLANTED_SECRET.encode())
    page.handlers["response"](FakeResponse("http://x.test/a/plain", '{"visible": "kept"}'))
    page.handlers["response"](FakeResponse("http://x.test/a/q?sessionid=%s" % PLANTED_SECRET, "in the url"))
    await asyncio.sleep(0.05)
    text = recorder.dump(redact)
    assert PLANTED_SECRET not in text, "the dump holds a planted secret (in a request, a body, a frame or a url)"
    assert "http://x.test/a/plain" in text and '{"visible": "kept"}' in text and "ws://x.test/gateway" in text, \
        "the dump lost what is not secret"


SECRET32 = "c0ffee" * 5 + "ab"      # 32 characters: under the long-token rule, found only as a whole cookie object
COOKIE_TAIL = '{"domain": ".site.test", "name": "sessionid", "value": "%s"}' % SECRET32


@mutation(N, "    wide = redact(text[:limit + REDACT_MARGIN_CHARS])\n", "    wide = redact(text[:limit])\n",
          expect="straddling the cut")
@sync
async def test_a_cookie_object_cut_by_the_dump_limit_is_redacted_before_the_cut():
    filler = "x" * (20_000 - len(COOKIE_TAIL) + 2)           # the limit falls just before the closing quote
    straddling = filler + COOKIE_TAIL
    assert SECRET32 in straddling[:20_000] and SECRET32 not in redact(straddling), "premise"
    page, recorder = attached(body_paths=[r"^/a"])
    page.handlers["response"](FakeResponse("http://x.test/a", straddling, post_data=straddling))
    await asyncio.sleep(0.05)
    text = recorder.dump(redact)
    assert "x.test/a" in text and SECRET32 not in text, "a secret straddling the cut is in the dump"


@mutation(N, "    return await asyncio.to_thread(_render, events, dropped, redact)",
          "    return _render(events, dropped, redact)", expect="loop stalled")
@sync
async def test_a_dump_of_a_full_recorder_does_not_stall_the_event_loop():
    page, recorder = attached(body_paths=[r"^/a"])
    for n in range(600):                       # 15 000 characters each: all inside the dump's cut, 7.5 M held
        page.handlers["response"](FakeResponse("http://x.test/a/%d" % n, json_text(15_000, n)))
    await settle(recorder, 500, timeout=30)
    await asyncio.sleep(0.5)
    assert len(recorder.events()) == 500
    text, gap = await loop_gap_during(recorder.dump_async(redact))
    assert text.count("\n") >= 900 and "x.test/a/599" in text, "premise: the dump holds the newest events"
    assert gap < 1.5, "the event loop stalled for %.2f s while the network log was redacted" % gap


@mutation(N, "    wide = redact(text[:limit + REDACT_MARGIN_CHARS])\n", "    wide = redact(text)\n",
          expect="redactor was handed")
@mutation(N, "MAX_TOTAL_CHARS = 8_000_000", "MAX_TOTAL_CHARS = 40_000_000", expect="over the recorder's bound")
@mutation(N, "MAX_EVENTS = 500 ", "MAX_EVENTS = 5000 ", expect="over the recorder's bound")
@sync
async def test_the_default_bounds_hold_and_the_redactor_never_gets_more_than_a_printed_field_and_a_margin():
    page, recorder = attached(body_paths=[r"^/a"])
    for n in range(600):
        page.handlers["response"](FakeResponse("http://x.test/a/%d" % n, "small %d" % n))
    await asyncio.sleep(0.3)
    assert len(recorder.events()) == 500, "over the recorder's bound: %d events held" % len(recorder.events())
    for n in range(6):                                  # 11.4 M characters offered, whole 2 M bodies
        page.handlers["response"](FakeResponse("http://x.test/a/big%d" % n, json_text(1_900_000, n),
                                               post_data=json_text(1_900_000, n + 9)))
    await settle(recorder, 2)
    await asyncio.sleep(0.5)
    held = sum(len(e.body) + len(e.req) for e in recorder.events())
    assert held <= 8_000_000, "over the recorder's bound: %d characters held" % held
    handed = []
    recorder.dump(lambda t: (handed.append(len(t)), t)[1])
    assert handed and max(handed) <= 2 * (20_000 + REDACT_MARGIN_CHARS), \
        "the redactor was handed %d characters in one call: a long call holds the loop" % max(handed)


@mutation(N, "REDACT_MARGIN_CHARS = 16384", "REDACT_MARGIN_CHARS = 4096", expect="a cookie object longer than")
@sync
async def test_a_cookie_object_that_starts_before_the_cut_and_runs_far_past_it_is_redacted_whole():
    page, recorder = attached(body_paths=[r"^/a"])
    for n, (value_chars, redactor) in enumerate([(3_900, redact), (12_000, big_redact)]):
        text, first_word = cookie_text(value_chars)
        page.handlers["response"](FakeResponse("http://x.test/a/%d" % n, text, post_data=text))
        await asyncio.sleep(0.05)
        dump = recorder.dump(redactor, since=recorder.events()[-1].t)
        assert "x.test/a/%d" % n in dump and first_word not in dump, \
            "a cookie object longer than the margin was cut before it was redacted: its value is in the dump (%d)" \
            % value_chars


# ---- bounds ----------------------------------------------------------------------------------------------------------
@mutation(N, "                if e.seq in checked:\n                    continue\n", "", expect="predicate ran again")
@sync
async def test_wait_for_tests_each_event_once_however_long_it_waits():
    page, recorder = attached(body_paths=[r"^/a"])
    calls = []

    def predicate(e):
        calls.append(e.seq)
        return e.body == "never until the end"
    waiting = asyncio.ensure_future(recorder.wait_for(predicate, timeout=10))
    for n in range(30):
        page.handlers["response"](FakeResponse("http://x.test/a/%d" % n, "b%d" % n))
        await asyncio.sleep(0.02)
    assert len(calls) == 30, "the predicate ran again on events it had checked: %d calls for 30 events" % len(calls)
    page.handlers["response"](FakeResponse("http://x.test/a/end", "never until the end"))
    hit = await waiting
    assert hit is not None and len(calls) == 31


@mutation(N, "            self._total -= len(gone.req) + len(gone.body)\n", "            pass\n",
          expect="running total")
@mutation(N, "            if ev.held:           # the body just read counts now",
          "            if True:              # x", expect="running total")
@mutation(N, "        self._total += len(ev.req) + len(ev.body)\n        self._trim()", "        self._trim()",
          expect="running total")
@sync
async def test_the_recorders_running_total_matches_what_it_holds_after_drops_and_late_reads():
    page, recorder = attached(body_paths=[r"^/a"], ws_hosts=["x.test"], max_events=4, max_total_chars=1000)
    gate = asyncio.Event()
    page.handlers["response"](FakeResponse("http://x.test/a/slow", "s" * 30, gate=gate))
    for n in range(3):
        page.handlers["response"](FakeResponse("http://x.test/a/%d" % n, "x" * 10))
    await asyncio.sleep(0.1)                        # the three bodies are read and counted
    ws = FakeWs("ws://x.test/gateway")
    page.handlers["websocket"](ws)
    for _ in range(2):                              # frames count when they arrive; two more events push two out
        ws.handlers["framereceived"]("f" * 5)
    gate.set()                                      # the pushed-out event's body is read late and must not count
    await asyncio.sleep(0.1)
    held = sum(len(e.req) + len(e.body) for e in recorder._events)
    assert recorder._total == held == 30, "the running total drifted: %d, holds %d" % (recorder._total, held)
    assert len(recorder._events) == 4 and recorder.dropped == 2


@mutation(N, "while self._events and (len(self._events) > self._max_events or self._total > self._max_total):",
          "while self._events and (self._total > self._max_total):", expect="more events than the bound")
@mutation(N, "while self._events and (len(self._events) > self._max_events or self._total > self._max_total):",
          "while self._events and (len(self._events) > self._max_events):", expect="more text than the bound")
@mutation(N, "            self.dropped += 1\n", "            pass\n", expect="drop is not counted")
@mutation(N, "    if dropped:\n", "    if False:\n", expect="not shown in the dump")
@sync
async def test_the_recorder_keeps_a_bounded_number_of_events_and_bytes_and_counts_what_it_dropped():
    page, recorder = attached(body_paths=[r"^/a"], max_events=3, max_total_chars=100)
    for n in range(5):
        page.handlers["response"](FakeResponse("http://x.test/a/%d" % n, "b%d" % n))
    await asyncio.sleep(0.1)
    assert [e.body for e in recorder.events()] == ["b2", "b3", "b4"], "more events than the bound, or the newest went"
    assert recorder.dropped == 2, "a drop is not counted"
    assert [e.seq for e in recorder.events()] == [2, 3, 4], "a sequence number was reused"
    assert "2 earlier events were dropped" in recorder.dump(redact), "the drop is not shown in the dump"
    for n in range(3):                                         # 40 characters each: only two fit in 100
        page.handlers["response"](FakeResponse("http://x.test/a/big%d" % n, "%d" % n * 40))
    await asyncio.sleep(0.1)
    held = recorder.events()
    assert sum(len(e.body) for e in held) <= 100, "more text than the bound: %d characters" % sum(
        len(e.body) for e in held)
    assert held[-1].body == "2" * 40, "the newest event was dropped instead of the oldest"
    waiting = asyncio.ensure_future(recorder.wait_for(lambda e: e.body == "late", timeout=5))
    await asyncio.sleep(0.05)
    page.handlers["response"](FakeResponse("http://x.test/a/late", "late"))
    seen = await waiting
    assert seen is not None and seen.body == "late", "a new event is not seen after the bound was reached"
    assert recorder.first("/a/late").body == "late"


@mutation(N, "                except Exception as exc:      # noqa: BLE001 - a predicate bug",
          "                except ZeroDivisionError as exc:      # noqa: BLE001 - a predicate bug",
          expect="predicate error reached the caller")
@sync
async def test_a_predicate_that_raises_is_logged_and_counts_as_no_match(caplog):
    page, recorder = attached(body_paths=[r"^/a"])
    page.handlers["response"](FakeResponse("http://x.test/a/1", "not json"))
    page.handlers["response"](FakeResponse("http://x.test/a/2", '{"ok": true}'))
    await asyncio.sleep(0.05)
    try:
        got = await recorder.wait_for(lambda e: e.json()["ok"], timeout=1)
    except ValueError:
        raise AssertionError("a predicate error reached the caller") from None
    assert got is not None and got.path == "/a/2", "the event after the broken one was not tested"
    assert "wait_for predicate raised" in caplog.text and "/a/1" in caplog.text, "the predicate error was not logged"
    assert await recorder.wait_for(lambda e: e.json()["ok"] and False, timeout=0.2) is None
