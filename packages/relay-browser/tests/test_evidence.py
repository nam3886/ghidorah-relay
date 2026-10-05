"""Evidence (contracts §6.4, §13; coverage R98, M19): the after-send JPEG, and on a UI error the PNG, the HTML (as
text, at most 2 M characters) and the network log, with no planted secret in any text part."""

import threading

import pytest
from browserkit import PLANTED_SECRET, Account, big_redact, cookie_text, json_text, redact, site_for, sync
from fixtures_server import SiteServer

from relay_browser.evidence import EvidenceWriter
from relay_browser.net import REDACT_MARGIN_CHARS, NetRecorder
from relay_browser.session import open_session
from relay_core.testing.mutation import mutation

E = "packages/relay-browser/src/relay_browser/evidence.py"
NETF = "packages/relay-browser/src/relay_browser/net.py"
WIDE = "    wide = redact(text[:limit + REDACT_MARGIN_CHARS])\n"
HTML_CUT = 'html = await asyncio.to_thread(redact_cut, await page.content() or "", self._html_max, self._redact)'
LEAK = "<p>Cookie: sessionid=%s</p>\n<p>visible text</p>" % PLANTED_SECRET   # a Cookie line is redacted to its end


class FakePage:
    def __init__(self, html=LEAK, fail=()):
        self.html, self.fail, self.shots = html, set(fail), []

    async def screenshot(self, **kw):
        self.shots.append(kw)
        if "screenshot" in self.fail:
            raise RuntimeError("page closed")
        return b"\xff\xd8JPEG" if kw.get("type") == "jpeg" else b"\x89PNG"

    async def content(self):
        if "content" in self.fail:
            raise RuntimeError("page closed")
        return self.html


class FakeResponse:
    url, status = "http://x.test/a", 200

    class request:       # noqa: N801 - a stand-in
        method, post_data = "POST", "sessionid=%s" % PLANTED_SECRET

    async def text(self):
        return '{"sessionid": "%s"}' % PLANTED_SECRET


async def recorder_with_a_secret() -> NetRecorder:
    import asyncio

    class Page:
        def on(self, event, handler):
            self.handler = handler if event == "response" else getattr(self, "handler", None)
    page, recorder = Page(), NetRecorder(body_paths=[r"^/a"])
    recorder.attach(page)
    page.handler(FakeResponse())
    await asyncio.sleep(0.05)
    return recorder


@mutation(E, "quality=60", "quality=100", expect="q60")
@sync
async def test_the_after_send_picture_is_a_q60_jpeg():
    page = FakePage()
    ev = await EvidenceWriter(redact).after_send(page)
    assert ev.mime == "image/jpeg" and ev.name.endswith(".jpg") and ev.data.startswith(b"\xff\xd8")
    assert page.shots == [{"type": "jpeg", "quality": 60, "full_page": False}], "the picture is not a q60 JPEG"


@mutation(E, HTML_CUT,
          'html = (await page.content() or "")[:self._html_max]',
          expect="planted secret in the html")
@mutation(E, "await self._recorder.dump_async(self._redact)", "await self._recorder.dump_async(lambda t: t)",
          expect="planted secret in the network log")
@mutation(E, 'TEXT_MIME = "text/plain; charset=utf-8"', 'TEXT_MIME = "text/html; charset=utf-8"',
          expect="must be served as text")
@sync
async def test_a_ui_error_gives_a_png_the_html_and_the_network_log_with_no_planted_secret():
    recorder = await recorder_with_a_secret()
    items = await EvidenceWriter(redact, recorder).ui_error(FakePage(), "no receipt!")
    kinds = sorted(e.name.rsplit(".", 2)[-2] if e.name.endswith(".txt") else "png" for e in items)
    assert kinds == ["html", "network", "png"], kinds
    assert all("no_receipt" in e.name for e in items), "the tag is not in the name"
    for e in items:
        if e.name.endswith(".png"):
            assert e.mime == "image/png" and e.data == b"\x89PNG"
            continue
        assert e.mime.startswith("text/plain"), "the page's HTML and the log must be served as text"
        where = "html" if ".html" in e.name else "network log"
        assert PLANTED_SECRET not in e.data.decode("utf-8"), "planted secret in the " + where
    html = next(e for e in items if ".html" in e.name).data.decode()
    assert "visible text" in html, "redaction ate what is not secret"
    assert "http://x.test/a" in next(e for e in items if "network" in e.name).data.decode()


