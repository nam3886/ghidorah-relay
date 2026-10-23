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
} from "@/api/settings";
import ContainerLimits from "@/components/settings/ContainerLimits.vue";
import PasswordForm from "@/components/settings/PasswordForm.vue";
import SectionState from "@/components/settings/SectionState.vue";
import SettingsGroupCard from "@/components/settings/SettingsGroupCard.vue";
import "@/components/settings/settings-skeleton.css";
import { cardsOf } from "@/components/settings/settings-cards";
import ValueTable from "@/components/settings/ValueTable.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { loadFailedText } from "@/components/jobs/job-text";
import { bootValue, parseValue, valueText } from "@/components/settings/settings-value";
import { errorText } from "@/lib/errors";
import PricesTable from "@/components/settings/PricesTable.vue";
import SettingsCard from "@/components/settings/SettingsCard.vue";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { notify } from "@/lib/notify";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";

// Settings (final.html): the provider strip picks All providers, General or one provider. A card per provider (its
// "Taking jobs" switch, its models and prices, its basic settings, the rest under Advanced) and General (the
// relay-wide settings, with links to the page's other sections). Every value is a draft until its card's Save; a
// refused value keeps what was typed and says why under it.
const scope = useScopeStore();
const providers = useProvidersStore();
const router = useRouter();
const { isPhone, isTablet } = useBreakpoint();
const { busy, run } = useBusy();

const items = ref<SettingItem[]>([]);
const envOnly = ref<EnvOnlyItem[]>([]);
const fixed = ref<FixedLimit[]>([]);
const containers = ref<ContainerRow[]>([]);
type KeyedPrice = PriceRow & { id: string };
const prices = ref<KeyedPrice[]>([]);
const settingsLoading = ref(false);
const pricesLoading = ref(false);
const settingsError = ref<string | null>(null);
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
    settingsError.value = null;
  } catch (err) {
    // worded as B-States draws a failed load: "Could not load the settings: the relay answered 502 (bad gateway)."
    if (settingsLatest.isCurrent(ticket)) settingsError.value = loadFailedText(err, errorText(err), "the settings");
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

const reload = () => Promise.all([loadSettings(), loadPrices()]);

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

// Save sends each changed setting of the card, the input text parsed as JSON (or the raw string when it is not JSON),
// one after another; a refusal is kept on its field and toasted, the others still go. Then both lists are read again.
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
  void reload();
  // the models of each provider card, and how each provider sends (its header)
  void scope.loadCatalog().catch((err: unknown) => notify.error(errorText(err)));
  void providers.refresh();
});
watch(() => scope.provider, loadPrices);

// A row is one (provider, model, duration): that is its table key.
const withKey = (row: PriceRow): KeyedPrice => ({ ...row, id: `${row.provider}/${row.model}/${row.duration}` });

// The page's other sections, reached from the General card's footer: in-page anchors. The app routes by hash, so a
// click scrolls to the card instead of changing it.
const SECTIONS = [
  { id: "password", label: "Change password" },
  { id: "boot", label: "Boot-only values" },
  { id: "prices", label: "Measured prices" },
  { id: "limits", label: "Fixed limits" },
  { id: "containers", label: "Containers" },
] as const;
const current = ref<string>("password");

// What the strip picked: one provider's card, General alone, or every card (General first). The other sections show
// with General and with All; a provider keeps its measured prices.
const cards = computed(() => cardsOf(items.value));
const shown = computed(() =>
  cards.value.filter((c) => (scope.provider ? c.group === scope.provider : scope.general ? c.group === "core" : true)),
);
const modelsOf = (group: string) => (scope.models ?? []).filter((m) => m.owned_by === group);
const summaryOf = (group: string) => providers.data?.providers.find((p) => p.provider === group) ?? null;
function frames(group: string): void {
  scope.provider = group;
  void router.push({ name: "frames" });
}
// the first read: one card of flat bars
const skeleton = computed(() => settingsLoading.value && !items.value.length && !settingsError.value);
const bootRows = computed(() => envOnly.value.map((row) => ({ name: row.name, value: bootValue(row.value) })));
// Scrolls the page's own scroll area (the window on a phone) and nothing else: scrollIntoView would also scroll the
// overflow-hidden ancestors once a card cannot reach the top, shifting and clipping the layout. The area clamps the
// target, so a card too near the end just scrolls as far as it can.
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
</script>

