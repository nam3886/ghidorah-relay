<script setup lang="ts">
import { computed, ref } from "vue";
import type { SettingItem } from "@/api/settings";
import type { CatalogModel, ProviderSummary } from "@/api/shell";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import ToggleSwitch from "@/components/shared/ToggleSwitch.vue";
import { providerClass } from "@/lib/providers";
import ModelPrices from "./ModelPrices.vue";
import PoolsEditor from "./PoolsEditor.vue";
import SettingField from "./SettingField.vue";
import SettingsCard from "./SettingsCard.vue";
import { type Card, fieldLabel } from "./settings-cards";
import { useStacked } from "./settings-media";
import { valueText } from "./settings-value";
import { useBreakpoint } from "@/composables/use-breakpoint";

// One provider's tab of the Settings page (B Ledger): its line (how it sends, its accounts, today's jobs), then the
// sections: Taking jobs (`<p>.claims_enabled` and the provider's basic settings), Send path (`<p>.send_path`), Models
// and prices, Pools and budgets (`<p>.pools`) and the rest under Advanced. Every control edits a draft of the page; the
// page's bar saves them.
const props = defineProps<{
  card: Card;
  drafts: Record<string, string>;
  errors: Record<string, string>;
  models: CatalogModel[];
  summary: ProviderSummary | null;
  busy: boolean;
}>();
const emit = defineEmits<{ draft: [key: string, text: string]; reset: [key: string]; accounts: [] }>();

// Below 968 px (SettingsTablet, SettingsPhone) each title sits inside its card with the board's few words beside it,
// the provider's line is plain text (none on a phone), the claim switch tells its state in words, the send path is
// one full-width choice, and Advanced is a button. Nothing is dropped that edits: only words and pictures go.
const stacked = useStacked();
const { isPhone } = useBreakpoint();
const p = computed(() => props.card.group);
const own = computed(() => props.card.own);
const isEdited = (item: SettingItem | undefined): boolean =>
  !!item && props.drafts[item.key] !== undefined && props.drafts[item.key] !== valueText(item);

const claims = computed(() => own.value.claims_enabled);
const taking = computed(() => (claims.value ? props.drafts[claims.value.key] === "true" : true));
// the banner speaks of what is stored: claims are off now, whatever the draft says
const storedOff = computed(() => claims.value?.value === false);

const line = computed(() => {
  const s = props.summary;
  if (!s) return "";
  const path = s.execution === "http" ? "HTTP path" : "Browser path";
  return `${path} · ${s.accounts.total} accounts, ${s.accounts.usable} usable · ${s.done_today.toLocaleString("en-US")} jobs today`;
});

// Send path: the two choices, what each covers
const sendPath = computed(() => own.value.send_path);
const pathNow = computed(() => (sendPath.value ? props.drafts[sendPath.value.key] : ""));
const PATHS = [
  {
    id: "browser",
    label: "Browser",
    icon: [
      "M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
      "M3 9h18",
      "M7 6.5h.01",
      "M10 6.5h.01",
    ],
    text: "One Chrome profile per account. Takes every job.",
    pill: "",
  },
  {
    id: "http",
    label: "HTTP",
    icon: ["m8 7-5 5 5 5", "m16 7 5 5-5 5", "m14 4-4 16"],
    text: "Signed calls, no Chrome. A job it cannot take still goes by browser.",
    pill: "Beta",
  },
];
const INFO = ["M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z", "M12 11v5", "M12 8h.01"];
const ALERT = [
  "M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z",
  "M12 9v4",
  "M12 17h.01",
];

const hasModels = computed(
  () => props.models.length > 0 && !!(own.value.prices || own.value.costs || own.value.disabled_models),
);
const units = computed(() => Object.fromEntries((props.summary?.pools ?? []).map((x) => [x.pool, x.unit])));

