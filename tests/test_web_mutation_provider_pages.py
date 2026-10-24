"""Mutation markers of the provider redesign of API keys, Request logs, Alerts, Prompt frames and Test, and of the
provider strip's modes: each breaks one rule in the TypeScript source and demands that the named Vitest test turns red
(scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
STRIP = SRC + "/components/layout/ProviderStrip.vue"
KEY_MODEL = SRC + "/components/keys/key-model.ts"
KEYS = SRC + "/views/KeysView.vue"
LOGS_TABLE = SRC + "/components/logs/LogsTable.vue"
ALERTS = SRC + "/views/AlertsView.vue"
FRAMES_VIEW = SRC + "/views/FramesView.vue"
FRAMES_MODEL = SRC + "/components/frames/frames-model.ts"
FRAME_MODELS = SRC + "/components/frames/FrameModels.vue"
TEST_VIEW = SRC + "/views/TestView.vue"

STRIP_SPEC = "tests/unit/provider-strip.spec.ts"
KEY_SPEC = "tests/unit/keys/key-model.spec.ts"
KEYS_SPEC = "tests/unit/keys/keys-view.spec.ts"
LOGS_SPEC = "tests/unit/logs/logs-view.spec.ts"
ALERTS_SPEC = "tests/unit/alerts/alerts-view.spec.ts"
FRAMES_SPEC = "tests/unit/frames/frames-view.spec.ts"
TEST_SPEC = "tests/unit/test/test-view.spec.ts"

ONLY = (f"FAILED {STRIP_SPEC} > the provider strip > for a one-provider page lists the providers alone, picks `only` "
        "(not the filter) and is off while locked")
GROUPS = f"FAILED {KEY_SPEC} > modelGroups > "
KEYS_STRIP = f"FAILED {KEYS_SPEC} > the provider strip > "
LOGS_PROVIDER = f"FAILED {LOGS_SPEC} > the provider of a request > "
ALERTS_STRIP = f"FAILED {ALERTS_SPEC} > the provider strip > "
FRAMES = f"FAILED {FRAMES_SPEC} > "
TEST_STRIP = f"FAILED {TEST_SPEC} > the provider strip > "


@mutation(STRIP, "    scope.only = name;\n    return;", "    return;", expect=ONLY)
@mutation(STRIP, ':disabled="mode === \'only\' && scope.stripLocked"', ':disabled="false"', expect=ONLY)
@pytest.mark.web_unit
def test_the_strip_of_a_one_provider_page():
    vitest(STRIP_SPEC)


@mutation(KEY_MODEL, "if (only && provider !== only) continue;", "if (false) continue;",
          expect=GROUPS + "keeps one provider's group when asked, and has none for any model")
@mutation(KEY_MODEL, "paused: m?.disabled === true", "paused: false",
          expect=GROUPS + "groups by provider in the drawn order, finds aliases, marks paused, and puts unknown names "
                          "last")
@mutation(KEYS, "scope.provider ? items.value.filter((k) => keyReaches(k, scope.provider, known.value)) : items.value",
          "items.value",
          expect=KEYS_STRIP + "a provider shows the keys that may use its models, only its models, the points "
                              "wallet and its cap; paused is greyed")
@pytest.mark.web_unit
def test_the_keys_page_by_provider():
    vitest(KEY_SPEC, KEYS_SPEC)


@mutation(LOGS_TABLE,
          '<span v-if="r.provider" class="pchip" :class="providerClass(r.provider)">{{ r.provider }}</span>',
          '<span class="pchip" :class="providerClass(r.provider ?? \'\')">{{ r.provider }}</span>',
          expect=LOGS_PROVIDER + "a phone row has the chip too, and none when there is no provider")
@pytest.mark.web_unit
def test_the_logs_page_by_provider():
    vitest(LOGS_SPEC)


@mutation(ALERTS, "scope.provider ? groups.value.filter((g) => g.provider === scope.provider) : groups.value",
          "groups.value",
          expect=ALERTS_STRIP + "a provider keeps its own groups and live conditions, and the count says so")
@mutation(ALERTS, 'const providerOf = (p: string | null | undefined): string => p || "relay";',
          'const providerOf = (p: string | null | undefined): string => p || "";',
          expect=ALERTS_STRIP + "the rows and live conditions carry their provider tile, none is the relay")
@pytest.mark.web_unit
def test_the_alerts_page_by_provider():
    vitest(ALERTS_SPEC)


@mutation(FRAMES_MODEL, r"marker: /^\{\{\w+\}\}$/.test(part)", "marker: false",
          expect=FRAMES + "the editor > picks the markers out of the frame, and says whether each is in once")
@mutation(FRAME_MODELS, "m.owned_by === props.frames.listed && m.disabled === true",
          "m.owned_by === props.frames.listed",
          expect=FRAMES + "the models of the kind > writes a model's name under its id only when it differs, "
                          "and marks a model the admin paused")
@pytest.mark.web_unit
def test_the_frames_page_by_provider():
    vitest(FRAMES_SPEC)


@mutation(TEST_VIEW, "m.owned_by === provider.value && m.disabled === true &&", "m.owned_by === provider.value &&",
          expect=TEST_STRIP + "starts on the first provider in the drawn order that has a model, shows its models "
                              "only, and a paused one is a disabled chip")
@mutation(TEST_VIEW, "if (first) void pick(first.model);", "if (first) void first;",
          expect=TEST_STRIP + "another provider shows its models and picks its first; a provider the key has none of "
                              "says so")
@pytest.mark.web_unit
def test_the_test_page_by_provider():
    vitest(TEST_SPEC)
