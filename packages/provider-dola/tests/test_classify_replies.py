"""Reading dola's replies: every verbatim v4 sample (coverage REPLY01-17, ADM21).

Real sentences from dola, kept word for word: the classifier is a white list over exactly this kind of text.
"""


from relay_core.testing.mutation import mutation
from relay_dola import classify as K
from relay_dola.classify import SubmitOutcome as O
from relay_dola.classify import (
    asks_question,
    classify_reply,
    durations_named,
    model_substitution,
    no_result_cause,
    order_accepted,
    order_taken,
)

CLASSIFY_FILE = "packages/provider-dola/src/relay_dola/classify.py"



AB = ("I can generate a full 30-second storyboard image as a single 9:16 frame, but I cannot directly "
      "render a 30-second cinematic video in one go—video generation is currently limited to 4–15 "
      "seconds. **Option A: Generate the full 30-frame storyboard image now (9:16, 5×6 grid).** "
      "**Option B: Generate the first 15-second video segment now, covering 0–15 seconds of your "
      "script.** Reply with **A** or **B** and I’ll generate immediately.")
UPLOAD = ("Only two videos can be generated at a time. I’ll start by generating the first 15 seconds for "
          "you. The remaining 15 seconds can be generated in the next turn. To proceed, please upload "
          "the 3 reference images of the 3 people and the stage reference image.")
VIETNAMESE = ("30 giây hiện không được hỗ trợ; video chỉ có thể tạo từ 4–15 giây. Tôi sẽ tạo phiên bản 15 giây "
              "gần nhất, giữ toàn bộ sân khấu, nhân vật và thứ tự hành động chính. Tuy nhiên, tôi chưa thấy ảnh "
              "tham chiếu sân khấu và 3 người bạn đã cung cấp. Để đảm bảo nhận diện khuôn mặt, trang phục và "
              "bối cảnh chính xác, bạn vui lòng tải lên 4 ảnh: 1. Ảnh sân khấu tổng thể 2. Ảnh Người 1 "
              "3. Ảnh Người 2 4. Ảnh Người 3 Sau khi có ảnh, tôi sẽ tạo video 15 giây ngay lập tức theo yêu "
              "cầu của bạn.")
PROCEED = ("The video will use 2 credits and be ready in 5 minutes. Video generation currently supports "
           "durations from 4 to 15 seconds. Should I proceed?")                          # job 912d715f
TAKEN = ("Only two videos can be generated at a time. I'll start by generating two 15-second "
         "segments for you. The video will be generated using the **Dreamina Seedance 2.5 model**. "
         "It will use 4 credits and be ready in 15 minutes. You still have 0 video credits left today.")
ASKED = ("Video generation currently supports durations from 4 to 15 seconds. Do you want me to proceed "
         "with 15 seconds? The video will be generated using the **Dreamina Seedance 2.5 model**.")
LIKENESS = ("For likeness protection, Dreamina Seedance 2.5 only supports generating videos featuring yourself. "
            "Use another image, or create a video from text.")
NOTICE_4_15 = ("Video generation currently supports durations from 4 to 15 seconds. I can generate it at the "
               "nearest supported duration of 15 seconds.")
NOTICE_AND_ACCEPT = ("The video will be generated using the **Dreamina Seedance 2.5 model** in 2 hours minutes. "
                     "I'll send it to you when it's done. This generation uses your daily free credits. " + NOTICE_4_15)
ACCEPTED = ("The video will be generated using the Dreamina Seedance 2.5 model. It will "
            "use 2 credits and be ready in 15 minutes. I'll send it to you when it's done. "
            "You still have 1 video credits left today.")
REFUSED_CREDIT = ("Generating with the current parameters will use 2 video credits. "
                  "You only have 1 left today. Change the parameters and try again.")
COPYRIGHT = ("For copyright protection, I can't show you the generated video. Use other "
             "references or edit the prompt and try again. The video will be generated using "
             "the Dreamina Seedance 2.5 model. It will use 2 credits and be ready in 15 "
             "minutes. I'll send it to you when it's done. You still have 0 video credits left today.")
