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
import { parseValue } from "@/components/settings/settings-value";
import DataTable from "@/components/shared/DataTable.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import type { Column } from "@/components/shared/types";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";

const scope = useScopeStore();
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
    if (settingsLatest.isCurrent(ticket)) settingsError.value = errorText(err);
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
    if (pricesLatest.isCurrent(ticket)) pricesError.value = errorText(err);
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

const envColumns: Column[] = [
  { key: "name", label: "Boot-only value", minWidth: 200 },
  { key: "value", label: "Value (read-only)", minWidth: 260 },
];
const priceColumns: Column[] = [
  { key: "provider", label: "Provider" },
  { key: "model", label: "Model", minWidth: 160 },
  { key: "duration", label: "Duration", align: "right" },
  { key: "charged", label: "Charged", align: "right" },
  { key: "jobs", label: "Jobs with a figure", align: "right" },
  { key: "disagreed", label: "Upstream disagreed", align: "right" },
  { key: "lowest_upstream", label: "Lowest upstream", align: "right" },
  { key: "substituted", label: "Substituted (not counted)", align: "right" },
];
// A row is one (provider, model, duration): that is its table key.
const withKey = (row: PriceRow): KeyedPrice => ({ ...row, id: `${row.provider}/${row.model}/${row.duration}` });
</script>

<template>
  <section class="settings">
    <div class="settings__block">
      <h3>Settings</h3>
      <SettingsTable
        :items="items"
        :loading="settingsLoading"
        :error="settingsError"
        :busy="busy"
        @save="save"
        @reset="reset"
        @retry="loadSettings"
      />
    </div>

    <div class="settings__block">
      <DataTable
        :columns="envColumns"
        :rows="envOnly"
        :loading="settingsLoading"
        row-key="name"
        empty-text="None"
        max-height="none"
      >
        <template #cell-name="{ row }"
          ><span class="mono">{{ row.name }}</span></template
        >
        <template #cell-value="{ row }"
          ><span class="mono">{{ row.value === null || row.value === undefined ? "-" : row.value }}</span></template
        >
      </DataTable>
    </div>

    <div class="settings__block">
      <h3>Measured prices</h3>
      <p class="settings__note">
        Points charged against what the upstream said it took. A job the upstream did something else for (another model
        or length) is counted apart.
      </p>
      <DataTable
        :columns="priceColumns"
        :rows="prices"
        :loading="pricesLoading"
        :error="pricesError"
        empty-text="No job has an upstream figure yet"
        max-height="none"
        @retry="loadPrices"
      >
        <template #cell-duration="{ row }">{{ row.duration === null ? "-" : `${row.duration}s` }}</template>
        <template #cell-disagreed="{ row }">
          <StatusChip v-if="row.disagreed" status="error" :label="String(row.disagreed)" />
          <template v-else>0</template>
        </template>
        <template #cell-lowest_upstream="{ row }">{{
          row.lowest_upstream === null ? "-" : row.lowest_upstream
        }}</template>
      </DataTable>
    </div>

    <PasswordForm />
  </section>
</template>

<style scoped>
.settings {
  display: grid;
  gap: 24px;
}
.settings__block h3 {
  margin: 0 0 8px;
  font-size: 16px;
}
.settings__note {
  margin: 0 0 8px;
  color: var(--rl-muted);
  font-size: 12px;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}
</style>
