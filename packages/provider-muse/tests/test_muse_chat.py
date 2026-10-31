"""The side chat read from the recorded histories (fixtures/muse/spike-261026), the verdict rules, and what is said."""

import base64
from types import SimpleNamespace

from musekit import history, spike

from relay_core.testing.mutation import mutation
from relay_muse.engine.chat import (
    File,
    Media,
    compose,
    fresh,
    is_media_ref,
    outputs,
    pending_approvals,
    read_history,
    stream_body,
    verdict,
    video_unavailable_here,
)

CHAT_FILE = "packages/provider-muse/src/relay_muse/engine/chat.py"
CONSTANTS_FILE = "packages/provider-muse/src/relay_muse/constants.py"
MEDIA_DIRS = 'MEDIA_DIRS = ("workspace/imagine_media/", "workspace/your_files/")'
YOUR_FILES = "ref-spike-301030/history_your_files.json"
EDIT = "ref-spike-301030/history_edit_two_refs.json"
SID = "11111111-2222-4333-8444-555555555555"


def reading(name, kind="image"):
    return read_history(history(name, SID), kind)


def test_the_recorded_answers_read_as_their_media():
    one = reading("job_j01.json")
    assert [m.mime for m in one.media] == ["image/webp"] and one.answered and not one.busy and one.user
    assert one.media[0].ref.startswith("workspace/imagine_media/media-generation-generate-one-image-a-red-fox")
    video = reading("job_j03.json", "video")
    assert [m.mime for m in video.media] == ["video/mp4"] and video.other == 0
    four = reading("job_j11.json")
    assert len(four.media) == 4 and len({m.ref for m in four.media}) == 4
    two = reading("history_j06.json")
    assert len(two.media) == 2, "two images, one card each"
    i2v = reading("job_j10.json", "video")
    assert len(i2v.media) == 1 and i2v.media[0].ref.endswith(".mp4")


@mutation(CONSTANTS_FILE, MEDIA_DIRS, 'MEDIA_DIRS = ("workspace/imagine_media/",)', expect="a your_files video")
def test_a_video_muse_saved_in_your_files_is_its_result_not_a_refusal():
    r = reading(YOUR_FILES, "video")
    assert [m.ref for m in r.media] == ["workspace/your_files/ocean_waves_beach_5s.mp4"], "a your_files video"
    assert r.media[0].mime == "video/mp4" and r.other == 0 and r.answered and "sandbox://" in r.text
    assert verdict(r, 1, settled=True) == "done"
    (out,) = outputs(r, 1, SID, job_id="job-1", duration=5, ratio="16:9")
    assert out.ref == "workspace/your_files/ocean_waves_beach_5s.mp4" and out.mime == "video/mp4"
    as_image = reading(YOUR_FILES, "image")
    assert as_image.media == () and as_image.other == 1, "a video is never an image's result"


@mutation(CONSTANTS_FILE, MEDIA_DIRS, 'MEDIA_DIRS = ("workspace/imagine_media/",)', expect="an image in your_files")
def test_an_image_in_your_files_is_a_result_too_and_the_folders_are_the_setting():
    """Live 30/10 (job aae305b4): muse saved an edit's image in workspace/your_files."""
    image_card = history("job_j01.json", SID)
    item = next(e for e in image_card if e["event_name"] == "delta.presentation")["payload"]["data"]["images"][0]
    item["variants"]["original"] = "https://x.metaaivm.com/media/raw/workspace/your_files/a-fox-0-1a2b.webp"
    r = read_history(image_card, "image")
    assert [m.ref for m in r.media] == ["workspace/your_files/a-fox-0-1a2b.webp"], "an image in your_files"
    assert is_media_ref("workspace/your_files/a.webp", "image/webp")
    assert is_media_ref("workspace/imagine_media/a.mp4", "video/mp4"), "a 26/10 video in imagine_media"
    only = ("workspace/imagine_media/",)
    assert read_history(image_card, "image", only).media == ()
    assert not is_media_ref("workspace/your_files/a.webp", "image/webp", only)