@mutation(E, HTML_CUT,
          'html = await asyncio.to_thread(self._redact, await page.content() or "")', expect="html over the cap")
@sync
async def test_the_html_is_cut_to_the_cap():
    items = await EvidenceWriter(redact, html_max_chars=100).ui_error(FakePage(html="word " * 1000), "t")
    html = next(e for e in items if ".html" in e.name).data.decode()
    assert len(html) <= 100, "html over the cap: %d characters" % len(html)


@mutation(NETF, WIDE, "    wide = redact(text[:limit])\n", expect="straddling the cap")
@sync
async def test_a_cookie_object_cut_by_the_cap_is_redacted_before_the_cut():
    secret = "c0ffee" * 5 + "ab"                                    # 32 characters: under the long-token rule
    tail = '<script>var c=[{"name":"sessionid","value":"%s"}]</script>' % secret
    html = "z" * (200 - len(tail) + 12) + tail                       # the cap falls just before the closing quote
    assert secret in html[:200] and '"' not in html[200 - 1:200], "premise: the cap cuts the object before its quote"
    assert secret not in redact(html), "premise: the redactor knows this object whole"
    items = await EvidenceWriter(redact, html_max_chars=200).ui_error(FakePage(html=html), "t")
    text = next(e for e in items if ".html" in e.name).data.decode()
    assert secret not in text, "a secret straddling the cap is in the evidence"
    assert len(text) <= 200


@mutation(E, HTML_CUT, 'html = redact_cut(await page.content() or "", self._html_max, self._redact)',
          expect="on the event loop's thread")
@mutation(NETF, WIDE, "    wide = redact(text)\n", expect="redactor was handed")
@sync
async def test_the_html_is_redacted_off_the_event_loop_and_in_bounded_pieces():
    seen = []

    def spy(text):
        seen.append((threading.current_thread() is threading.main_thread(), len(text)))
        return redact(text)
    await EvidenceWriter(spy, html_max_chars=100_000).ui_error(FakePage(html=json_text(2_000_000)), "t")
    assert seen and not any(main for main, _ in seen), "the redactor ran on the event loop's thread"
    assert max(n for _, n in seen) <= 100_000 + REDACT_MARGIN_CHARS, \
        "the redactor was handed %d characters of a page of 2 M" % max(n for _, n in seen)


@mutation(E, "            except Exception as exc:      # noqa: BLE001 - a failing redactor",
          "            except ZeroDivisionError as exc:      # noqa: BLE001 - a failing redactor",
          expect="the network log failed the job")
@sync
async def test_a_network_log_that_cannot_be_redacted_does_not_replace_the_ui_error(caplog):
    def broken(text):
        if "x.test/a" in text:
            raise ValueError("redactor bug")
        return redact(text)
    try:
        items = await EvidenceWriter(broken, await recorder_with_a_secret()).ui_error(FakePage(), "t")
    except ValueError:
        raise AssertionError("the network log failed the job") from None
    assert sorted(e.name.rsplit(".", 1)[-1] for e in items) == ["png", "txt"], "the png and html were lost"
    assert not any("network" in e.name for e in items)
    assert "network log failed" in caplog.text


@mutation("packages/relay-browser/src/relay_browser/net.py", "REDACT_MARGIN_CHARS = 16384",
          "REDACT_MARGIN_CHARS = 4096", expect="a cookie object longer than")
@sync
async def test_html_with_a_cookie_object_that_starts_before_the_cap_and_runs_far_past_it_is_redacted_whole():
    for value_chars, redactor in ((3_900, redact), (12_000, big_redact)):
        text, first_word = cookie_text(value_chars)
        items = await EvidenceWriter(redactor, html_max_chars=20_000).ui_error(FakePage(html=text), "t")
        html = next(e for e in items if ".html" in e.name).data.decode()
        assert len(html) > 19_000 and first_word not in html, \
            "a cookie object longer than the margin was cut before it was redacted: its value is in the HTML (%d)" \
            % value_chars