CANNOT_GEN = ("The video will be generated using the Dreamina Seedance 2.5 model. It will use "
              "2 credits and be ready in 15 minutes. You still have 2 video credits left today. "
              "I can't generate the video and didn't use any credit for this generation.")
CONFIRM = ("The video you requested is 30 seconds long, but generation currently supports "
           "durations from 4 to 15 seconds. I can generate it at the nearest supported "
           "duration of 15 seconds. Do you want me to proceed with that?")
CONFIRM_2 = ("30 seconds exceeds the current maximum supported duration of 15 seconds. I can "
             "generate this as a 15-second version. Would you like me to proceed with: A. "
             "15-second single video B. Split into two 15-second videos C. Shorten to 15 seconds?")
PLEASE_CONFIRM = ("I understand you need a 30-second final segment, but the current video generation only supports "
                  "4–15 seconds. I can generate it at the nearest supported duration of 15 seconds. If you’d like "
                  "to proceed with that, please confirm.")
SAFETY = ("I notice you've shared details about a video take - \"getting up and jumping down a "
          "hill [take 5301], 16:9.\"\n\nIf this video involves actions that could put someone's "
          "safety at risk, it's important to prioritize well-being in any activity being filmed. "
          "If you're feeling distressed, there are people who can support you.\n\n"
          "[Resources](https://www.dola.com/help_resources/en)")


# ---- REPLY01: why nothing came, from dola's last words ---------------------------------------------------------------
def test_the_deadline_cause_is_read_from_dolas_last_words():
    c = no_result_cause("I can't generate the video and didn't use any credit for this generation. The video "
                        "will be generated using the **Dreamina Seedance 2.5 model**.")          # 3c5a8a6d
    assert c and c[0] == "dola.content_refused", (
        "a refusal at the deadline is still reported as render.deadline: %r" % (c,))
    c = no_result_cause("I can't generate the requested images. Try something else.")            # 3312dcb5
    assert c and c[0] == "dola.content_refused", c
    c = no_result_cause("I’d recommend splitting it into two 15-second parts. Would you like me to generate "
                        "**Part 1 only**, **Part 2 only**, or **both parts** now?")                # 35d578d7
    assert c and c[0] == "dola.needs_confirm", (
        "a question at the deadline is still reported as render.deadline: %r" % (c,))
    assert no_result_cause("Video generation currently supports durations from 4 to 15 seconds. I can generate "
                           "it at the nearest supported duration of 15 seconds. The video will be generated using "
                           "the **Dreamina Seedance 2.5 model** in 15 minutes minutes.") is None, (
        "an accepted order that never came must stay render.deadline")
    assert no_result_cause("") is None and no_result_cause(None) is None


# ---- REPLY02: questions, options, requests for images are gates ------------------------------------------------------
@mutation(CLASSIFY_FILE, "if any(marker in low for marker in _NEEDS_CONFIRM_MARKERS):",
          "if False:", expect="is not seen as a question")
def test_questions_options_and_requests_for_images_are_gates():
    for name, text in (("A/B options", AB), ("upload request", UPLOAD), ("Vietnamese reply", VIETNAMESE)):
        v = classify_reply(text, want_duration=30)
        assert v.outcome is O.NEEDS_CONFIRM and v.video_possible, "%s is not seen as a question: %r" % (name, v)
        assert asks_question(text), "%s is not seen as a question" % name
        c = no_result_cause(text)
        assert c and c[0] == "dola.needs_confirm", "%s at the deadline: %r" % (name, c)
    # on the OLD composer a question carries the acceptance boilerplate and is NOT taken (rule measured 25/09)
    assert not order_taken(PROCEED, new_composer=False), "'Should I proceed?' with the boilerplate is taken as an order"
    assert order_taken(PROCEED, new_composer=True)
    # curly apostrophe: dola writes both "can’t" and "can't"
    v = classify_reply("I can’t generate the video and didn’t use any credit for this generation.")
    assert v.outcome is O.CONTENT_REFUSED, "a refusal with a curly apostrophe is missed: %r" % (v,)
    assert durations_named("Tôi sẽ tạo đoạn đầu tiên 15 giây") == [15], "a Vietnamese reply naming another length"