def test_the_refusal_is_an_answer_in_words_with_no_media():
    r = reading("job_j07.json")
    assert (r.media, r.other, r.answered, r.busy) == ((), 0, True, False)
    assert r.text.startswith("I can't make a photorealistic image of Taylor Swift and Elon Musk")
    assert verdict(r, 1, settled=True) == "refused"
    assert verdict(r, 1, settled=False) is None


@mutation(CHAT_FILE, '            elif (payload.get("metadata") or {}).get("message_kind") == "message":',
          "            elif True:", expect="a progress line is no answer")
def test_a_commentary_line_is_not_an_answer():
    events = history("history_j02.json", SID)
    commentary_only = [e for e in events if e["event_name"] == "message.user"
                       or (e.get("payload") or {}).get("metadata", {}).get("message_kind") == "commentary"]
    r = read_history(commentary_only, "image")
    assert not r.answered and verdict(r, 1, settled=True) is None, "a progress line is no answer"
    assert verdict(read_history(events, "image"), 1, settled=False) == "done"


@mutation(CHAT_FILE, "    if len(r.media) >= want:\n        return \"done\"", "    if False:\n        return \"done\"",
          expect="all asked are there")
@mutation(CHAT_FILE, "    if not settled or r.busy:\n        return None", "    if r.busy:\n        return None",
          expect="unsettled")
@mutation(CHAT_FILE, '    return "refused" if r.answered else None', "    return None", expect="refused when settled")
def test_the_verdict_waits_for_all_asked_or_a_settled_chat():
    two = reading("history_j06.json")
    assert verdict(two, 2, settled=False) == "done", "all asked are there"
    first_card = read_history(history("history_j06.json", SID)[:3], "image")
    assert len(first_card.media) == 1
    assert verdict(first_card, 2, settled=False) is None, "unsettled"
    assert verdict(first_card, 2, settled=True) == "done"
    refused = reading("job_j07.json")
    assert verdict(refused, 1, settled=True) == "refused", "refused when settled"
    busy = refused._replace(busy=True)
    assert verdict(busy, 1, settled=True) is None and verdict(first_card._replace(busy=True), 2, True) is None
    empty = read_history([], "image")
    assert verdict(empty, 1, settled=True) is None


@mutation(CHAT_FILE, "    kept = r.media[:want]\n", "    kept = r.media\n", expect="never more than paid")
@mutation(CHAT_FILE, "**({LAST: True} if i == len(kept) - 1 else {})", "**({LAST: True} if i == 0 else {})",
          expect="the last output deletes the chat")
def test_no_more_outputs_than_paid_are_delivered():
    four = reading("job_j11.json")
    outs = outputs(four, 2, SID)
    assert [o.idx for o in outs] == [0, 1], "never more than paid"
    assert outs[0].ref == four.media[0].ref and outs[0].meta == {"session_id": SID}, "the last output deletes the chat"
    assert outs[1].meta == {"session_id": SID, "last": True}


@mutation(CHAT_FILE, '                    if of == kind and found.mime.startswith(kind + "/"):',
          "                    if True:", expect="the other kind")
