<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import {
  deleteSetting,
  type EnvOnlyItem,
  getPrices,
  getSettings,
  type PriceRow,
  putSetting,
  type SettingItem,
} from "@/api/settings";
import PasswordForm from "@/components/settings/PasswordForm.vue";
import SettingsTable from "@/components/settings/SettingsTable.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { bootValue, loadFailedText, parseValue } from "@/components/settings/settings-value";
import SectionState from "@/components/settings/SectionState.vue";
import "@/components/settings/settings-skeleton.css";
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
    settingsError.value = null;
  } catch (err) {
    if (settingsLatest.isCurrent(ticket)) settingsError.value = loadFailedText("settings", err);
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
    if (pricesLatest.isCurrent(ticket)) pricesError.value = loadFailedText("measured prices", err);
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
  { id: "settings", label: "Settings" },
  { id: "boot", label: "Boot-only values" },
  { id: "prices", label: "Measured prices" },
  { id: "password", label: "Change password" },
] as const;
const current = ref<string>("settings");
// the boot-only list while the first read runs: its rows as bars (the B-States loading pattern)
const BOOT_SKELETON = ["88px", "120px", "72px", "104px"];
function go(id: string): void {
  current.value = id;
  document.getElementById(id)?.scrollIntoView({ block: "start" });
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
        <SettingsCard
          id="settings"
          title="Settings"
          note="A saved value is kept in the database and wins over the environment. Reset returns to the environment value, or the default."
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
          snug
        >
          <!-- read with the settings: their failure and their wait are this card's too -->
          <SectionState
            :error="settingsError"
            :empty="!envOnly.length && !settingsLoading"
            empty-text="None"
            @retry="loadSettings"
          />
          <dl v-if="!settingsError && !envOnly.length && settingsLoading" class="boot" aria-hidden="true">
            <div v-for="w in BOOT_SKELETON" :key="w" class="boot__row">
              <i class="set-skel" :style="{ width: w }" />
              <span class="boot__leader" />
              <i class="set-skel" :style="{ width: '96px' }" />
            </div>
          </dl>
          <dl v-else-if="!settingsError && envOnly.length" class="boot" aria-label="Boot-only values">
            <div v-for="row in envOnly" :key="row.name" class="boot__row">
              <dt>{{ row.name }}</dt>
              <span class="boot__leader" aria-hidden="true" />
              <dd class="num">{{ bootValue(row.value) }}</dd>
            </div>
          </dl>
        </SettingsCard>

        <SettingsCard
          id="prices"
          title="Measured prices"
          note="What upstream reported per job, against what the relay charges."
        >
          <PricesTable :rows="prices" :loading="pricesLoading" :error="pricesError" @retry="loadPrices" />
        </SettingsCard>

        <PasswordForm />
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

/* The drawn sub-navigation sticks 16 px from the top of the page (tablet: at the top). The app's top bar sticks too, so
   both sit under it: its height (shell.md: 64 + its line, tablet 110, phone 102) is added. A card reached from the
   sub-navigation stops below both. ponytail: the shell's heights written here; a variable from the top bar when it
   gets one. */
.settings {
  --settings-bar: 65px;
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

.boot {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 14px 20px 18px;
  background: var(--rl-raised);
  font-size: 13px;
}
.boot__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.boot dt {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-muted);
  white-space: nowrap;
}
.boot__leader {
  flex: 1;
  min-width: 16px;
  border-bottom: 1px dotted var(--rl-line-strong);
  transform: translateY(-4px);
}
.boot dd {
  margin: 0;
  font-family: var(--rl-mono);
  font-weight: 500;
  text-align: right;
  overflow-wrap: anywhere;
}

@media (min-width: 768px) and (max-width: 1279px) {
  /* the two-row top bar; a card stops below it and the stuck pills (8 + 36 + 8) */
  .settings {
    --settings-bar: 110px;
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
  .boot {
    gap: 12px;
    padding: 14px 16px 18px;
  }
  .boot__row {
    flex-direction: column;
    align-items: stretch;
    gap: 2px;
  }
  .boot dt {
    white-space: normal;
    overflow-wrap: anywhere;
  }
  .boot__leader {
    display: none;
  }
  .boot dd {
    text-align: left;
  }
}
</style>