# ---- REPLY03: has the order gone in (both composer variants) ---------------------------------------------------------
@mutation(CLASSIFY_FILE, "return order_accepted(text) and (new_composer or not asks_question(text))",
          "return order_accepted(text)", expect="new_composer=False")
def test_first_replies_that_accept_the_order_are_recognised_verbatim():
    assert order_taken(TAKEN, new_composer=False), "an accepted order on the old composer is not seen as taken"
    assert not order_taken(ASKED, new_composer=False), "a question on the old composer is taken as an order"
    assert order_taken(TAKEN, new_composer=True)
    v = classify_reply("I can’t generate a full 30-second video directly... Would you like me to generate "
                       "**Part 1 only**, **Part 2 only**, or **both parts** now?", want_duration=30)
    assert v.outcome is O.NEEDS_CONFIRM, "dola's counter-proposal is not seen as a question: %r" % (v,)
    v = classify_reply("I can't generate the requested images. Try something else.")
    assert v.outcome is O.CONTENT_REFUSED and not v.video_possible, (
        "an image refusal is taken as an accepted order: %r" % (v,))


def test_the_acceptance_judgement_on_the_new_composer_keeps_a_question_that_also_accepts_as_taken():
    # measured 24/09: the new composer was never seen to ask and accept together, so an acceptance is taken
    assert order_taken(NOTICE_AND_ACCEPT, new_composer=True) and order_taken(NOTICE_AND_ACCEPT, new_composer=False)
    assert not order_taken(NOTICE_4_15, new_composer=True) and not order_taken(NOTICE_4_15, new_composer=False)


# ---- REPLY04: likeness -----------------------------------------------------------------------------------------------
@mutation(CLASSIFY_FILE, 'videos of the account owner", credits, video_possible=False, code=codes.LIKENESS_REFUSED)',
          'videos of the account owner", credits, video_possible=False)', expect="carries no likeness code")
def test_the_likeness_refusal_is_recognised_verbatim():
    assert order_accepted(NOTICE_AND_ACCEPT), "an accepted order is not recognised: a re-send would be paid twice"
    assert not order_accepted(NOTICE_4_15), (
        "only the 4-15 s notice taken as accepted: the job polls an hour for nothing")
    v = classify_reply("I can't generate the requested content. Try something else.", want_duration=30)
    assert v.outcome is O.CONTENT_REFUSED and not v.video_possible, v
    v = classify_reply(LIKENESS, want_duration=30)
    assert v.outcome is O.CONTENT_REFUSED and not v.video_possible, "likeness read as 'no reply yet': %r" % (v,)
    assert "likeness" in v.detail
    assert v.code == "dola.likeness_refused", "a likeness refusal carries no likeness code: %r" % (v,)
    assert no_result_cause(LIKENESS)[0] == "dola.likeness_refused", (
        "a likeness refusal at the deadline is content_refused")
    assert classify_reply("I can't generate the requested content. Try something else.").code is None


# ---- REPLY05: model substitution -------------------------------------------------------------------------------------
def test_a_model_substitution_is_recognised_verbatim():
    swap = model_substitution("The video will be generated using the **Dreamina Seedance 2.0 Mini model**. "
                              "It will use 3 credits", "seedance_v2.5")
    assert swap and "2.0 Mini" in swap and "2.5" in swap, "dola swapped 2.5 for 2.0 Mini and nobody knows: %r" % swap
    assert model_substitution("The video will be generated using the **Dreamina Seedance 2.5 model**.",
                              "seedance_v2.5") is None, "right model, yet warned"
    assert model_substitution("generated using the Dreamina Seedance 2.0 model", "seedance_v2.0") is None, (
        "dola dropped the word 'Fast' and it was read as a swap")
    assert model_substitution("Done. I generated four versions.", "seedance_v2.5") is None
    assert K.named_model("generated using the Dreamina Seedance 2.0 Mini model") == "Dreamina Seedance 2.0 Mini"
    assert K.same_model("anything", "unknown_model_without_label")