def test_media_of_the_other_kind_a_missing_file_or_a_strange_path_is_never_delivered():
    image_card = history("job_j01.json", SID)
    as_video = read_history(image_card, "video")
    assert as_video.media == () and as_video.other == 1, "the other kind"
    assert verdict(as_video, 1, settled=True) == "refused"
    card = next(e for e in image_card if e["event_name"] == "delta.presentation")
    item = card["payload"]["data"]["images"][0]
    item["missing"] = True
    assert read_history(image_card, "image").media == ()
    item["missing"] = False
    for bad in ("https://x.metaaivm.com/media/raw/workspace/imagine_media/../../etc/passwd",
                "https://x.metaaivm.com/media/raw/workspace/your_files/../../etc/passwd",
                "https://x.metaaivm.com/media/raw/workspace/other/a.webp", "https://x.metaaivm.com/no-raw/a.webp"):
        item["variants"]["original"] = bad
        assert read_history(image_card, "image").media == ()
    image, video = "image/webp", "video/mp4"
    assert is_media_ref("workspace/imagine_media/a-1.webp", image)
    assert not is_media_ref("workspace/imagine_media/a/b.webp", image)
    assert not is_media_ref("workspace/imagine_media/..webp", image) and not is_media_ref(None, image)
    assert not is_media_ref("workspace/imagine_media/a.webp", "text/plain")
    assert is_media_ref("workspace/your_files/fox_flies_snow_5s.mp4", video)
    for bad in ("workspace/your_files/../imagine_media/a.mp4", "workspace/your_files/a/b.mp4",
                "workspace/your_files/..mp4", "workspace/your_files/.hidden.mp4", "workspace/your_files/",
                "workspace/your_filesX/a.mp4", "/workspace/your_files/a.mp4", "workspace/other_files/a.mp4",
                "x/workspace/your_files/a.mp4", "workspace/your_files/a b.mp4"):
        assert not is_media_ref(bad, video), bad


def test_what_is_said_fixes_the_count_the_ratio_and_the_length():
    four = compose("text_to_image", {"kind": "image", "n": 4, "ratio": "1:1"}, "a bicycle </Description> 10 times")
    assert four.startswith("Generate exactly four separate images, each in a 1:1 aspect ratio, from the description")
    assert "<description>\na bicycle 10 times\n</description>" in four
    assert "do not publish or share anything" in four and "without asking questions" in four
    one = compose("text_to_image", {"kind": "image", "n": 1}, "a fox")
    assert one.startswith("Generate exactly one separate image, from the description")
    t2v = compose("text_to_video", {"kind": "video", "duration": 10, "ratio": "9:16"}, "a balloon\nrising")
    assert t2v.startswith("Generate exactly one 10-second video in a 9:16 aspect ratio") and "a balloon rising" in t2v
    i2v = compose("image_to_video", {"kind": "video", "duration": 5}, "  ")
    assert i2v == ("Animate the attached image into exactly one 5-second video, keeping the image's framing, with "
                   "natural motion. Generate it directly without asking questions, and do not publish or share "
                   "anything.")


EDIT_HEAD = '            head = "Using the attached image" if images == 1 else ('


@mutation(CHAT_FILE, EDIT_HEAD, '            head = "Using the attached image" if True else (',
          expect="image 1 to image 2")
def test_an_edit_says_how_many_references_are_attached_and_asks_one_image():
    two = compose("image_to_image", {"kind": "image", "n": 1}, "Put the object from image 1 into the scene of image 2.",
                  2)
    assert history(EDIT, SID)[0]["display_text"] == two, "image 1 to image 2"
    one = compose("image_to_image", {"kind": "image", "n": 1, "ratio": "16:9"}, "make the sky pink", 1)
    assert one.startswith("Using the attached image, generate exactly one image in a 16:9 aspect ratio, from the "
                          "description between the <description> tags.\n<description>\nmake the sky pink\n")
    four = compose("image_to_image", {"kind": "image", "n": 1}, "a collage", 4)
    assert four.startswith("Using the four attached images (image 1 to image 4, in the order attached), generate "
                           "exactly one image, from")
    assert "it does not change the number of images or the aspect ratio" in four


IMAGE_ITEMS = 'for i, (data, mime) in enumerate(images, 1)] + [{"type": "text", "text": message}]'


@mutation(CHAT_FILE, IMAGE_ITEMS, 'for i, (data, mime) in enumerate(images, 1)][::-1] + '
          '[{"type": "text", "text": message}]', expect="in the order sent")
