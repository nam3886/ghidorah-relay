"""The gemini spec (caps, settings, catalog, error rules, `plan()`), the credential import and the frames."""

import json
from datetime import UTC, datetime

import pytest
from geminikit import PSID, PSIDTS, export

from relay_core import errors, frames, pricing
from relay_core.api.doors import checked_prompt
from relay_core.pool import can_afford, day_pool, left_of, pool_configs
from relay_core.providers import (
    AfterSent,
    Completion,
    CreateRequest,
    Dead,
    InputError,
    MediaIn,
    ProviderRegistry,
    _problems,
)
from relay_core.testing.fake_provider import FakeSettings
from relay_core.testing.mutation import mutation
from relay_dola.spec import SPEC as DOLA
from relay_flow.spec import SPEC as FLOW
from relay_gemini import credential as cred
from relay_gemini.spec import SPEC
from relay_meta.spec import SPEC as META
from relay_vibes.spec import SPEC as VIBES

SPEC_FILE = "packages/provider-gemini/src/relay_gemini/spec.py"
CREDENTIAL_FILE = "packages/provider-gemini/src/relay_gemini/credential.py"
IMG = MediaIn(sha256="0" * 64, mime="image/png", size=10)
MODEL = "gemini-nano-banana-2"
VIDEO = "gemini-omni-video"
PRO = "gemini-nano-banana-2.1"          # the pro-tier image model: the "Pro" mode of an AI Pro account


def plan(model=MODEL, prompt="a lighthouse at dawn", *, cfg=None, **kw):
    return SPEC.plan(CreateRequest(model=model, prompt=prompt, **kw), cfg or FakeSettings(SPEC), {})


def priced(model=MODEL, prompt="a lighthouse at dawn", *, cfg=None, **kw):
    """The plan as the door makes it: the core's price and upstream cost from `gemini.prices` and `gemini.costs`."""
    cfg = cfg or FakeSettings(SPEC)
    return pricing.price_plan(SPEC, plan(model, prompt, cfg=cfg, **kw), cfg)


def refusal(model=MODEL, prompt="a lighthouse at dawn", **kw):
    try:
        plan(model, prompt, **kw)
    except InputError as e:
        return e.code, e.param
    return None


# ---- caps, settings, catalog, codes --------------------------------------------------------------------------------
def test_the_caps_say_an_http_provider_with_an_image_and_a_video_pool_checked_every_five_minutes():
    c = SPEC.caps
    assert SPEC.name == "gemini" and c.execution == "http"
    assert [(p.name, p.unit) for p in c.pools] == [("image", "uses"), ("image_pro", "uses"), ("video", "uses")]
    assert (c.account_slots, c.max_attempts, c.suspect_threshold, c.strike_effect) == (1, 3, 3, Dead("signed_out"))
    assert (c.has_poll, c.has_reconcile, c.has_probe_proxy, c.titles) == (True, True, False, False)
    # the check rotates the session, which ages out in ~20 min: it must come at least twice per rotation interval
    assert c.check_interval_s * 2 <= SPEC.settings()["gemini.rotate_interval_s"].default


@mutation(SPEC_FILE, '    _setting("claims_enabled", bool, False,', '    _setting("claims_enabled", bool, True,',
          expect="gemini must stay drained")
def test_gemini_stays_drained_by_default_and_declares_its_tunables():
    s = SPEC.settings()
    assert s["gemini.claims_enabled"].default is False, "gemini must stay drained until the owner turns it on"
    assert (s["gemini.rotate_interval_s"].default, s["gemini.rotate_interval_s"].max) == (600, 900)
    assert s["gemini.account_min_gap_s"].default == 30 and s["gemini.poll_interval_s"].default == 30
    assert s["gemini.render_deadline_s"].default == 900
    assert s["gemini.prices"].default == {MODEL: 1, PRO: 2, VIDEO + "@10s": 2}
    assert s["gemini.costs"].default == {MODEL: 1, PRO: 1, VIDEO + "@10s": 1}, "one use of its budget per job"
    assert s["gemini.pools"].default == {"image": day_pool({"free": 100}),
                                         "image_pro": day_pool({"free": 0, "pro": 100, "ultra": 1000}),
                                         "video": day_pool({"free": 15})}
    assert s["gemini.video_limit_rest_s"].default == 5 * 3600
    assert all(s[k].description for k in s) and _problems(SPEC) == []


