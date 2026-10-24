<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  type ContainerRow,
  deleteSetting,
  type EnvOnlyItem,
  type FixedLimit,
  getPrices,
  getSettings,
  type PriceRow,
  putSetting,
  type SettingItem,
  type SettingsList,
} from "@/api/settings";
import ContainerLimits from "@/components/settings/ContainerLimits.vue";
import SettingsHistory from "@/components/settings/SettingsHistory.vue";
import { SHOWN_OFFSET_MIN } from "@/components/settings/settings-pools";
import IconButton from "@/components/shared/IconButton.vue";
import PasswordForm from "@/components/settings/PasswordForm.vue";
import ProviderSettings from "@/components/settings/ProviderSettings.vue";
import SettingField from "@/components/settings/SettingField.vue";
import "@/components/settings/settings-skeleton.css";
import { cardsOf, changeLines } from "@/components/settings/settings-cards";
import ValueTable from "@/components/settings/ValueTable.vue";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { loadFailedText } from "@/components/jobs/job-text";
import { bootValue, parseValue, valueText } from "@/components/settings/settings-value";
import { errorText } from "@/lib/errors";
import { providerClass } from "@/lib/providers";
import PricesTable from "@/components/settings/PricesTable.vue";
import SettingsCard from "@/components/settings/SettingsCard.vue";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { notify } from "@/lib/notify";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";

// Settings (B Ledger): a row of tabs picks General or one provider (the shared provider filter: "" is General). A
// provider's tab holds its sections (Taking jobs, Send path, Models and prices, Pools and budgets, Advanced) and its
// measured prices; General holds the relay-wide settings, the password, what the relay was started with, the measured
// prices of every provider, the fixed limits and the containers, reached from its links. Every value is a draft until
// the page's bar saves them all; a refused value keeps what was typed and says why under it.
const scope = useScopeStore();
const providers = useProvidersStore();
const router = useRouter();
const { isPhone, isTablet } = useBreakpoint();
const compact = computed(() => isPhone.value || isTablet.value);
const { busy, run } = useBusy();

const items = ref<SettingItem[]>([]);
const envOnly = ref<EnvOnlyItem[]>([]);
const fixed = ref<FixedLimit[]>([]);
const containers = ref<ContainerRow[]>([]);
// "Last saved 14:02 GMT+7 by admin": the newest change of any setting, on the page's GMT+7 clock (settings-pools)
const saved = ref<SettingsList["last_saved"]>(null);
const lastSaved = computed(() =>
  saved.value
    ? {
        at: `${new Date(saved.value.at_ms + SHOWN_OFFSET_MIN * 60_000).toISOString().slice(11, 16)} GMT+7`,
        by: saved.value.by ?? "the relay",
      }
    : null,
);
const historyOpen = ref(false);
// A phone's page button goes to the top bar when the shell drew its target (the view alone, in a spec, keeps its row).
const barThere = ref(false);
onMounted(() => (barThere.value = !!document.getElementById("topbar-actions")));
const inBar = computed(() => isPhone.value && barThere.value);
type KeyedPrice = PriceRow & { id: string };
const prices = ref<KeyedPrice[]>([]);
const settingsLoading = ref(false);
const pricesLoading = ref(false);
const settingsError = ref<string | null>(null);
const settingsFailedAt = ref("");
const pricesError = ref<string | null>(null);
const settingsLatest = useLatest();
const pricesLatest = useLatest();

async function loadSettings(): Promise<void> {
  const ticket = settingsLatest.next();
  settingsLoading.value = true;
  try {
    const data = await getSettings();
    if (!settingsLatest.isCurrent(ticket)) return;
    items.value = data.items;
    envOnly.value = data.env_only;
    fixed.value = data.fixed;
    containers.value = data.containers;
    saved.value = data.last_saved ?? null;
    settingsError.value = null;
  } catch (err) {
    // worded as B-States draws a failed load: "Could not load the settings: the relay answered 502 (bad gateway)."
    if (settingsLatest.isCurrent(ticket)) {
      settingsError.value = loadFailedText(err, errorText(err), "the settings");
      settingsFailedAt.value = new Date().toLocaleTimeString("en-GB", { hour12: false });
    }
  } finally {
    if (settingsLatest.isCurrent(ticket)) settingsLoading.value = false;
  }
}