@mutation(CHAT_FILE, IMAGE_ITEMS, 'for i, (data, mime) in enumerate(images, 1)]', expect="the text last")
def test_the_stream_body_opens_a_new_side_chat_and_carries_its_images_inline_before_the_text():
    body = stream_body("edit", "node-1", SID, [(b"one", "image/jpeg"), (b"two", "image/png"), (b"3", "image/webp")])
    assert body["items"][-1] == {"type": "text", "text": "edit"}, "the text last"
    assert [(i["type"], base64.b64decode(i["data_base64"]), i["mime_type"], i["filename"]) for i in body["items"][:3]] \
        == [("image", b"one", "image/jpeg", "image1.jpg"), ("image", b"two", "image/png", "image2.png"),
            ("image", b"3", "image/webp", "image3.webp")], "in the order sent"
    assert "message" not in body and body["model"] == "auto/auto"
    body = stream_body("hello", "node-1", SID)
    assert body["message"] == "hello" and body["session_id"] == SID and body["node_id"] == "node-1"
    assert body["metadata"] == {"thread_is_dictation_used": False} and "items" not in body
    body = stream_body("animate", "node-1", SID, [(b"\x00webp", "image/webp")])
    image, text = body["items"]
    assert image == {"type": "image", "mime_type": "image/webp", "data_base64": base64.b64encode(b"\x00webp").decode(),
                     "filename": "image1.webp"}
    assert text == {"type": "text", "text": "animate"} and body["model"] == "auto/auto" and "message" not in body


def test_pending_approvals_are_found_in_their_events_once():
    events = [{"event": "approvals.snapshot", "payload": {"approvals": [
                  {"approval_id": "ap-1", "status": "pending", "message_revision": 3},
                  {"approval_id": "ap-2", "status": "denied"}]}},
              {"event_name": "approval.requested", "payload": {"approval_id": "ap-3"}},
              {"event": "approvals.snapshot", "payload": {"pending": [{"id": "ap-1"}, {"id": "bad id!"}]}},
              {"event": "agent.status", "payload": {"approval_id": "ap-9"}}]
    assert pending_approvals(events) == [("ap-1", 3), ("ap-3", None)]


METER = "        used = made if pool == kind else -(-made * total // scale[kind])"


@mutation(CHAT_FILE, METER, "        used = made", expect="a video is ten images' worth")
def test_a_done_job_lowers_both_pools_by_its_share_of_the_one_meter():
    from datetime import UTC, datetime

    from musekit import settings

    from relay_core.providers import Balance
    from relay_muse.engine.chat import meter_effects
    week = datetime(2026, 11, 2, 6, 44, tzinfo=UTC)
    account = SimpleNamespace(capacity={"image": 300, "video": 30}, capacity_until={"image": week, "video": week})
    video = meter_effects(account, settings(), "video", 1)
    assert video == (Balance("image", 290, week), Balance("video", 29, week)), "a video is ten images' worth"
    assert meter_effects(account, settings(), "image", 4) == (Balance("image", 296, week), Balance("video", 29, week))
    assert meter_effects(account, settings(), "image", 6)[1] == Balance("video", 29, week), "a part rounds up"
    low = SimpleNamespace(capacity={"image": 3, "video": None}, capacity_until={})
    assert meter_effects(low, settings(), "video", 1) == (Balance("image", 0, None),), "never below 0; unknown kept"
    assert meter_effects(SimpleNamespace(), settings(), "image", 1) == ()


def test_more_or_fewer_results_than_paid_are_noted_with_the_job_id(caplog):
    from relay_muse.engine.chat import made_note
    four = reading("job_j11.json")
    with caplog.at_level("WARNING"):
        assert made_note(four, 2, "image", "job-9") == ("muse made 4 images, 2 were paid: the first 2 are "
                                                         "delivered, the rest are not")
        assert made_note(four, 4, "image", "job-9") == "muse made the 4 image(s) asked"
        assert "fewer" not in made_note(four, 5, "image", "job-9") and "made 4 of the 5" in made_note(
            four, 5, "image", "job-9")
    assert [r.getMessage()[:14] for r in caplog.records] == ["muse job job-9"] * 3


