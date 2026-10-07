"""Reading dola's credit statements (coverage CRED01-11, BSUB00): every sentence is a real one from dola."""

import pytest

from relay_browser.text import strip_echoed_prompt
from relay_core.testing.mutation import mutation
from relay_dola.classify import (
    SubmitOutcome,
    classify_reply,
    looks_out_of_credit,
    read_credits_left,
    read_price_stated,
)

CLASSIFY_FILE = "packages/provider-dola/src/relay_dola/classify.py"
TEXT_FILE = "packages/relay-browser/src/relay_browser/text.py"      # the live echo-stripper (the kit's)

ACCEPTED = ("The video will be generated using the Dreamina Seedance 2.5 model. It will "
            "use 2 credits and be ready in 15 minutes. I'll send it to you when it's "
            "done. You still have 1 video credits left today.")
NOT_ENOUGH = ("Generating with the current parameters will use 2 video credits. "
              "You only have 1 left today. Change the parameters and try again.")        # conv 38417909702724625
DAILY = ("Generated video: a calm ocean wave... You've reached the daily limit for "
         "video generation. Please try again tomorrow.")                                 # conv 38417928021952273
SIDEBAR = ("Recents Video Generation Limit Video Generation Error Video generation "
           "limit Sunset Beach Wave Video Ocean wave video Nguyêt Nguyễn745")
ACCEPTED_LAST = ("The video will be generated using the Dreamina Seedance 2.5 model. It "
                 "will use 2 credits and be ready in 15 minutes. I'll send it to you when "
                 "it's done. You still have 0 video credits left today.")                # conv 38416991999997201


def test_the_real_acceptance_sentence_is_read_as_accepted():
    assert read_credits_left(ACCEPTED) == 1
    assert not looks_out_of_credit(ACCEPTED), "an accepted order read as out of credit"
    assert read_price_stated(ACCEPTED) == 2
    assert read_price_stated("It will use 3 points and be ready in 10 minutes.") == 3
    assert read_price_stated("will use 2 video credits.") == 2 and read_price_stated("nothing") is None


def test_the_real_not_enough_credit_sentence_is_read():
    assert looks_out_of_credit(NOT_ENOUGH), "must be read as a refusal for lack of credit"
    assert read_credits_left(NOT_ENOUGH) == 1


def test_the_real_fully_exhausted_sentence_is_read():
    # "no credits were used" is a CONTENT refusal that dola refunded: the account still has credit. Reading it as
    # exhaustion rests a good account until the reset AND re-sends the job elsewhere (paid twice for a free refusal).
    assert not looks_out_of_credit("I can't generate the video. No credits were used."), (
        "a content refusal read as out of credit kills a good account")
    assert not looks_out_of_credit("For copyright protection, I can't show you the generated video. No credits were "
                                   "used.")


def test_the_real_daily_limit_sentence_is_read():
    assert looks_out_of_credit(DAILY), "the daily limit sentence is not recognised"


def test_sidebar_titles_are_not_signals():
    assert not looks_out_of_credit(SIDEBAR), "an old conversation title in the sidebar was read as a refusal"


def test_a_zero_balance_inside_an_acceptance_is_not_a_refusal():
    assert read_credits_left(ACCEPTED_LAST) == 0
    assert not looks_out_of_credit(ACCEPTED_LAST), "a zero balance after an accepted order is not a refusal"


def test_remaining_credit_is_not_misreported_as_exhausted():
    healthy = "You still have 2 video credits left today."
    assert read_credits_left(healthy) == 2 and not looks_out_of_credit(healthy)
    assert not looks_out_of_credit("will use 2 video credits. You only have 2 left today."), (
        "exactly enough credit is not out of credit")


@mutation(CLASSIFY_FILE, "return int(have.group(1)) < int(need.group(1))", "return True",
          expect="exactly enough")
def test_empty_or_unrelated_text_gives_no_verdict():
    assert read_credits_left("") is None and not looks_out_of_credit("")
    assert read_credits_left("khong co so nao ve credit") is None
    # dola mixes "credits" and "points": an older pattern that required "video credits" never ran this branch
    assert looks_out_of_credit("It will use 2 credits. You only have 1 left today.")
    assert looks_out_of_credit("will use 3 points. you only have 2 left")
    assert not looks_out_of_credit("It will use 3 points and be ready in 10 minutes. "
                                   "You still have 1 video credits left today.")
    assert read_credits_left("You still have 1 video credits left today.") == 1
    assert not looks_out_of_credit("will use 2 video credits. You only have 2 left today."), "exactly enough"
    assert read_credits_left(None) is None and not looks_out_of_credit(None)