// The measured prices follow the global provider filter.
async function loadPrices(): Promise<void> {
  const ticket = pricesLatest.next();
  pricesLoading.value = true;
  try {
    const data = await getPrices(scope.provider);
    if (!pricesLatest.isCurrent(ticket)) return;
    prices.value = data.items.map(withKey);
    pricesError.value = null;
  } catch (err) {
    if (pricesLatest.isCurrent(ticket)) {
      pricesError.value = loadFailedText(err, errorText(err), "the measured prices");
    }
  } finally {
    if (pricesLatest.isCurrent(ticket)) pricesLoading.value = false;
  }
}

const reload = () => Promise.all([loadSettings(), loadPrices(), providers.refresh()]);

// The draft of every setting (the text its input holds) and the refusal of its last Save. A read of the settings gives
// each draft its stored value, except a refused one: what was typed stays, with the reason under it.
const drafts = reactive<Record<string, string>>({});
const errors = reactive<Record<string, string>>({});
watch(items, (list) => {
  const keys = new Set(list.map((i) => i.key));
  for (const key of Object.keys(drafts)) if (!keys.has(key)) delete drafts[key];
  for (const item of list) if (!(item.key in errors)) drafts[item.key] = valueText(item);
});
const byKey = computed(() => new Map(items.value.map((i) => [i.key, i])));
const edited = (key: string): boolean => {
  const item = byKey.value.get(key);
  return !!item && drafts[key] !== valueText(item);
};

// Save sends each changed setting of the page, the input text parsed as JSON (or the raw string when it is not JSON),
// one after another; a refusal is kept on its field and toasted, the others still go. Then the lists are read again.
const save = (keys: string[]) =>
  run(async () => {
    let refused = 0;
    for (const key of keys.filter(edited)) {
      try {
        await putSetting(key, parseValue(drafts[key] ?? ""));
        delete errors[key];
      } catch (err) {
        refused++;
        errors[key] = errorText(err);
        notify.error(errors[key]);
      }
    }
    await reload();
    if (!refused) notify.success("Saved");
  });

// Discard puts the stored values back and forgets the refusals.
function discard(keys: string[]): void {
  for (const key of keys) {
    delete errors[key];
    const item = byKey.value.get(key);
    if (item) drafts[key] = valueText(item);
  }
}

const reset = (key: string) =>
  run(async () => {
    await deleteSetting(key);
    delete errors[key];
    await reload();
    notify.success("Reset");
  });

onMounted(() => {
  void loadSettings();
  void loadPrices();
  // the models of each provider tab, and how each provider sends (its line)
  void scope.loadCatalog().catch((err: unknown) => notify.error(errorText(err)));
  void providers.refresh();
});
watch(() => scope.provider, loadPrices);

// A row is one (provider, model, duration): that is its table key.
const withKey = (row: PriceRow): KeyedPrice => ({ ...row, id: `${row.provider}/${row.model}/${row.duration}` });

// General's other sections, reached from its links: in-page anchors. The app routes by hash, so a click scrolls to the
// section instead of changing it.
const SECTIONS = [
  { id: "password", label: "Change password" },
  { id: "boot", label: "Boot-only values" },
  { id: "prices", label: "Measured prices" },
  { id: "limits", label: "Fixed limits" },
  { id: "containers", label: "Containers" },
] as const;
const current = ref<string>("password");