def test_the_registry_takes_gemini_beside_the_others_and_no_model_name_clashes():
    reg = ProviderRegistry()
    for spec in (DOLA, META, VIBES, FLOW, SPEC):
        reg.register(spec)
    spec, model = reg.resolve(MODEL)
    assert spec is SPEC and model.operations == ("text_to_image", "image_to_image") and model.media_type == "image"
    assert model.limits["max_images"] == 1 and model.limits["ratios"] == ["1:1", "9:16", "3:4", "4:3", "16:9"]
    assert [m.id for m in SPEC.catalog()] == [MODEL, PRO, VIDEO]
    spec, video = reg.resolve(VIDEO)
    assert spec is SPEC and video.media_type == "video" and video.operations == ("text_to_video", "image_to_video")
    assert {k: video.limits[k] for k in ("max_images", "ratios", "default_ratio", "durations", "default_duration")} \
        == {"max_images": 1, "ratios": ["16:9"], "default_ratio": "16:9", "durations": [10], "default_duration": 10}


def test_gemini_registers_its_codes_on_load():
    assert set(SPEC.codes()) <= set(errors.RULES)
    for code in ("gemini.http_error", "gemini.upload_failed", "gemini.account_unresolved"):   # before the send
        r = errors.rule(code)
        assert (r.terminal, r.refund, r.counts_attempt, r.public) == (False, True, True, True), code
    for code in ("gemini.refused", "gemini.not_entitled", "gemini.video_limit"):   # answered for good: refunded
        r = errors.rule(code)
        assert (r.terminal, r.refund, r.counts_attempt, r.public) == (True, True, False, True), code


# ---- plan ----------------------------------------------------------------------------------------------------------
def test_a_text_job_draws_with_its_ratio_or_none_and_an_image_job_edits_held_in_the_session():
    for ratio in ("1:1", "9:16", "3:4", "4:3", "16:9"):
        assert dict(plan(ratio=ratio).params) == {"ratio": ratio}
    p = plan()
    assert dict(p.params) == {} and p.operation == "text_to_image"
    assert (p.media_type, p.completion, p.after_sent) == ("image", Completion.HOLD, AfterSent.RECONCILE)
    assert (p.pool, p.price_key, p.submit_timeout_s) == ("image", MODEL, 300)
    assert (priced().price, priced().account_cost) == (1, 1)
    edit = plan(images=(IMG,))
    assert edit.operation == "image_to_image" and dict(edit.params) == {}


@mutation(SPEC_FILE, "        if len(req.images) > 1:", "        if False:", expect="two input images")
@mutation(SPEC_FILE, "        if req.ratio is not None and (req.images or req.ratio not in C.RATIO_IDS):",
          "        if req.ratio is not None and req.ratio not in C.RATIO_IDS:", expect="a ratio with an image")
@mutation(SPEC_FILE, "        if C.is_blank_prompt(req.prompt):", "        if False:", expect="a blank prompt")
def test_what_gemini_cannot_do_is_refused_rather_than_ignored():
    assert refusal(images=(IMG, IMG)) == ("input.too_many_images", "images"), "two input images were planned"
    assert refusal(images=(IMG,), ratio="1:1") == ("input.bad_ratio", "ratio"), "a ratio with an image was planned"
    assert refusal(n=2) == ("input.invalid", "n")
    assert refusal(prompt=" ​ ") == ("input.empty_prompt", "prompt"), "a blank prompt was planned"
    assert refusal(duration=8) == ("input.bad_duration", "duration")
    for ratio in ("21:9", "16x9", "2:3"):
        assert refusal(ratio=ratio) == ("input.bad_ratio", "ratio"), ratio
    for name in ("gemini", "veo", "flow-nano-banana-2.1"):
        assert refusal(name) == ("input.bad_model", "model"), name


VIDEO_BRANCH = "        if req.model == C.VIDEO_MODEL:\n            return self._video(req, settings)\n"
VIDEO_DURATION = "        if req.duration not in (None, C.VIDEO_DURATION):"
VIDEO_RATIO = "        if ratio not in C.VIDEO_RATIO_IDS:"


@mutation(SPEC_FILE, VIDEO_BRANCH, "", expect="a video planned as an image")
@mutation(SPEC_FILE, VIDEO_DURATION, "        if False:", expect="an 8 s video")
@mutation(SPEC_FILE, VIDEO_RATIO, "        if False:", expect="a portrait video")
@mutation(SPEC_FILE, '            operation="image_to_video" if req.images else "text_to_video",',
          '            operation="text_to_video",', expect="an image to video")