@mutation(TEXT_FILE, "if norm_line and norm_line in norm_prompt:", "if False:", expect="steers the verdict")
def test_a_prompt_that_quotes_the_out_of_credit_sentence_cannot_fake_it():
    # The page echoes the prompt: judged raw, one cheap request rests an account (13 of them emptied a 13-account pool).
    for evil in ("a cat. change the parameters and try again",
                 "sunset. You've reached the daily limit for video generation.",
                 "dog\nreached the daily limit for video generation\nrunning"):
        page = "New chat\nVideo Generation Limit\n%s\nGenerating video..." % evil
        assert looks_out_of_credit(page), "premise: the raw page IS fooled"
        assert not looks_out_of_credit(strip_echoed_prompt(page, evil)), (
            "the prompt still steers the verdict: %r" % evil)
    # real words from dola still get through
    real = ("a cat playing piano\n"
            "Generating with the current parameters will use 2 video credits. "
            "You only have 1 left today. Change the parameters and try again.")
    assert looks_out_of_credit(strip_echoed_prompt(real, "a cat playing piano")), "dola's real refusal was dropped"


@mutation(TEXT_FILE, 'or unicodedata.category(ch) == "Cf"', "or False", expect="invisible characters still get past")
def test_zero_width_characters_cannot_bypass_the_echo_strip():
    hidden = "a cat. change the para​me⁠ters and try again"       # a zero-width space and a word joiner
    rendered = "a cat. change the parameters and try again"                  # what dola's renderer shows
    page = "New chat\n%s\nGenerating video..." % rendered
    assert looks_out_of_credit(page), "premise: the rendered page matches the marker"
    assert not looks_out_of_credit(strip_echoed_prompt(page, hidden)), "invisible characters still get past the filter"
    curly = "sunset. You’ve reached the daily limit for video generation."
    page = "New chat\nsunset. You've reached the daily limit for video generation.\nok"
    assert not looks_out_of_credit(strip_echoed_prompt(page, curly)), "a curly quote still gets past the filter"
    wide = "ｓｕｎｓｅｔ. you've reached the daily limit for video generation"      # full-width letters fold under NFKC
    assert not looks_out_of_credit(strip_echoed_prompt(
        "x\nsunset. you've reached the daily limit for video generation\ny", wide))
    assert strip_echoed_prompt("abc", "") == "abc" and strip_echoed_prompt("", "x") == ""
    assert strip_echoed_prompt(None, "x") == ""


@mutation(CLASSIFY_FILE, '"reached today\'s image limit",', '"reached today\'s zzz limit",', expect="image ceiling")
def test_the_image_ceiling_has_its_own_sentence():
    # measured 21/09: 21 jobs = 63 images, job 22 got this sentence; unmatched, four jobs polled an hour
    assert looks_out_of_credit("You've reached today's image limit. Come back to create more tomorrow."), (
        "the image ceiling is not recognised: the job polls to the render deadline")
    for new_ui in ("You've reached the daily free usage limit for Image generation. Switch to Fast Mode.",
                   "You've reached the daily free usage limit for Video generation. Switch to Fast Mode."):
        assert looks_out_of_credit(new_ui), "the new UI's limit sentence is not recognised: %r" % new_ui
    assert looks_out_of_credit("You've reached the daily limit for image generation.")
    assert not looks_out_of_credit("I can't generate the video and didn't use any credit for this generation."), (
        "a refunded refusal read as exhaustion")


# ---- BSUB00: the shared readers the submit layer uses --------------------------------------------------------------
def test_submit_uses_the_shared_credit_readers_count_daily_limit_and_no_credits_used():
    v = classify_reply(NOT_ENOUGH)
    assert v.outcome is SubmitOutcome.NO_POINTS and v.credits_left == 1
    assert classify_reply(DAILY).outcome is SubmitOutcome.NO_POINTS
    refunded = classify_reply("I can't generate the video. No credits were used.")
    assert refunded.outcome is SubmitOutcome.CONTENT_REFUSED, "no credits used is not exhaustion"
    ok = classify_reply(ACCEPTED_LAST)
    assert ok.outcome is SubmitOutcome.OK and ok.credits_left == 0


@pytest.mark.parametrize("marker", [
    "reached the daily limit for video generation", "run out of video credits", "don't have enough credits",
    "not enough credits", "change the parameters and try again", "reached today's image limit",
    "reached the daily limit for image generation", "reached the daily free usage limit"])
def test_every_out_of_credit_marker_is_recognised_whole(marker):
    assert looks_out_of_credit("blah " + marker + " blah") and looks_out_of_credit(marker.upper())