// The tabs: General, then a provider per group of settings. The open one is the provider filter's when it has a tab,
// else General.
const cards = computed(() => cardsOf(items.value));
const groups = computed(() => cards.value.map((c) => c.group));
const tab = computed(() => (groups.value.includes(scope.provider) ? scope.provider : "core"));
const shown = computed(() => cards.value.find((c) => c.group === tab.value) ?? null);
function choose(group: string): void {
  scope.provider = group === "core" ? "" : group;
}
const modelsOf = (group: string) => (scope.models ?? []).filter((m) => m.owned_by === group);
const summaryOf = (group: string) => providers.data?.providers.find((p) => p.provider === group) ?? null;
function accounts(group: string): void {
  scope.provider = group;
  void router.push({ name: "accounts" });
}
// the first read: flat bars
const skeleton = computed(() => settingsLoading.value && !items.value.length && !settingsError.value);
const bootRows = computed(() => envOnly.value.map((row) => ({ name: row.name, value: bootValue(row.value) })));

// The unsaved-changes bar: every edited setting of every tab, a line per change in words.
const changed = computed(() => items.value.filter((i) => edited(i.key)));
const lines = computed(() => changed.value.flatMap((i) => changeLines(i, drafts[i.key] ?? "")));
const count = computed(() => Math.max(lines.value.length, changed.value.length));
const barText = computed(() => {
  const more = lines.value.length - 3;
  return lines.value.slice(0, 3).join(" · ") + (more > 0 ? ` · ${more} more` : "");
});
const changedKeys = computed(() => changed.value.map((i) => i.key));

// Scrolls the page's own scroll area (the window on a phone) and nothing else: scrollIntoView would also scroll the
// overflow-hidden ancestors once a section cannot reach the top, shifting and clipping the layout. The area clamps the
// target, so a section too near the end just scrolls as far as it can.
function go(id: string): void {
  current.value = id;
  const card = document.getElementById(id);
  if (!card) return;
  const wrap = card.closest<HTMLElement>(".app-scroll__wrap");
  const area = wrap && getComputedStyle(wrap).overflowY !== "visible" ? wrap : null;
  const anchor = parseFloat(getComputedStyle(card).scrollMarginTop) || 0;
  const offset = card.getBoundingClientRect().top - (area ? area.getBoundingClientRect().top : 0) - anchor;
  if (area) area.scrollTo({ top: area.scrollTop + offset });
  else window.scrollTo({ top: window.scrollY + offset });
}
const ERROR_ICON = ["M7 18a5 5 0 0 1-.9-9.9A6 6 0 0 1 17.7 8 4.5 4.5 0 0 1 18 18z", "m3 3 18 18"];
const EMPTY_ICON = ["M3 13h5l1.5 3h5L16 13h5", "M5 5h14l2 8v6H3v-6z"];
</script>