def test_a_video_job_is_ten_seconds_landscape_detached_on_the_video_pool_from_text_or_one_image():
    try:
        p = plan(VIDEO)
    except InputError as e:
        raise AssertionError("a video planned as an image: %s" % e.code) from None
    assert (p.operation, p.media_type, p.model) == ("text_to_video", "video", VIDEO), "a video planned as an image"
    assert dict(p.params) == {"ratio": "16:9", "duration": 10}
    assert (p.completion, p.after_sent) == (Completion.DETACH, AfterSent.RECONCILE)
    assert (p.pool, p.price_key) == ("video", VIDEO + "@10s")
    assert (priced(VIDEO).price, priced(VIDEO).account_cost) == (2, 1), "2 points, one use of the video budget"
    assert plan(VIDEO, duration=10, ratio="16:9").params == p.params
    assert plan(VIDEO, images=(IMG,)).operation == "image_to_video", "an image to video planned from text"
    assert refusal(VIDEO, duration=8) == ("input.bad_duration", "duration"), "an 8 s video was planned"
    assert refusal(VIDEO, ratio="9:16") == ("input.bad_ratio", "ratio"), "a portrait video was planned (not measured)"
    assert refusal(VIDEO, images=(IMG, IMG)) == ("input.too_many_images", "images")
    assert refusal(VIDEO, prompt=" ") == ("input.empty_prompt", "prompt")


@mutation(SPEC_FILE, 'renamed={"gemini-omni-video": ("gemini-omni-video@10s",)}', "renamed={}",
          expect="the stored video price")
def test_a_price_table_stored_by_the_earlier_build_keeps_the_videos_price():
    """The earlier build keyed the video by its model alone: the stored price moves to `gemini-omni-video@10s`, and
    the image keeps its default."""
    loaded = SPEC.settings()["gemini.prices"].on_load({"gemini-omni-video": 3})
    assert loaded == {MODEL: 1, PRO: 2, VIDEO + "@10s": 3}, "the stored video price was lost: %s" % loaded


def test_the_price_comes_from_the_settings_and_a_missing_one_is_refused():
    cfg = FakeSettings(SPEC, gemini__prices={MODEL: 4})
    assert priced(cfg=cfg).price == 4 and priced(options={"price": 0}).price == 1
    with pytest.raises(pricing.Unpriced):
        priced(VIDEO, cfg=cfg)


# ---- the credential ------------------------------------------------------------------------------------------------
def sealed(raw) -> dict:
    return json.loads(SPEC.parse_credential(json.dumps(raw).encode()).blob)


def refused(raw) -> str:
    try:
        SPEC.parse_credential(raw if isinstance(raw, bytes) else json.dumps(raw).encode())
    except ValueError as e:
        assert PSID not in str(e) and PSIDTS not in str(e)
        return str(e)
    return "accepted"


@mutation(CREDENTIAL_FILE, '    cookies = [c for c in cookies if on_google(c["domain"])]', "",
          expect="a cookie of another site was sealed")
def test_an_export_seals_the_google_cookies_only_with_their_domains_and_rotates_at_first_use():
    blob = sealed(export())
    assert blob["rotated_at"] == 0
    assert [(c["name"], c["domain"]) for c in blob["cookies"]] == [
        ("__Secure-1PSID", ".google.com"), ("__Secure-1PSIDTS", ".google.com"), ("SIDCC", ".google.com"),
        ("COMPASS", ".gemini.google.com")], "a cookie of another site was sealed"
    assert SPEC.parse_credential(json.dumps(export()).encode()).hint == "gemini"
    wrapped = sealed({"url": "https://gemini.google.com/", "cookies": export()})
    assert wrapped["cookies"][0]["value"] == PSID
    again = sealed({"cookies": blob["cookies"], "rotated_at": 1792737600.5})
    assert again == {**blob, "rotated_at": 1792737600.5}


NEEDS_PSID = '    if not any(c["name"] == SESSION_COOKIE and c["value"] for c in cookies):'


@mutation(CREDENTIAL_FILE, NEEDS_PSID, "    if not cookies:", expect="no google.com __Secure-1PSID cookie")
def test_an_export_without_the_google_session_cookie_is_refused_with_a_fixed_reason():
    no_psid = [c for c in export() if c["name"] != "__Secure-1PSID"]
    elsewhere = [{**export()[0], "domain": ".notgoogle.com"}]
    for raw in (no_psid, elsewhere):
        assert "no google.com __Secure-1PSID cookie" in refused(raw)
    assert refused(b"") == "empty credential"
    assert refused(b"SID=abc") == "the credential is not a JSON cookie export"
    with pytest.raises(ValueError, match="not readable"):
        cred.jar_of(b"[not json")


@mutation(CREDENTIAL_FILE, "    email = _email(data)\n", "    email = None\n", expect="the email was not sealed")
@mutation(CREDENTIAL_FILE, "    return raw.strip().lower()", "    return raw.strip()", expect="not lowercased")
@mutation(CREDENTIAL_FILE, "    if not isinstance(raw, str) or len(raw) > 254 or not _EMAIL.fullmatch(raw.strip()):",
          "    if not isinstance(raw, str):", expect="a bad email was sealed")
