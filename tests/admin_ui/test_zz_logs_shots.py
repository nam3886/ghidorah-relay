"""Screenshots of the Request logs page and the request detail (a throwaway of the Logs page round, never committed)."""

import json
import os
import re
import time

import pytest
from accountkit import done_job
from jobkit import key as new_key
from patchright.sync_api import expect
from test_e2e_keys_logs_alerts import open_tab, request_buttons

pytestmark = pytest.mark.browser

SHOTS = os.environ.get("RELAY_SCREENSHOTS_DIR", "/shots")
KEYS = ["Studio Saigon", "Lotus Media", "Hanoi Reels bot", "Internal QA", "Partner trial"]
ROWS = [
    (5, 201, "POST /v1/videos", "Lantern festival on the Thu Bon river", "dola", 0, None, 412),
    (7, 200, "GET /v1/jobs/9c41e7b2", "Lantern festival on the Thu Bon river", "dola", 0, None, 38),
    (14, 402, "POST /v1/videos", None, "vibes", 4, "wallet.empty", 21),
    (28, 201, "POST /v1/images", "Watercolor lotus pond, morning mist", "gemini", 3, None, 388),
    (32, 429, "POST /v1/videos", None, "dola", 1, "key.cap_reached", 19),
    (50, 503, "POST /v1/images", None, "qwen", 2, "provider.no_account", 24),
    (63, 200, "GET /v1/jobs/e2d7f019", "Ao dai walk through the Hue citadel gate", "dola", 0, "dola.content_refused", 41),
    (81, 401, "POST /v1/videos", None, "", None, "auth.invalid_key", 6),
    (99, 201, "POST /v1/videos", "Bowl of pho, steam rising, overhead", "flow", 0, None, 506),
    (120, 500, "GET /v1/jobs/8a51f0c7", "Cyclo ride through the old quarter", "dola", 3, "internal.error", 1840),
    (134, 201, "POST /v1/videos", "Talking head, studio backdrop", "meta", 1, None, 377),
    (153, 400, "POST /v1/videos", None, "flow", 0, "request.duration_out_of_range", 12),
    (161, 200, "GET /v1/models", None, "", 3, None, 9),
    (190, 201, "POST /v1/images", "Matte black earbuds on slate", "vibes", 0, None, 295),
    (205, 200, "GET /v1/jobs/3a90cc5e", "Bowl of pho, steam rising, overhead", "flow", 0, None, 41),
    (222, 201, "POST /v1/videos", "Cyclo ride through the old quarter", "dola", 2, None, 431),
]
PROMPT = {"model": "vibes-video", "prompt": "Neon alley in the rain, handheld, reflections on wet asphalt, a cyclist passes",
          "duration": 8, "ratio": "9:16", "n": 1, "external_ref": "pt-0091"}
ANSWER = {"error": {"code": "wallet.empty", "message": "This key has 0 points; the job needs 1.", "balance": 0, "needed": 1}}


def seed(db):
    keys = []
    for label in KEYS:
        k = new_key(db)
        db.run("UPDATE api_keys SET label = %s WHERE id = %s", label, k)
        keys.append(k)
    now = int(time.time() * 1000)
    for ago, status, route, title, provider, ki, error, dur in ROWS:
        job = None
        if title:
            job = done_job(db, keys[ki if ki is not None else 0], ref="ref-%d" % ago, prompt=title)
            db.run("UPDATE jobs SET title = %s WHERE id = %s", title, job)
        body = PROMPT if status == 402 else {"prompt": title or "-"}
        resp = ANSWER if status == 402 else {"ok": status < 400}
        db.run("INSERT INTO request_logs (key_id, provider, route, status_code, job_id, duration_ms, error_code, "
               "body_redacted, response_redacted, at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
               "to_timestamp(%s::float8 / 1000.0))",
               keys[ki] if ki is not None else None, provider or None, route, status, job, dur, error,
               json.dumps(body), json.dumps(resp), now - ago * 1000)


@pytest.mark.parametrize("color_scheme", ["light", "dark"])
def test_shots(app, app_session, db, color_scheme):
    page = app_session
    seed(db)
    os.makedirs(SHOTS, exist_ok=True)
    for width, height in ((1440, 1360), (820, 1140), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        open_tab(page, "Request logs")
        expect(request_buttons(page).first).to_be_visible()
        page.wait_for_timeout(600)
        page.mouse.move(0, 0)
        page.screenshot(path="%s/logs-%d-%s.png" % (SHOTS, width, color_scheme))
        if width == 390:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)", isolated_context=False)
            page.wait_for_timeout(400)
            page.screenshot(path="%s/logs-390-%s-bottom.png" % (SHOTS, color_scheme))
            page.evaluate("window.scrollTo(0, 0)", isolated_context=False)
        if width < 1280:
            page.get_by_role("button", name="Filters: dates").click()
            expect(page.get_by_test_id("logs-filters-sheet")).to_be_visible()
            page.wait_for_timeout(500)
            page.screenshot(path="%s/logs-%d-%s-filters.png" % (SHOTS, width, color_scheme))
            page.get_by_role("button", name="Done").click()
            expect(page.get_by_test_id("logs-filters-sheet")).to_be_hidden()
            page.get_by_role("button", name="Search requests").click()
            page.wait_for_timeout(300)
            page.screenshot(path="%s/logs-%d-%s-search.png" % (SHOTS, width, color_scheme))
            page.get_by_role("button", name="Search requests").click()
        request_buttons(page).nth(2).click()
        expect(page.get_by_test_id("request-drawer")).to_be_visible()
        page.wait_for_timeout(700)
        page.mouse.move(0, 0)
        page.screenshot(path="%s/logs-detail-%d-%s.png" % (SHOTS, width, color_scheme))
        page.get_by_test_id("request-drawer").get_by_role("button", name=re.compile(r"^Close")).click()
        expect(page.get_by_test_id("request-drawer")).to_be_hidden()