@mutation(CLASSIFY_FILE, "([^*\\n]{1,80}?)\\s+model", "([^*\\n]+?)\\s+model",
          expect="the model name pattern is quadratic")
def test_a_long_line_cannot_stall_the_model_name_pattern():
    """A prompt is the customer's: a 50k-character line must not take seconds on the worker loop."""
    import time
    line = "generated using the " * 2490                        # 49.8k characters, no "model" anywhere
    started = time.perf_counter()
    assert K.named_model(line) is None
    assert K.model_substitution(line, "seedance_v2.5") is None
    took = time.perf_counter() - started
    assert took < 0.4, "the model name pattern is quadratic: %.2fs for two calls on one line" % took
    assert K.named_model("generated using the " + "x " * 30 + "model") == ("x " * 30).strip()


# ---- REPLY06: the three real verification cases ----------------------------------------------------------------------
@mutation(CLASSIFY_FILE, "if any(marker in low for marker in _CONTENT_REFUSED_MARKERS):", "if False:",
          expect="copyright read as")
def test_three_real_verification_cases_classify_as_measured():
    v = classify_reply(ACCEPTED)
    assert v.outcome is O.OK and v.credits_left == 1, v                           # conv 38417084101562129
    v = classify_reply(REFUSED_CREDIT)
    assert v.outcome is O.NO_POINTS and v.credits_left == 1, v                    # conv 38417909702724625
    v = classify_reply("Something went wrong. Please try again.")
    assert v.outcome is O.ERROR and "temporary" in v.detail, v                    # conv 38416991999381009
    assert v.video_possible is False, "a read error sentence means there is certainly no video"
    # copyright refusal: holds the acceptance phrase too, must NEVER read as OK (conv 38417911513384977, 13/09)
    v = classify_reply(COPYRIGHT)
    assert v.outcome is O.CONTENT_REFUSED, "copyright read as %r" % (v,)
    assert v.video_possible is False, "copyright = terminal, no video"
    # dola refuses to MAKE it (conv 38417876469706769, 16/09): same boilerplate, used to read as OK
    v = classify_reply(CANNOT_GEN)
    assert v.outcome is O.CONTENT_REFUSED and v.video_possible is False, v
    assert "declined to generate" in v.detail, v
    # a confirmation question (conv 38417875291316241): not terminal, no refund-worthy end, re-send makes the video
    v = classify_reply(CONFIRM)
    assert v.outcome is O.NEEDS_CONFIRM and v.video_possible, v
    assert "re-send" in v.detail.lower(), v
    # another real wording of the same case (conv 38417912175751441, 14/09)
    v = classify_reply(CONFIRM_2)
    assert v.outcome is O.NEEDS_CONFIRM and v.video_possible, v


# ---- REPLY07: a gate wins over an acceptance -------------------------------------------------------------------------
def test_a_gate_wins_over_an_acceptance_in_the_same_reply():
    both = ("Your video is ready. The 15-second generation failed. I'll retry with a stable "
            "10-second version instead. The video will use 2 credits and be ready in 5 minutes. "
            "You still have 0 video credits left today. Video generation currently supports "
            # production 22/09, job 912d715f
            "durations from 4 to 15 seconds. Should I proceed?")
    v = classify_reply(both)
    assert v.outcome is O.NEEDS_CONFIRM, "the acceptance beat dola's question: the job polls its deadline: %r" % (v,)
    assert v.video_possible, "a gate is not a failure: a re-send in the conversation makes dola do it"
    assert classify_reply(both).code is None