NO_VIDEO_HERE = "region-301030/history_video_unavailable.json"
# muse answers in the prompt's language: the same answer as muse would write it in other languages (not seen live)
NO_VIDEO_HERE_ELSEWHERE = (
    "视频生成在你所在的国家或地区不可用，这是账号的长期限制，重试也没有用。",
    "お住まいの国や地域では動画生成をご利用いただけません。再試行しても変わりません。",
    "La generación de video no está disponible en tu país o región, y reintentarlo no cambiará nada.",
    "La génération de vidéo n’est pas disponible dans votre pays ou région.",
    "Die Videoerstellung ist in deinem Land oder deiner Region nicht verfügbar.",
    "해당 국가 또는 지역에서는 동영상 생성을 사용할 수 없습니다.",
    "Создание видео недоступно в вашей стране или регионе.",
    "A geração de vídeo não está disponível no seu país ou região.",
    "Pembuatan video tidak tersedia di negara atau wilayah Anda.",
    "I can’t make the video — video generation isn’t available for your account in your country or region.",
)
# answers that are NOT the account's restriction: a refusal of the prompt, a done video, a question
NOT_THE_ACCOUNT = (
    "I can't make a video of real public figures like that. Want a stylised version instead?",
    "Done — one 5-second video at 16:9 (1280×720), not published or shared.",
    "Video không tạo được vì nội dung vi phạm chính sách. Bạn có muốn đổi mô tả không?",
    "Which region of the coast should the video show: the north or the south?",
    "I can't generate that — it isn't available for this prompt in any region of my tools.",
    "Video về các quốc gia châu Âu đã xong.",
)
# refusals of the CONTENT that name a video, a country or region and a restriction: the generation is not what is
# unavailable, or the place is not the user's own (Codex 30/10)
CONTENT_REFUSALS = (
    "This video is blocked in your country.",
    "I can't make a video of that country's flag.",
    "A video of real people isn't allowed in your region.",
    "I can't generate videos of this country's leader; it's restricted content.",
    "Video generation of real people is restricted.",
    "Video này bị chặn ở quốc gia của bạn, nên mình không gửi được.",
    "Mình không thể tạo video về lá cờ của quốc gia đó vì nội dung bị hạn chế.",
    "该视频在你所在的国家被限制播放。",
)
GENERATION = '    "video generation", "generate video", "generate videos",'
SCOPE = '    "your account", "this account", "the account", "your country", "your region",'


@mutation(CHAT_FILE, "    return all(p.search(said) for p in _ACCOUNT_NO_VIDEO)",
          "    return any(p.search(said) for p in _ACCOUNT_NO_VIDEO)", expect="not the account's restriction")
@mutation(CHAT_FILE, '        alts.append(r"(?<!\\w)" + spaced if _bounded(word) else spaced)',
          "        alts.append(spaced)", expect="inside a word")
@mutation(CHAT_FILE, GENERATION, '    "video", "视频", "video generation", "generate video", "generate videos",',
          expect="a content refusal")
@mutation(CHAT_FILE, SCOPE, '    "country", "region", "quốc gia", "the account", "your country", "your region",',
          expect="a content refusal")
def test_muse_saying_the_account_makes_no_video_where_it_is_is_told_from_a_refusal_of_the_prompt():
    live = spike(NO_VIDEO_HERE)["answers"]
    assert len(live) == 5 and {a["language"] for a in live} == {"en", "vi"}
    for answer in live:
        assert video_unavailable_here(answer["text"]), answer["job"]
    for text in NO_VIDEO_HERE_ELSEWHERE:
        assert video_unavailable_here(text), text
        assert video_unavailable_here(text.upper()), text
    for text in NOT_THE_ACCOUNT:
        assert not video_unavailable_here(text), "not the account's restriction: %s" % text
    assert not video_unavailable_here(reading("job_j07.json").text)
    assert not video_unavailable_here(reading(YOUR_FILES, "video").text)
    assert not video_unavailable_here("")
    for text in CONTENT_REFUSALS:
        assert not video_unavailable_here(text), "a content refusal: %s" % text
    assert not video_unavailable_here("Video generation in your country is unrestricted."), "inside a word"
    r = reading(NO_VIDEO_HERE, "video")
    assert (r.media, r.other, r.answered, r.busy, r.user) == ((), 0, True, False, True)
    assert verdict(r, 1, settled=True) == "refused" and video_unavailable_here(r.text), "nothing was made"