<template>
  <section class="settings" :class="{ 'is-tablet': isTablet, 'is-phone': isPhone }">
    <div v-if="!compact" class="settings__head">
      <div class="settings__head-text">
        <h1 class="settings__title">Settings</h1>
        <p class="settings__subtitle">
          Changes apply to jobs claimed after you save.<template v-if="lastSaved">
            Last saved <span class="num">{{ lastSaved.at }}</span> by {{ lastSaved.by }}.</template
          >
        </p>
      </div>
      <span class="settings__head-btns">
        <IconButton icon="history" label="Change history" @click="historyOpen = true" />
      </span>
    </div>
    <template v-else>
      <h1 class="settings__title sr-only">Settings</h1>
      <!-- a phone's Change history sits in the top bar (SettingsPhone has no head row); a tablet keeps the row -->
      <div v-if="!inBar" class="settings__compact-head">
        <span v-if="lastSaved" class="settings__subtitle"
          >Last saved <span class="num">{{ lastSaved.at }}</span> by {{ lastSaved.by }}.</span
        >
        <IconButton icon="history" label="Change history" @click="historyOpen = true" />
      </div>
      <Teleport v-else to="#topbar-actions" defer>
        <IconButton icon="history" label="Change history" placement="bottom-end" @click="historyOpen = true" />
      </Teleport>
    </template>
    <SettingsHistory v-model="historyOpen" />

    <nav class="stabs" :class="{ 'stabs--chips': compact }" aria-label="Settings areas">
      <ArrowScroller label="Settings areas" :active-key="tab">
        <button
          v-for="g in groups.length ? groups : ['core']"
          :key="g"
          type="button"
          class="stab"
          :class="g === 'core' ? 'stab--general' : providerClass(g)"
          :aria-pressed="tab === g"
          :data-testid="`settings-tab-${g === 'core' ? 'general' : g}`"
          @click="choose(g)"
        >
          <span v-if="g !== 'core'" class="stab__dot" aria-hidden="true"></span>{{ g === "core" ? "General" : g }}
        </button>
      </ArrowScroller>
    </nav>

    <section id="settings" class="settings__body" aria-label="Settings" :aria-busy="settingsLoading">
      <div v-if="settingsError" class="sbox" role="alert" data-state="error">
        <span class="sbox__icon is-bad"><PathIcon :paths="ERROR_ICON" :size="22" :stroke="1.8" /></span>
        <b class="sbox__title">Could not load the settings</b>
        <span class="sbox__text">{{ settingsError }}</span>
        <span class="sbox__detail mono num">GET /admin/settings · {{ settingsFailedAt }}</span>
        <div class="sbox__btns"><button type="button" class="sbox__primary" @click="loadSettings">Retry</button></div>
      </div>
      <div v-else-if="skeleton" class="sskel" data-state="loading" aria-hidden="true">
        <div v-for="n in 3" :key="n" class="sskel__sec">
          <div class="sskel__head">
            <i class="set-skel" :style="{ width: '120px' }" /><i class="set-skel" :style="{ width: '200px' }" />
          </div>
          <div class="sskel__card">
            <i v-for="r in 4" :key="r" class="set-skel" :style="{ width: `${40 + r * 12}%` }" />
          </div>
        </div>
      </div>
      <div v-else-if="!items.length" class="sbox" role="status" data-state="empty">
        <span class="sbox__icon is-brand"><PathIcon :paths="EMPTY_ICON" :size="22" :stroke="1.8" /></span>
        <b class="sbox__title">No settings</b>
        <span class="sbox__text">The relay sent no settings. It may be an older build; update the box and reload.</span>
        <div class="sbox__btns"><button type="button" class="sbox__primary" @click="loadSettings">Reload</button></div>
      </div>

      <ProviderSettings
        v-else-if="shown && shown.group !== 'core'"
        :key="shown.group"
        data-state="ready"
        :card="shown"
        :drafts="drafts"
        :errors="errors"
        :models="modelsOf(shown.group)"
        :summary="summaryOf(shown.group)"
        :busy="busy"
        @draft="(key: string, text: string) => (drafts[key] = text)"
        @reset="reset"
        @accounts="accounts(shown.group)"
      />

      <SettingsCard
        v-else-if="shown"
        id="relay"
        data-state="ready"
        title="Relay"
        note="Relay-wide values: every provider reads them."
        bare
        data-testid="settings-card-core"
      >
        <template #head>
          <nav class="glinks" aria-label="General sections">
            <a
              v-for="l in SECTIONS"
              :key="l.id"
              :href="`#${l.id}`"
              :aria-current="current === l.id ? 'true' : undefined"
              @click.prevent="go(l.id)"
              >{{ l.label }}</a
            >
          </nav>
        </template>
        <div class="gfields">
          <SettingField
            v-for="item in shown.basic"
            :key="item.key"
            :item="item"
            :draft="drafts[item.key] ?? ''"
            :edited="edited(item.key)"
            :error="errors[item.key]"
            :busy="busy"
            @update:draft="(t: string) => (drafts[item.key] = t)"
            @reset="reset(item.key)"
          />
        </div>
      </SettingsCard>
    </section>

    <PasswordForm v-if="tab === 'core'" />

    <SettingsCard
      v-if="tab === 'core'"
      id="boot"
      title="Boot-only values"
      note="Read once when the relay starts. Change them in the environment and restart. Secrets are never listed."
    >
      <!-- read with the settings: their failure and their wait are this section's too -->
      <ValueTable
        :rows="bootRows"
        :loading="settingsLoading"
        :error="settingsError"
        label="Boot-only values"
        @retry="loadSettings"
      />
    </SettingsCard>

    <SettingsCard
      id="prices"
      title="Measured prices"
      note="What upstream reported per job, against what the relay charges."
    >
      <PricesTable :rows="prices" :loading="pricesLoading" :error="pricesError" @retry="loadPrices" />
    </SettingsCard>

    <SettingsCard
      v-if="tab === 'core'"
      id="limits"
      title="Fixed limits"
      note="Constants in the code that shape behaviour. They change only with a new release."
    >
      <ValueTable
        :rows="fixed"
        :loading="settingsLoading"
        :error="settingsError"
        label="Fixed limits"
        reasons
        @retry="loadSettings"
      />
    </SettingsCard>

    <SettingsCard
      v-if="tab === 'core'"
      id="containers"
      title="Containers"
      note="Each relay service's own limits, read from its cgroup when it started. A worker that stopped cleanly is not listed."
    >
      <ContainerLimits :rows="containers" :loading="settingsLoading" :error="settingsError" @retry="loadSettings" />
    </SettingsCard>

    <div v-if="changedKeys.length" class="sbar" role="region" aria-label="Unsaved changes">
      <span class="sbar__dot" aria-hidden="true"></span>
      <div class="sbar__text">
        <b
          ><span class="num">{{ count }}</span> unsaved {{ count === 1 ? "change" : "changes" }}</b
        >
        <span v-if="!isPhone" class="sbar__lines num">{{ barText }}</span>
      </div>
      <span class="sbar__btns">
        <button
          type="button"
          class="sbar__ghost"
          :disabled="busy"
          aria-label="Discard changes"
          @click="discard(changedKeys)"
        >
          Discard
        </button>
        <button
          type="button"
          class="sbar__primary"
          :disabled="busy"
          aria-label="Save changes"
          @click="save(changedKeys)"
        >
          {{ isPhone ? "Save" : "Save changes" }}
        </button>
      </span>
    </div>
  </section>