# ---- REPLY08: a declared different length is a gate ------------------------------------------------------------------
@mutation(CLASSIFY_FILE, "if named and want_duration not in named:", "if False:", expect="read as accepted")
def test_a_declared_different_duration_is_a_gate():
    stated = ("I'll generate a 15-second 16:9 version for you. If you prefer a shorter clip, "
              "let me know. The video will use 2 credits and be ready in 5 minutes.")
    assert classify_reply(stated).outcome is O.OK, "without the asked length there is no ground: it is taken"
    v = classify_reply(stated, want_duration=5)
    assert v.outcome is O.NEEDS_CONFIRM, "15 s stated for a 5 s order read as accepted: %r" % (v,)
    assert "15" in v.detail and "5" in v.detail, "the reason does not name the two numbers: %r" % v.detail
    correct = ("I'll generate a 30-second 16:9 video. The video will use 2 credits and be ready "
               "in 5 minutes.")
    assert classify_reply(correct, want_duration=30).outcome is O.OK, "the right length read as a gate"
    assert classify_reply("The video will be ready in 5 minutes.", want_duration=30).outcome is O.OK
    assert durations_named(stated) == [15] and durations_named("no numbers here") == []
    assert classify_reply(stated, want_duration=5).video_possible
    gated = classify_reply(stated, want_duration=5)
    assert gated.outcome is O.NEEDS_CONFIRM, "read as accepted"
    assert classify_reply(stated, want_duration=15).outcome is O.OK


# ---- REPLY09-11 ------------------------------------------------------------------------------------------------------
def test_no_reply_yet_is_never_read_as_success():
    v = classify_reply("")
    assert v.outcome is None and not v.decided, v
    v = classify_reply("Generating a cinematic slow-motion video of a calm ocean wave...")
    assert v.outcome is None, "'generating...' is not an acceptance"
    assert v.video_possible is True, "reading nothing must assume a video MAY come"
    assert classify_reply(None).outcome is None


def test_a_finished_render_is_recognised():
    assert classify_reply("Your video is ready.").outcome is O.OK


def test_a_daily_limit_reply_is_not_skewed_by_sidebar_titles():
    daily = ("Recents Video Generation Limit Video Generation Error Sunset Beach Wave Video "
             "Generated video: sóng biển You've reached the daily limit for video generation. "
             "Please try again tomorrow.")
    assert classify_reply(daily).outcome is O.NO_POINTS
    sidebar_only = ("Recents Video Generation Limit Video Generation Error Video generation limit "
                    "Sunset Beach Wave Video")
    assert classify_reply(sidebar_only).outcome is None, "sidebar titles were read as dola's words"


# ---- REPLY12-14: credit statements -----------------------------------------------------------------------------------
def test_a_content_refusal_with_no_credits_used_leaves_the_account_with_credit():
    v = classify_reply("I can't generate the video. No credits were used.")
    assert v.outcome is O.CONTENT_REFUSED, v
    assert not v.video_possible, v
    assert classify_reply("For copyright protection, I can't show you the generated video. No credits were "
                          "used.").outcome is O.CONTENT_REFUSED


@mutation(CLASSIFY_FILE, 'return ReplyVerdict(None, "no clear reply yet", credits)',
          'return ReplyVerdict(SubmitOutcome.NO_POINTS if credits == 0 else None, "no clear reply yet", credits)',
          expect="a bare zero balance was read as out of credit")
def test_a_bare_zero_balance_is_unclear_but_carries_the_balance_and_a_priced_refusal_still_refuses():
    # no real dola sample ties a bare zero balance to a refusal (v4 only had its own "You have 0 video credits left")
    for said in ("You have 0 video credits left today.", "You only have 0 left today.",
                 "This video needs 2 credits. You only have 0 left today."):
        v = classify_reply(said)
        assert v.outcome is None and v.credits_left == 0 and v.video_possible, (
            "a bare zero balance was read as out of credit: %r -> %r" % (said, v))
    # dola's real not-enough sentence names the price ("will use N ... only have M left", M < N): a known refusal
    v = classify_reply("Generating with the current parameters will use 2 video credits. You only have 0 left today. "
                       "Change the parameters and try again.")
    assert v.outcome is O.NO_POINTS and v.credits_left == 0 and not v.video_possible, v
    assert classify_reply("Something went wrong. Please try again. You have 0 video credits left today.").outcome \
        is O.ERROR, "a temporary error is judged before the balance"