@sync
async def test_a_page_that_cannot_be_captured_does_not_fail_the_job():
    broken = FakePage(fail={"screenshot", "content"})
    assert await EvidenceWriter(redact).after_send(broken) is None
    assert await EvidenceWriter(redact).ui_error(broken, "t") == []
    only_log = await EvidenceWriter(redact, await recorder_with_a_secret()).ui_error(broken, "t")
    assert [e.name.endswith(".network.txt") for e in only_log] == [True]
    half = await EvidenceWriter(redact).ui_error(FakePage(fail={"content"}), "t")
    assert [e.name.endswith(".png") for e in half] == [True]


@pytest.mark.browser
@sync
async def test_a_real_page_gives_a_real_jpeg_and_png_and_a_redacted_html(tmp_path, token):
    site = site_for(tmp_path, capture_paths=(r"^/api",))
    async with SiteServer() as server:
        server.add("GET", "/", "<title>app</title>" + LEAK + "<script>fetch('/api')</script>")
        server.add("GET", "/api", lambda r: (200, {}, '{"sessionid": "%s"}' % PLANTED_SECRET), "application/json")
        async with open_session(site, Account(), purpose="submit", token=token, redact=redact) as s:
            await s.page.goto(server.url())
            await s.recorder.wait_for(lambda e: e.path == "/api", 10)
            after = await s.evidence.after_send(s.page)
            items = await s.evidence.ui_error(s.page, "ui error")
    assert after.data[:3] == b"\xff\xd8\xff", "not a JPEG"
    assert next(e for e in items if e.name.endswith(".png")).data[:4] == b"\x89PNG", "not a PNG"
    for e in items:
        if not e.name.endswith(".png"):
            assert PLANTED_SECRET not in e.data.decode(), "planted secret in %s" % e.name
    assert "visible text" in next(e for e in items if ".html" in e.name).data.decode()
    assert "/api" in next(e for e in items if "network" in e.name).data.decode()


# ---- redaction shrinks the text: the slice's far end must never reach the printed part -----------------------------
def shrinking_text(straddle_at: int) -> tuple[str, str]:
    """Thirty long tokens (each redacted to a short marker, so the text shrinks by ~30 000 chars), then a cookie
    object whose value straddles `straddle_at`, the end of the slice the redactor sees."""
    tokens = " ".join("T%03d" % n + "k" * 996 for n in range(30))
    secret = "s3cr3tvalue%s" % ("q" * 20)
    obj = '{"domain": ".s.test", "name": "sessionid", "value": "%s"}' % secret
    filler = straddle_at - len(tokens) - 1 - (len(obj) - 3)
    text = tokens + " " + ("f " * (filler // 2)) + ("f" if filler % 2 else "") + obj + " after" * 50
    at = text.find(secret)
    assert at < straddle_at < at + len(secret) + 2, "premise: the value straddles the slice"
    return text, secret


@mutation("packages/relay-browser/src/relay_browser/net.py", "    return os.path.commonprefix([wide, near])[:limit]\n",
          "    return wide[:limit]\n",
          expect="a secret cut at the far end of the slice")
def test_a_secret_cut_at_the_far_end_of_the_slice_never_reaches_the_dump_or_the_html_when_redaction_shrinks(tmp_path):
    from relay_browser.net import DUMP_EVENT_CHARS, REDACT_MARGIN_CHARS, redact_cut

    text, secret = shrinking_text(DUMP_EVENT_CHARS + REDACT_MARGIN_CHARS)
    assert secret not in redact(text), "premise: the redactor hides it in the whole text"
    out = redact_cut(text, DUMP_EVENT_CHARS, redact)
    assert "s3cr3tvalue" not in out, "a secret cut at the far end of the slice was printed: %r" % out[-300:]
    assert len(out) > 0 and "T000" not in out, out[:80]

    cap = 100_000
    page_text, _ = shrinking_text(cap + REDACT_MARGIN_CHARS)
    html = redact_cut(page_text, cap, redact)
    assert "s3cr3tvalue" not in html, "a secret cut at the far end of the slice reached the HTML: %r" % html[-300:]