<template>
  <section class="settings" :class="{ 'is-tablet': isTablet, 'is-phone': isPhone }">
    <div>
      <h1 class="settings__title">Settings</h1>
      <p class="settings__subtitle">
        A card per provider: its models and prices, its budgets, the rest folded under Advanced. General holds the
        relay-wide values, your password and what the relay was started with.
      </p>
    </div>

    <section id="settings" class="settings__cards" aria-label="Settings" :aria-busy="settingsLoading">
      <SettingsCard v-if="settingsError || !items.length" id="settings-state" title="Settings">
        <SectionState :error="settingsError" :empty="!settingsLoading" empty-text="No settings" @retry="loadSettings" />
        <div v-if="skeleton" class="set-skel-card" data-state="loading" aria-hidden="true">
          <i v-for="n in 5" :key="n" class="set-skel" :style="{ width: `${40 + n * 9}%` }" />
        </div>
      </SettingsCard>
      <div v-else class="sboard" :class="{ 'sboard--single': shown.length === 1 }" data-state="ready">
        <SettingsGroupCard
          v-for="card in shown"
          :key="card.group"
          :card="card"
          :drafts="drafts"
          :errors="errors"
          :models="modelsOf(card.group)"
          :summary="summaryOf(card.group)"
          :busy="busy"
          :links="card.group === 'core' ? SECTIONS : undefined"
          :current="current"
          @draft="(key: string, text: string) => (drafts[key] = text)"
          @save="save"
          @discard="discard"
          @reset="reset"
          @go="go"
          @frames="frames(card.group)"
        />
      </div>
    </section>

    <div class="settings__content">
      <PasswordForm v-if="!scope.provider" />

      <SettingsCard
        v-if="!scope.provider"
        id="boot"
        title="Boot-only values"
        note="Read once when the relay starts. Change them in the environment and restart. Secrets are never listed."
      >
        <!-- read with the settings: their failure and their wait are this card's too -->
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
        v-if="!scope.provider"
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
        v-if="!scope.provider"
        id="containers"
        title="Containers"
        note="Each relay service's own limits, read from its cgroup when it started. A worker that stopped cleanly is not listed."
      >
        <ContainerLimits :rows="containers" :loading="settingsLoading" :error="settingsError" @retry="loadSettings" />
      </SettingsCard>
    </div>
  </section>
</template>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.settings__title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 600;
  letter-spacing: -0.015em;
}
.settings__subtitle {
  margin: 4px 0 0;
  color: var(--rl-muted);
}
.settings__content {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-width: 0;
}
.sboard {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
  align-items: start;
  gap: 16px;
}
.sboard--single {
  grid-template-columns: minmax(0, 760px);
}
.set-skel-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 0 16px 18px;
}

/* A card reached from a General link stops 16 px under the top of the page area; on a phone the window scrolls and the
   sticky top bar (102 px) is added. ponytail: the phone bar height written here; a variable from the top bar when it
   gets one. */
.settings {
  --settings-bar: 0px;
  --settings-anchor: calc(var(--settings-bar) + 16px);
}
@media (min-width: 768px) and (max-width: 1279px) {
  .settings__title {
    font-size: 24px;
    line-height: 32px;
    letter-spacing: -0.012em;
  }
  .settings__subtitle {
    margin-top: 2px;
  }
}
@media (max-width: 767px) {
  .settings {
    --settings-bar: 102px;
    gap: 16px;
  }
  .settings__title {
    font-size: 22px;
    line-height: 30px;
    letter-spacing: -0.01em;
  }
  .settings__subtitle {
    margin-top: 2px;
  }
  .settings__content {
    gap: 16px;
  }
  .sboard {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
