<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
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
import SettingsTable from "@/components/settings/SettingsTable.vue";
import ValueTable from "@/components/settings/ValueTable.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { loadFailedText } from "@/components/jobs/job-text";
import { bootValue, parseValue } from "@/components/settings/settings-value";
import { errorText } from "@/lib/errors";
import PricesTable from "@/components/settings/PricesTable.vue";
import SettingsCard from "@/components/settings/SettingsCard.vue";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";

const scope = useScopeStore();
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

// Save sends the input text parsed as JSON, or the raw string when it is not JSON. Both writes reload the lists.
const save = (key: string, text: string) =>
  run(async () => {
    await putSetting(key, parseValue(text));
    await reload();
    notify.success("Saved");
  });

const reset = (key: string) =>
  run(async () => {
    await deleteSetting(key);
    await reload();
    notify.success("Reset");
  });

onMounted(reload);
watch(() => scope.provider, loadPrices);

// A row is one (provider, model, duration): that is its table key.
const withKey = (row: PriceRow): KeyedPrice => ({ ...row, id: `${row.provider}/${row.model}/${row.duration}` });

// The sub-navigation: in-page anchors. The app routes by hash, so a click scrolls to the card instead of changing it.
const SECTIONS = [
  { id: "password", label: "Change password" },
  { id: "settings", label: "Settings" },
  { id: "boot", label: "Boot-only values" },
  { id: "prices", label: "Measured prices" },
  { id: "limits", label: "Fixed limits" },
  { id: "containers", label: "Containers" },
] as const;
const current = ref<string>("password");
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
        Values the relay reads at run time, what it was started with, what upstream charges, and your password.
      </p>
    </div>

    <div class="settings__row">
      <nav class="snav" aria-label="Settings sections">
        <ul>
          <li v-for="s in SECTIONS" :key="s.id">
            <a
              :href="`#${s.id}`"
              :class="{ 'is-current': current === s.id }"
              :aria-current="current === s.id ? 'true' : undefined"
              @click.prevent="go(s.id)"
              >{{ s.label }}</a
            >
          </li>
        </ul>
      </nav>

      <div class="settings__content">
        <PasswordForm />

        <SettingsCard
          id="settings"
          title="Settings"
          note="A saved value is kept in the database. Reset returns to the default."
        >
          <SettingsTable
            :items="items"
            :loading="settingsLoading"
            :error="settingsError"
            :busy="busy"
            @save="save"
            @reset="reset"
            @retry="loadSettings"
          />
        </SettingsCard>

        <SettingsCard
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
          id="containers"
          title="Containers"
          note="Each relay service's own limits, read from its cgroup when it started. A worker that stopped cleanly is not listed."
        >
          <ContainerLimits :rows="containers" :loading="settingsLoading" :error="settingsError" @retry="loadSettings" />
        </SettingsCard>
      </div>
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
.settings__row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 20px;
}
.settings__content {
  flex: 1 1 640px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* The drawn sub-navigation sticks 16 px from the top of the page (tablet: at the top). On a tablet and a desktop the
   page scrolls in its own area under the top bar, so nothing is added for the bar (--settings-bar 0); on a phone the
   window scrolls and the sticky bar (102 px) is added. A card reached from the sub-navigation stops below the stuck
   navigation. ponytail: the phone bar height written here; a variable from the top bar when it gets one. */
.settings {
  --settings-bar: 0px;
  --settings-anchor: calc(var(--settings-bar) + 16px);
}
.snav {
  flex: 0 1 200px;
  min-width: 180px;
  position: sticky;
  top: calc(var(--settings-bar) + 16px);
}
.snav ul {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.snav a {
  display: block;
  padding: 8px 12px;
  border-radius: 10px;
  color: var(--rl-muted);
  font-size: 14px;
  font-weight: 500;
  text-decoration: none;
}
.snav a.is-current {
  background: var(--rl-primary-soft);
  color: var(--rl-fg);
  font-weight: 600;
}

@media (min-width: 768px) and (max-width: 1279px) {
  /* a card stops below the stuck pills (8 + 36 + 8) */
  .settings {
    --settings-bar: 0px;
    --settings-anchor: calc(var(--settings-bar) + 52px + 16px);
  }
  .settings__title {
    font-size: 24px;
    line-height: 32px;
    letter-spacing: -0.012em;
  }
  .settings__subtitle {
    margin-top: 2px;
  }
  .settings__row {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    gap: 20px;
  }
  .settings__content {
    flex: none;
  }
  .snav {
    flex: none;
    position: sticky;
    top: var(--settings-bar);
    z-index: 2;
    min-width: 0;
    margin: -8px -24px;
    padding: 8px 24px;
    background: var(--rl-bg);
    overflow-x: auto;
  }
  .snav ul {
    flex-wrap: nowrap;
    gap: 8px;
  }
  .snav li {
    flex: none;
  }
  .snav a {
    display: flex;
    align-items: center;
    box-sizing: border-box;
    height: 36px;
    padding: 0 14px;
    border: 1px solid var(--rl-line-strong);
    border-radius: 18px;
    background: var(--rl-surface);
    font-size: 13px;
  }
  .snav a.is-current {
    border-color: var(--rl-primary);
    background: var(--rl-primary-soft);
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
  .settings__row {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    gap: 16px;
  }
  .settings__content {
    flex: none;
    gap: 16px;
  }
  .snav {
    flex: none;
    position: static;
    min-width: 0;
    margin: 0 -16px;
    overflow-x: auto;
  }
  .snav ul {
    flex-wrap: nowrap;
    gap: 8px;
    padding: 0 16px;
  }
  .snav li {
    flex: none;
  }
  .snav a {
    display: flex;
    align-items: center;
    box-sizing: border-box;
    height: 44px;
    padding: 0 16px;
    border: 1px solid var(--rl-line-strong);
    border-radius: 22px;
    background: transparent;
    font-size: 14px;
  }
  .snav a.is-current {
    border-color: var(--rl-primary);
    background: var(--rl-primary-soft);
  }
}
</style>