const advOpen = ref(false);
// the stacked card's few words: the first three names ("poll, back-off, timeouts" on the board)
const advShort = computed(() =>
  props.card.advanced
    .slice(0, 3)
    .map((i) =>
      fieldLabel(i)
        .replace(/ \(.*\)$/, "")
        .toLowerCase(),
    )
    .join(", "),
);
const advNames = computed(() => {
  const names = props.card.advanced.map((i) =>
    fieldLabel(i)
      .replace(/ \(.*\)$/, "")
      .toLowerCase(),
  );
  const text = names.slice(0, 5).join(", ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}${names.length > 5 ? ", …" : ""}`;
});
</script>

<template>
  <div class="ptab-body" :class="providerClass(p)" :data-testid="`settings-card-${p}`">
    <p v-if="stacked && !isPhone && line" class="pline pline--text num">{{ p }} · {{ line }}</p>
    <div v-else-if="!stacked" class="pline">
      <span class="pchip">{{ p }}</span>
      <span v-if="line" class="pline__text num">{{ line }}</span>
      <a href="#/accounts" class="pline__link" @click.prevent="emit('accounts')">Open {{ p }} accounts</a>
    </div>

    <div v-if="storedOff" class="banner" role="status">
      <PathIcon :paths="ALERT" :size="16" :stroke="2.2" />
      <span class="banner__text"
        >Taking jobs is off for {{ p }}. Prices and models can still be edited; nothing is claimed until it is back
        on.</span
      >
      <button
        v-if="!taking"
        type="button"
        class="banner__btn"
        :disabled="busy"
        @click="emit('draft', claims!.key, 'true')"
      >
        Turn claims on
      </button>
    </div>

    <SettingsCard
      v-if="claims || card.basic.length"
      :id="`${p}-taking`"
      title="Taking jobs"
      :note="`Turn off to stop ${p} claiming new jobs. Running jobs finish; queued jobs wait for you.`"
      :short="isPhone ? '' : 'Running jobs finish; queued jobs wait'"
    >
      <div v-if="claims" class="claims" :data-key="claims.key">
        <div class="claims__text">
          <span class="claims__name">Claim new jobs</span>
          <span v-if="stacked" class="help num"
            >{{ taking ? "Claiming" : "Paused"
            }}<template v-if="taking && summary"> · {{ summary.running }} in flight</template></span
          >
          <span v-else class="help">{{ p }} takes jobs from the queue while this is on.</span>
          <span v-if="errors[claims.key]" class="error" role="alert">{{ errors[claims.key] }}</span>
        </div>
        <span v-if="isEdited(claims)" class="changed">Changed</span>
        <span v-if="!stacked" class="pill" :class="taking ? 'pill--ok' : 'pill--warn'"
          >{{ taking ? "Claiming" : "Paused"
          }}<template v-if="taking && summary">
            · <span class="num">{{ summary.running }}</span> in flight</template
          ></span
        >
        <ToggleSwitch
          size="md"
          :model-value="taking"
          label="Claim new jobs"
          :disabled="busy"
          @change="(on: boolean) => emit('draft', claims!.key, String(on))"
        />
      </div>
      <SettingField
        v-for="item in card.basic"
        :key="item.key"
        layout="row"
        :item="item"
        :draft="drafts[item.key] ?? ''"
        :edited="isEdited(item)"
        :error="errors[item.key]"
        :busy="busy"
        @update:draft="(t: string) => emit('draft', item.key, t)"
        @reset="emit('reset', item.key)"
      />
    </SettingsCard>

    <SettingsCard
      v-if="sendPath"
      :id="`${p}-path`"
      title="Send path"
      :note="`How a job reaches ${p}. Both paths go through the account's own proxy.`"
      pad
    >
      <span v-if="stacked" class="seg-label">How a job reaches {{ p }}</span>
      <div role="group" aria-label="Send path" class="seg">
        <button
          v-for="x in PATHS"
          :key="x.id"
          type="button"
          class="seg__btn"
          :aria-pressed="pathNow === x.id"
          :disabled="busy"
          @click="emit('draft', sendPath!.key, x.id)"
        >
          <template v-if="stacked">{{ x.label }}{{ x.pill ? ` · ${x.pill.toLowerCase()}` : "" }}</template>
          <template v-else><PathIcon :paths="x.icon" :size="16" :stroke="2" />{{ x.label }}</template>
        </button>
      </div>
      <span v-if="stacked" class="help"
        >Both paths go through the account's own proxy. Running jobs keep theirs; new jobs use the new one.</span
      >
      <div v-if="!stacked" class="paths">
        <div v-for="x in PATHS" :key="x.id" class="paths__item">
          <span class="paths__name"
            >{{ x.label }}
            <span v-if="sendPath.value === x.id" class="pill pill--sm pill--brand">In use</span>
            <span v-else-if="x.pill" class="pill pill--sm pill--warn">{{ x.pill }}</span></span
          >
          <span class="help">{{ x.text }}</span>
        </div>
      </div>
      <div v-if="!stacked" class="notice">
        <PathIcon :paths="INFO" :size="16" :stroke="2" />
        <span>The path is read at each send: running jobs keep theirs, new jobs use the new one.</span>
      </div>
      <span v-if="errors[sendPath.key]" class="error" role="alert">{{ errors[sendPath.key] }}</span>
    </SettingsCard>

    <SettingsCard
      v-if="hasModels"
      :id="`${p}-models`"
      title="Models and prices"
      :note="`What a customer pays in points against what ${p} takes from the account. Margin counts 1 credit or 1 image use as 1 point.`"
      :short="isPhone ? 'one row per model' : 'tap a model to edit it'"
    >
      <ModelPrices
        :card="card"
        :models="models"
        :drafts="drafts"
        :errors="errors"
        :busy="busy"
        :units="units"
        @draft="(key: string, text: string) => emit('draft', key, text)"
        @reset="(key: string) => emit('reset', key)"
      />
    </SettingsCard>

    <SettingsCard
      v-if="own.pools"
      :id="`${p}-pools`"
      title="Pools and budgets"
      :note="`What one ${p} account may spend per day. Every pool refills at the reset time.`"
      pad
    >
      <PoolsEditor
        :item="own.pools"
        :draft="drafts[own.pools.key]"
        :error="errors[own.pools.key]"
        :busy="busy"
        :summary="summary"
        @draft="(text: string) => emit('draft', own.pools!.key, text)"
        @reset="emit('reset', own.pools!.key)"
      />
    </SettingsCard>

    <SettingsCard
      v-if="card.advanced.length"
      :id="`${p}-advanced`"
      title="Advanced"
      note="Timings and limits. The defaults suit most days."
      :short="isPhone ? '' : advShort"
      :headless="isPhone"
    >
      <!-- stacked (SettingsTablet, SettingsPhone): a plain button that shows them -->
      <div v-if="stacked" class="adv-btn-row">
        <button
          type="button"
          class="adv-btn"
          :aria-expanded="advOpen"
          :aria-controls="`${p}-advanced-fields`"
          @click="advOpen = !advOpen"
        >
          {{ advOpen ? "Hide" : "Show" }} {{ card.advanced.length }} advanced
          {{ card.advanced.length === 1 ? "setting" : "settings" }}
          <span v-if="card.advanced.some(isEdited)" class="tag">edited</span>
        </button>
      </div>
      <button
        v-else
        type="button"
        class="disclosure"
        :aria-expanded="advOpen"
        :aria-controls="`${p}-advanced-fields`"
        @click="advOpen = !advOpen"
      >
        <span class="disclosure__text">
          <span class="disclosure__title"
            >{{ advOpen ? "Hide" : "Show" }} {{ card.advanced.length }} advanced
            {{ card.advanced.length === 1 ? "setting" : "settings" }}
            <span v-if="card.advanced.some(isEdited)" class="tag">edited</span></span
          >
          <span class="help">{{ advNames }}</span>
        </span>
        <PathIcon
          class="disclosure__chev"
          :class="{ 'is-open': advOpen }"
          :paths="['m6 9 6 6 6-6']"
          :size="18"
          :stroke="2"
        />
      </button>
      <div v-show="advOpen" :id="`${p}-advanced-fields`" class="adv-fields">
        <SettingField
          v-for="item in card.advanced"
          :key="item.key"
          :item="item"
          :draft="drafts[item.key] ?? ''"
          :edited="isEdited(item)"
          :error="errors[item.key]"
          :busy="busy"
          @update:draft="(t: string) => emit('draft', item.key, t)"
          @reset="emit('reset', item.key)"
        />
      </div>
    </SettingsCard>
  </div>
</template>

<style scoped>
.ptab-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.pline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.pline--text {
  margin: 0;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.seg-label {
  margin-bottom: -10px;
  font-size: 13px;
  font-weight: 700;
}
.adv-btn-row {
  padding: 12px 20px 20px;
}
.adv-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 40px;
  padding: 0 16px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.adv-btn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.pchip {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  background: var(--ps);
  color: var(--pi);
}
.pline__text {
  color: var(--rl-muted);
}
.pline__link {
  margin-left: auto;
  font-weight: 600;
  text-decoration: none;
  color: var(--rl-link);
}
.pline__link:hover {
  color: var(--rl-link-hover);
}
.banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 19px;
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.banner__text {
  flex: 1 1 280px;
  min-width: 0;
}
.banner__btn {
  flex: none;
  height: 32px;
  padding: 0 12px;
  border: 1px solid currentColor;
  border-radius: 8px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}
.banner__btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.help {
  font-size: 12px;
  line-height: 18px;
  font-weight: 500;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.error {
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--rl-bad);
  overflow-wrap: anywhere;
}
.num {
  font-variant-numeric: tabular-nums;
}
.changed {
  flex: none;
  font-size: 12px;
  font-weight: 700;
  color: var(--rl-brand-ink);
}
.tag {
  padding: 1px 6px;
  border-radius: 999px;
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
  font-size: 11px;
  font-weight: 700;
}
.claims {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 24px;
}
.claims__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  margin-right: auto;
}
.claims__name {
  font-weight: 700;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  vertical-align: 1px;
}
.pill--sm {
  height: 20px;
  padding: 0 8px;
}
.pill--ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.pill--warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.pill--brand {
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.seg {
  display: inline-flex;
  align-self: flex-start;
  gap: 2px;
  padding: 3px;
  border-radius: 12px;
  background: var(--rl-track);
}
.seg__btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 18px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.seg__btn[aria-pressed="true"] {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.1);
}
.seg__btn:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.paths {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.paths__item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.paths__name {
  font-weight: 700;
}
.notice {
  display: flex;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--rl-rule);
  border-radius: 10px;
  font-size: 13px;
  background: var(--rl-head);
  color: var(--rl-body);
}
.notice :deep(svg) {
  flex: none;
  margin-top: 2px;
}
.disclosure {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 64px;
  padding: 12px 24px;
  border: 0;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.disclosure:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: -2px;
}
.disclosure__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.disclosure__title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-weight: 700;
}
.disclosure__chev {
  flex: none;
  margin-left: auto;
  transition: transform 0.15s;
}
.disclosure__chev.is-open {
  transform: rotate(180deg);
}
.adv-fields {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr));
  gap: 16px;
  padding: 4px 24px 24px;
}
.adv-fields :deep(.field--card) {
  box-shadow: none;
  border: 1px solid var(--rl-rule);
}
@media (max-width: 967px) {
  .claims {
    padding: 14px 20px;
    gap: 12px;
    flex-wrap: wrap;
  }
  .claims__text {
    flex: 1 1 200px;
  }
  .paths {
    grid-template-columns: minmax(0, 1fr);
  }
  .seg {
    align-self: stretch;
  }
  .seg__btn {
    flex: 1;
    justify-content: center;
    height: 38px;
  }
  .disclosure {
    padding: 12px 20px;
  }
  .adv-fields {
    padding: 4px 20px 20px;
  }
}
@media (max-width: 767px) {
  .claims {
    padding: 14px 16px;
  }
  /* SettingsPhone: "Show 9 advanced settings" is the whole card, one full-width button */
  .adv-btn-row {
    padding: 0;
  }
  .adv-btn {
    width: 100%;
    height: 52px;
    border: 0;
    border-radius: 0;
  }
  .disclosure {
    padding: 12px 16px;
  }
  .adv-fields {
    padding: 4px 16px 16px;
  }
  .pline__link {
    margin-left: 0;
  }
}
</style>