def test_an_import_may_name_the_google_account_by_email_which_is_sealed_lowercased():
    blob = sealed({"cookies": export(), "email": " Owner.Two@Example.com "})
    assert blob.get("email") == "owner.two@example.com", "the email was not sealed, or not lowercased: %s" % blob
    assert cred.email_of(json.dumps(blob).encode()) == "owner.two@example.com"
    assert "email" not in sealed(export()) and cred.email_of(json.dumps(sealed(export())).encode()) is None
    for bad in ("owner", "a@b", "two words@example.com", 7, "x" * 250 + "@example.com"):
        assert refused({"cookies": export(), "email": bad}) == "the email is not an email address", \
            "a bad email was sealed: %r" % (bad,)


# ---- prompt frames ---------------------------------------------------------------------------------------------------
@mutation(SPEC_FILE, "max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX, verbatim=True)",
          "max_sent_chars=PROMPT_MAX_CHARS + frames.FRAME_MAX)", expect="a tracking code")
def test_the_shipped_frame_sends_the_customers_words_with_no_tracking_code():
    """gemini drew a typed tracking code into the image (FINDINGS 4): the default frame must render none."""
    assert set(SPEC.frames.defaults) == {"image", "video"}
    for words in ("a calm sea at dusk", "  a lantern\n\tfloats over a lake  "):
        got = frames.render(SPEC.frames.defaults["image"], checked_prompt(words, SPEC.max_prompt_chars), "7908",
                            SPEC.frames.verbatim)
        assert "7908" not in got, "a tracking code would be drawn into the image: %r" % got
        assert got == words.strip()
    longest = frames.render(SPEC.frames.defaults["image"], "x" * SPEC.max_prompt_chars, "7908", SPEC.frames.verbatim)
    assert len(longest) <= SPEC.frames.max_sent_chars


# ---- the pro-tier image model (Nano Banana 2.1) and the tier gate ---------------------------------------------------
PRO_POOL = '            pool="image_pro" if req.model == C.PRO_IMAGE_MODEL else "image",'


@mutation(SPEC_FILE, PRO_POOL, '            pool="image",', expect="the image_pro pool")
def test_the_pro_tier_image_model_is_an_image_model_of_its_own_pool_and_price():
    p = priced(PRO, ratio="9:16")
    assert (p.operation, p.media_type, p.model, p.params) == ("text_to_image", "image", PRO, {"ratio": "9:16"})
    assert p.pool == "image_pro", "a pro-tier image planned outside the image_pro pool: %s" % p.pool
    assert (p.price_key, p.price, p.account_cost) == (PRO, 2, 1)
    edit = plan(PRO, images=(IMG,))
    assert (edit.operation, edit.pool) == ("image_to_image", "image_pro")
    assert refusal(PRO, images=(IMG,), ratio="1:1") == ("input.bad_ratio", "ratio")
    assert plan(MODEL).pool == "image"
    model = {m.id: m for m in SPEC.catalog()}[PRO]
    assert model.display_name == "Gemini Nano Banana 2.1" and model.limits["ratios"] == ["1:1", "9:16", "3:4", "4:3",
                                                                                         "16:9"]


FREE_NONE = '"image_pro": day_pool({"free": 0, "pro": 100, "ultra": 1000}),'


@mutation(SPEC_FILE, FREE_NONE, '"image_pro": day_pool({"pro": 100, "ultra": 1000}),', expect="a free account")
@mutation(SPEC_FILE, FREE_NONE, '"image_pro": day_pool({"free": 0, "ultra": 1000}),', expect="a pro account")
def test_only_a_pro_or_ultra_account_can_ever_afford_a_pro_tier_image():
    """The tier gate is the pool's budget: 0 for a free account (and one whose tier was never read), so the claim
    never picks it (`can_afford`); a pro or ultra account has its own budget."""
    cfg = pool_configs("gemini", SPEC.caps, FakeSettings(SPEC).get)["image_pro"]
    now = datetime(2026, 10, 25, 6, 0, tzinfo=UTC)
    for tier in ("free", None):
        left = left_of(cfg, tier, [], now)
        assert left == 0 and not can_afford(left, 0, 1), "a free account could take a pro-tier image: %s" % left
    assert left_of(cfg, "pro", [], now) == 100, "a pro account had no budget of its own"
    assert left_of(cfg, "ultra", [], now) == 1000
    assert left_of(pool_configs("gemini", SPEC.caps, FakeSettings(SPEC).get)["image"], "free", [], now) == 100


def test_portrait_video_is_not_sold_until_it_is_measured():
    """25/10: two text to video sends with the portrait option (17) both came back 1280x720 landscape."""
    assert refusal(VIDEO, ratio="9:16") == ("input.bad_ratio", "ratio")
    assert {m.id: m for m in SPEC.catalog()}[VIDEO].limits["ratios"] == ["16:9"]