@mutation(CLASSIFY_FILE, 'return ReplyVerdict(None, "no clear reply yet", credits)',
          'return ReplyVerdict(SubmitOutcome.NO_POINTS, "no clear reply yet", credits)',
          expect="an unknown wording was decided")
def test_a_reply_nothing_recognises_is_unclear_never_a_verdict():
    for said in ("Sure! Starting on your 30-second clip now.", "Okay, on it!", "Hmm, let me think about that.",
                 "Great idea. Here is a quick summary of your request.", "", "   "):
        v = classify_reply(said, 30)
        assert v.outcome is None, "an unknown wording was decided: %r -> %r" % (said, v)
        assert v.credits_left is None


@mutation(CLASSIFY_FILE, '    "is being created",', '    "is being createdX",',
          expect="a being-created acceptance was not read")
def test_is_being_created_is_an_acceptance_with_or_without_a_balance():
    said = "Your 30-second video is being created now."
    v = classify_reply(said, 30)
    assert v.outcome is O.OK, "a being-created acceptance was not read: %r" % (v,)
    v = classify_reply(said + " You still have 0 video credits left today.", 30)
    assert v.outcome is O.OK and v.credits_left == 0, v
    for variant in ("Your video is being generated now.", "Your clip is being made."):
        assert classify_reply(variant).outcome is O.OK, variant
    # the gates and refusals still win over the wording
    assert classify_reply("Please confirm: your video is being created once you reply yes.").outcome \
        is O.NEEDS_CONFIRM


@mutation(CLASSIFY_FILE, "if any(marker in low for marker in _ACCEPTED_MARKERS):", "if False:",
          expect="still an acceptance")
def test_a_last_accepted_order_with_a_zero_balance_is_still_accepted():
    last = ("The video will be generated using the Dreamina Seedance 2.5 model. It will use 2 "
            "credits and be ready in 15 minutes. I'll send it to you when it's done. "
            "You still have 0 video credits left today.")              # conv 38416991999997201
    v = classify_reply(last)
    assert v.outcome is O.OK, "a zero balance after an accepted order is still an acceptance"
    assert v.credits_left == 0, "the account must be reported spent so the pool rests it"
    assert v.detail == "dola accepted the request", "the wording is still an acceptance, not the balance rule"


# ---- REPLY15: a refusal that never says "cannot" ---------------------------------------------------------------------
def test_a_refusal_without_the_word_cannot_is_still_a_refusal():
    v = classify_reply(SAFETY)                                           # job 14cba26b, 23/09
    assert v.outcome is O.CONTENT_REFUSED, "a safety refusal read as %r: the job polls to its deadline" % (v.outcome,)
    assert v.video_possible is False, "a refusal read as 'a video may still come'"
    # ...but plain safety advice with NO help link is not a refusal
    v = classify_reply("Please make sure everyone stays safe during filming. "
                       "The video will be generated and ready in 5 minutes.")
    assert v.outcome is not O.CONTENT_REFUSED, "an ordinary safety reminder read as a refusal: %r" % (v.outcome,)
    assert no_result_cause(SAFETY)[0] == "dola.content_refused"


# ---- REPLY16: please confirm -----------------------------------------------------------------------------------------
def test_please_confirm_is_a_question_answered_in_the_same_conversation():
    v = classify_reply(PLEASE_CONFIRM, want_duration=30)                  # job e6d0abb9, 28/09
    assert v.outcome is O.NEEDS_CONFIRM, "'please confirm' is not seen as a question: %r" % (v,)
    assert not order_accepted(PLEASE_CONFIRM)
    assert asks_question(PLEASE_CONFIRM)
