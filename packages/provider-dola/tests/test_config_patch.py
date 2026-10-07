"""The action-bar config patch (ported from v4 `config_patch._demo`, coverage CPATCH00/CPATCH01) and the response patch
built on it. Pure: a JSON string in, a JSON string out; the real captured config is the fixture."""

import json
from pathlib import Path

from relay_core.testing.mutation import mutation
from relay_dola.engine.config_patch import (
    CONFIG_URL_PART,
    _make_option,
    find_duration_options,
    patch_duration_options,
)
from relay_dola.engine.site import duration_response_patch

CP = "packages/provider-dola/src/relay_dola/engine/config_patch.py"
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "dola" / "action_bar_conf.json"
RAW = FIXTURE.read_text(encoding="utf-8")


def slider(**kw):
    """The NEW composer's config as dola ships it: a slider selector nested two JSON strings deep."""
    sel = dict({"id": 5, "key": "video-duration", "lower_bound": 4, "upper_bound": 10, "step_length": 1,
                "tag_starting_duration": 11}, **kw)
    inner = {"configs": {"1": {"selectors": [{"id": 1, "template": "{}"}, {"id": 5, "template": json.dumps(sel)}]}}}
    return json.dumps({"data": {"selector_list": [{"template": json.dumps(inner)}]}})


def read_slider(raw):
    inner = json.loads(json.loads(raw)["data"]["selector_list"][0]["template"])
    return json.loads(inner["configs"]["1"]["selectors"][1]["template"])


# ---- the old composer: a list of levels ---------------------------------------------------------------------------
def test_the_captured_config_offers_only_5_and_10():
    # if dola changes this the fixture is stale and every expectation below is about a config that no longer exists
    assert find_duration_options(RAW) == [["5", "10"]], "the captured config must offer 5 and 10 only"


@mutation(CP, "options.insert(after + 1, _make_option(options, seconds))", "pass", expect="the patch added no level")
def test_the_old_config_gets_a_30_second_level():
    patched, changed = patch_duration_options(RAW, 30)
    assert changed, "the patch reports it did not patch"
    assert find_duration_options(patched) == [["5", "10", "30"]], "the patch added no level"
    assert json.loads(patched)["data"]["item_list"], "the patch lost the config's own structure"


@mutation(CP, "            if not already:\n                # Insert", "            if True:\n                # Insert",
          expect="re-patch added a duplicate")
def test_a_second_patch_adds_nothing():
    patched, _ = patch_duration_options(RAW, 30)
    twice, changed_again = patch_duration_options(patched, 30)
    assert not changed_again, "re-patch added a duplicate (it reports a change)"
    assert find_duration_options(twice) == [["5", "10", "30"]], "re-patch added a duplicate level"


def test_the_new_option_has_the_shape_dola_uses():
    option = _make_option([{"id": 1, "option_key": "5"}, {"id": 2, "option_key": "10"}], 30)
    assert option == {"id": 3, "display_text": "30s", "message_text": "", "option_key": "30"}, option


def test_the_level_lands_right_after_10_so_the_dropdown_stays_ascending():
    config = json.dumps({"x": {"key": "video-duration", "option_list": [
        {"id": 1, "option_key": "5"}, {"id": 2, "option_key": "10"}, {"id": 3, "option_key": "60"}]}})
    assert find_duration_options(patch_duration_options(config, 30)[0]) == [["5", "10", "30", "60"]]


def test_a_config_with_no_selector_comes_back_unchanged_and_reports_failure():
    unrelated = '{"data":{"nothing":"here"}}'
    assert patch_duration_options(unrelated, 30) == (unrelated, False), "no selector must be a reported failure"


def test_broken_json_is_a_reported_failure_not_an_exception():
    assert patch_duration_options("{not json}", 30) == ("{not json}", False)
    assert patch_duration_options(None, 30)[1] is False


@mutation(CP, '    if str(node.get("key")) == DURATION_SELECTOR_KEY:\n        return True\n    options =',
          '    if str(node.get("key")) == DURATION_SELECTOR_KEY:\n        return True\n    return False\n    options =',
          expect="renamed selector")
def test_a_renamed_selector_is_still_recognised_by_its_options():
    renamed = json.dumps({"x": {"key": "renamed-by-dola", "option_list": [
        {"id": 1, "option_key": "5"}, {"id": 2, "option_key": "10"}]}})
    assert patch_duration_options(renamed, 30)[1], "renamed selector was not recognised: it would fail silently"


# ---- the new composer: a slider ------------------------------------------------------------------------------------
@mutation(CP, '            if upper < seconds:\n                out["upper_bound"] = seconds',
          '            if upper < seconds:\n                pass', expect="slider ceiling")
def test_the_slider_ceiling_rises_to_30():
    patched, ok = patch_duration_options(slider(), 30)
    got = read_slider(patched)
    assert ok and got["upper_bound"] == 30, "slider ceiling was not raised to 30: %r" % got


@mutation(CP, '                if int(out.get("tag_starting_duration", seconds + 1)) <= seconds:\n'
              '                    out["tag_starting_duration"] = seconds + 1',
          '                if False:\n                    out["tag_starting_duration"] = seconds + 1',
          expect="paid tier")
def test_the_paid_tier_mark_moves_past_30():
    got = read_slider(patch_duration_options(slider(), 30)[0])
    assert got["tag_starting_duration"] == 31, (
        "the paid tier mark stays under 30 s (a click opens the buy page): %r" % got
    )


@mutation(CP, "            return out, changed or lower <= seconds", "            return out, changed",
          expect="7 s is inside the slider")
def test_a_level_inside_the_slider_reports_patched_without_changing_the_ceiling():
    patched, ok = patch_duration_options(slider(), 7)
    assert ok and read_slider(patched)["upper_bound"] == 10, "7 s is inside the slider: it must report patched"


def test_a_level_below_the_slider_floor_is_not_patched():
    assert not patch_duration_options(slider(), 3)[1], "3 s is under the 4 s floor but reported patched"


# ---- the response patch the browser applies --------------------------------------------------------------------------
def test_the_response_patch_targets_the_config_request_and_returns_the_kit_pair():
    patch = duration_response_patch(30)
    assert patch.url_pattern == "*%s*" % CONFIG_URL_PART == "*action_bar_v3/get_item_conf*"
    body, changed = patch.apply(RAW)
    assert changed and find_duration_options(body) == [["5", "10", "30"]]
    assert patch.apply('{"unrelated": 1}') == ('{"unrelated": 1}', False)