</template>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  gap: 20px;
  /* a section reached from a General link stops 16 px under the top of the page area; on a phone the window scrolls
     and the sticky top bar (102 px) is added. ponytail: the phone bar height written here; a variable from the top
     bar when it gets one. */
  --settings-bar: 0px;
  --settings-anchor: calc(var(--settings-bar) + 16px);
}
.settings__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
}
.settings__head-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.settings__head-btns {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.settings__compact-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.settings__compact-head :deep(.ib-wrap),
.settings__compact-head > :last-child {
  margin-left: auto;
}
.settings__title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.settings__subtitle {
  margin: 0;
  color: var(--rl-muted);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.settings__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}

/* the tabs: an underline row on a desktop (its own 1 px rule under the active 2 px line), pill chips below 1280 */
.stabs {
  min-width: 0;
}
.stabs :deep(.asc__row) {
  padding-bottom: 0;
  box-shadow: inset 0 -1px 0 var(--rl-line);
}
.stab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 14px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.stab[aria-pressed="true"] {
  border-bottom-color: var(--rl-brand);
  color: var(--rl-fg);
}
.stab:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: -2px;
  border-radius: 6px;
}
.stab__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pc);
}
.stabs--chips :deep(.asc__row) {
  padding-bottom: 4px;
  box-shadow: none;
}
.stabs--chips .stab {
  gap: 6px;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-body);
  font-size: 13px;
}
.stabs--chips .stab[aria-pressed="true"] {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.is-phone .stabs--chips .stab {
  height: 44px;
}

/* General: the relay-wide fields as cards, the links in the label column */
.gfields {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: 16px;
  align-items: start;
}
.glinks {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  margin-top: 6px;
}
.glinks a {
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
  color: var(--rl-link);
}
.glinks a:hover {
  color: var(--rl-link-hover);
}
.glinks a[aria-current="true"] {
  color: var(--rl-fg);
}
@media (max-width: 967px) {
  .glinks {
    flex-direction: row;
    flex-wrap: wrap;
    gap: 6px 16px;
  }
}

/* a failed or empty read: a box in the page's frame (JobsStates) */
.sbox {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 40px 24px;
  border-radius: 16px;
  text-align: center;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.sbox__icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 14px;
}
.sbox__icon.is-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.sbox__icon.is-brand {
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.sbox__title {
  font-size: 15px;
}
.sbox__text {
  max-width: 420px;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.sbox__detail {
  padding: 3px 8px;
  border: 1px solid var(--rl-rule);
  border-radius: 6px;
  font-size: 11px;
  background: var(--rl-head);
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.sbox__btns {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.sbox__primary {
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.is-phone .sbox__primary {
  min-width: 44px;
  height: 44px;
}
.sskel {
  display: flex;
  flex-direction: column;
  gap: 28px;
}
.sskel__sec {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 40px;
  padding-top: 28px;
  border-top: 1px solid var(--rl-line);
}
.sskel__head {
  flex: 1 1 240px;
  max-width: 300px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.sskel__card {
  flex: 999 1 640px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px 24px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}

/* the unsaved-changes bar: dark and floating at the foot of the page area on a desktop; on a tablet and a phone the
   page's footer (JobsTablet `footer`), edge to edge over the bottom bar */
.sbar {
  position: sticky;
  bottom: 20px;
  z-index: 2;
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
  padding: 14px 16px 14px 20px;
  border: 1px solid #3a3d4d;
  border-radius: 14px;
  background: #1a1b25;
  color: #ffffff;
  box-shadow: 0 12px 32px rgba(16, 24, 40, 0.24);
}
:global(html.dark) .sbar {
  border-color: #3a3d52;
  background: #262838;
  color: #ecedf3;
}
.sbar__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #a78bfa;
}
:global(html.dark) .sbar__dot {
  background: #c4b2ff;
}
.sbar__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1 1 320px;
  min-width: 0;
}
.sbar__lines {
  font-size: 12px;
  line-height: 18px;
  color: #c3c6d4;
  overflow-wrap: anywhere;
}
:global(html.dark) .sbar__lines {
  color: #a3a6b8;
}
.sbar__btns {
  display: flex;
  gap: 12px;
}
.sbar__ghost,
.sbar__primary {
  height: 36px;
  padding: 0 14px;
  border-radius: 10px;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.sbar__ghost {
  border: 1px solid #3a3d4d;
  background: transparent;
  color: inherit;
}
.sbar__primary {
  padding: 0 16px;
  border: 0;
  background: var(--rl-primary);
  color: #ffffff;
  font-weight: 700;
}
.sbar button:disabled {
  opacity: 0.6;
  cursor: wait;
}

@media (max-width: 1279px) {
  .settings {
    gap: 16px;
  }
  .sbar {
    bottom: 0;
    margin: 12px -24px -24px;
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
    padding: 12px 24px;
    border: 0;
    border-top: 1px solid var(--rl-rule);
    border-radius: 0;
    background: var(--rl-surface);
    color: var(--rl-fg);
    box-shadow: none;
  }
  :global(html.dark) .sbar {
    background: var(--rl-surface);
    color: var(--rl-fg);
  }
  .sbar__dot {
    display: none;
  }
  .sbar__text {
    flex: none;
  }
  .sbar__text b {
    font-size: 13px;
    font-weight: 600;
    color: var(--rl-body);
  }
  .sbar__lines,
  :global(html.dark) .sbar__lines {
    font-size: 13px;
    color: var(--rl-body);
  }
  .sbar__btns {
    gap: 8px;
  }
  .sbar__ghost,
  .sbar__primary {
    flex: 1;
    height: 44px;
  }
  .sbar__ghost {
    border-color: var(--rl-line-strong);
    background: var(--rl-surface);
    color: var(--rl-fg);
  }
}
@media (max-width: 767px) {
  .settings {
    --settings-bar: 102px;
    gap: 14px;
  }
  .sbar {
    bottom: 64px;
    margin: 12px -16px -20px;
    padding: 12px 16px;
  }
}
</style>