# ---- only files whose names were not there before the send ---------------------------------------------------------
OLD_CARD = "             if m.ref not in before and m.ref in now]"
NEW_ONLY = ("    new = sorted((f for f in files if f.ref not in before and f.mime.startswith(kind + \"/\")), "
            "key=lambda f: -f.ms)")
BY_TIME = ("    new = sorted((f for f in files if f.ms > 250 and f.mime.startswith(kind + \"/\")), "
           "key=lambda f: -f.ms)")
OLDEST_FIRST = ("    new = sorted((f for f in files if f.ref not in before and f.mime.startswith(kind + \"/\")), "
                "key=lambda f: f.ms)")
UNSHOWN = "        if settled and not r.busy and unshown else []"
UNSHOWN_ALWAYS = "        if True else []"


@mutation(CHAT_FILE, OLD_CARD, "             if m.ref in now]", expect="an old file shown in a card")
@mutation(CHAT_FILE, OLD_CARD, "             if m.ref not in before]", expect="a card not listed now")
@mutation(CHAT_FILE, NEW_ONLY, BY_TIME, expect="an old file rewritten since")
@mutation(CHAT_FILE, NEW_ONLY, OLDEST_FIRST, expect="newest first")
@mutation(CHAT_FILE, UNSHOWN, UNSHOWN_ALWAYS, expect="only once settled")
def test_only_files_whose_names_were_not_there_count_cards_first_then_the_unshown_newest_first():
    old = File("workspace/your_files/ocean_waves_beach_5s.mp4", "video/mp4", 100)
    raw = File("workspace/your_files/media-generation-boat-0-1a2b.mp4", "video/mp4", 300)
    cut = File("workspace/your_files/paper_boat_5s.mp4", "video/mp4", 400)
    image = File("workspace/your_files/a-fox-0-1a2b.webp", "image/webp", 500)
    shown = reading(YOUR_FILES, "video")          # a card for ocean_waves_beach_5s.mp4
    before = frozenset({old.ref})
    got = fresh(shown, [old, raw, cut, image], before, "video", True).media
    assert old.ref not in [m.ref for m in got], "an old file shown in a card is never a result"
    assert [m.ref for m in got] == [cut.ref, raw.ref], "newest first"
    assert all(m.mime == "video/mp4" for m in got)
    rewritten = fresh(shown, [old._replace(ms=900), cut], before, "video", True).media
    assert [m.ref for m in rewritten] == [cut.ref], "an old file rewritten since the send is never a result"
    new_card = fresh(shown, [old, cut], frozenset(), "video", True).media
    assert [m.ref for m in new_card] == [old.ref, cut.ref], "the card first"
    assert fresh(shown, [old, cut], before, "video", False).media == (), "only once settled"
    assert fresh(shown._replace(busy=True), [old, cut], before, "video", True).media == (), "only once settled"
    assert fresh(shown, [old, cut], frozenset(), "video", False).media == (Media(old.ref, "video/mp4", (100, -1)),)
    assert fresh(shown, [cut], frozenset(), "video", True).media == (Media(cut.ref, "video/mp4", (400, -1)),), \
        "a card not listed now is no result"
    assert fresh(shown, [old, cut], frozenset(), "video", True, unshown=False).media == (
        Media(old.ref, "video/mp4", (100, -1)),), "only once settled, and only when no other chat moved"
