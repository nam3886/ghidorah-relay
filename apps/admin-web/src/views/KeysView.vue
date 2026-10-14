<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef } from "vue";
import { type ApiKey, createKey, deleteKey, type KeyBody, listKeys, patchKey, setKeyEnabled } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import KeyFormDialog from "@/components/keys/KeyFormDialog.vue";
import NewKeyCallout from "@/components/keys/NewKeyCallout.vue";
import {
  type CreateError,
  createError,
  deleteToast,
  PAGE_SIZE,
  unfinishedText,
  walletText,
} from "@/components/keys/key-model";
import CopyButton from "@/components/shared/CopyButton.vue";
import DataTable from "@/components/shared/DataTable.vue";
import ListToolbar from "@/components/shared/ListToolbar.vue";
import OffsetPager from "@/components/shared/OffsetPager.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { fmtTime } from "@/lib/format";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

// API keys. The list answers whole (a deployment has a handful of keys); the page shows it a screenful at a time.
// Keys ignore the global provider filter: a key spans providers.
const scope = useScopeStore();
const auth = useAuthStore();
const { busy, run } = useBusy();
const latest = useLatest();

const items = shallowRef<ApiKey[]>([]);
const offset = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);
const page = computed(() => items.value.slice(offset.value, offset.value + PAGE_SIZE));

const formOpen = ref(false);
const editing = shallowRef<ApiKey | null>(null);
const catalog = shallowRef<CatalogModel[]>([]);
const formError = ref<CreateError | null>(null);

// The raw key of a key just created. It lives only here: not in a store, not in storage, not in the address, not in a
// log. It goes on Dismiss, when the operator leaves the tab (this view unmounts) and on sign-out.
const rawKey = ref<string | null>(null);
// `disposed`: the operator left or signed out, so a create that is still in flight must not bring the key back.
let disposed = false;
const forgetSignOut = auth.onSignOut(() => {
  disposed = true;
  rawKey.value = null;
});
onBeforeUnmount(() => {
  disposed = true;
  rawKey.value = null;
  forgetSignOut();
  latest.invalidate();
});

const columns = [
  { key: "label", label: "Label", minWidth: 200 },
  { key: "status", label: "Status", width: 110 },
  { key: "models", label: "Models", minWidth: 160 },
  { key: "wallets", label: "Wallets", minWidth: 220 },
  { key: "unfinished", label: "Unfinished / max", align: "right" as const, width: 140 },
  { key: "jobs", label: "Jobs total", align: "right" as const, width: 100 },
  { key: "created", label: "Created", width: 170 },
];

async function load(): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listKeys();
    if (!latest.isCurrent(ticket)) return;
    items.value = out.items;
    // a shorter list than before: stay on a page that exists
    if (offset.value >= out.items.length)
      offset.value = Math.max(0, Math.floor((out.items.length - 1) / PAGE_SIZE) * PAGE_SIZE);
    error.value = null;
    updatedAt.value = Date.now();
  } catch (err) {
    if (!latest.isCurrent(ticket)) return;
    error.value = errorText(err);
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}
void load();

// The catalog (one read, shared) feeds the model list and the wallet rows of the form.
async function openForm(key: ApiKey | null) {
  await run(async () => {
    catalog.value = await scope.loadCatalog();
    editing.value = key;
    formError.value = null;
    formOpen.value = true;
  });
}

async function submit(body: KeyBody) {
  const key = editing.value;
  await run(async () => {
    formError.value = null;
    if (key) {
      await patchKey(key.id, body);
    } else {
      // Only the key text is kept from the answer; the rest of it is the list row the reload brings anyway.
      let created: string;
      try {
        created = (await createKey(body)).key;
      } catch (err) {
        // Shown from a fixed table, never from the server's message (see createError); no toast either.
        if (!disposed) formError.value = createError(err);
        return;
      }
      if (!disposed) rawKey.value = created;
    }
    formOpen.value = false;
    await load();
  });
}

// The list is read again even when the change failed, so the switch goes back to what the server holds.
async function toggle(k: ApiKey, enabled: boolean) {
  await run(async () => {
    try {
      await setKeyEnabled(k.id, enabled);
    } finally {
      await load();
    }
  });
}

async function remove(k: ApiKey) {
  const ok = await confirmDialog({
    title: "Delete key",
    message: `Delete key "${k.label || k.prefix}"? A key that has jobs is only disabled.`,
    confirmText: "Delete",
    danger: true,
  });
  if (!ok) return;
  await run(async () => {
    const out = await deleteKey(k.id);
    await load();
    notify.info(deleteToast(out.result));
  });
}
</script>

<template>
  <section class="keys-view">
    <ListToolbar :loading="loading" :updated-at="updatedAt" @refresh="load">
      <h2 class="view-title">API keys</h2>
      <template #actions>
        <el-button type="primary" :disabled="busy" @click="openForm(null)">New key</el-button>
      </template>
    </ListToolbar>

    <NewKeyCallout v-if="rawKey !== null" :raw-key="rawKey" @dismiss="rawKey = null" />

    <DataTable :columns="columns" :rows="page" :loading="loading" :error="error" empty-text="No API keys" @retry="load">
      <template #cell-label="{ row }">
        <div class="strong">{{ row.label || "(no label)" }}</div>
        <div class="prefix">
          <span class="mono">{{ row.prefix }}</span>
          <span v-if="row.prefix" title="Copy the key prefix (the full key is not stored)"
            ><CopyButton :text="row.prefix"
          /></span>
        </div>
      </template>
      <template #cell-status="{ row }"><StatusChip :status="row.enabled ? 'enabled' : 'disabled'" /></template>
      <template #cell-models="{ row }">
        <StatusChip v-if="row.models.includes('*')" status="any model" />
        <span v-else class="sub">{{ row.models.join(", ") }}</span>
      </template>
      <template #cell-wallets="{ row }">
        <template v-if="row.wallets.length">
          <div v-for="w in row.wallets" :key="`${w.provider}/${w.kind}`" class="sub nowrap">
            {{ w.provider }} {{ w.kind }}: {{ walletText(w) }}
          </div>
        </template>
        <template v-else>-</template>
      </template>
      <template #cell-unfinished="{ row }">{{ unfinishedText(row) }}</template>
      <template #cell-created="{ row }">{{ fmtTime(row.created_ms) }}</template>
      <template #actions="{ row }">
        <div class="row-actions">
          <el-switch
            :model-value="row.enabled"
            :aria-label="`Enabled: ${row.label || row.prefix}`"
            @change="(v: string | number | boolean) => toggle(row, Boolean(v))"
          />
          <el-button size="small" :disabled="busy" @click="openForm(row)">Edit</el-button>
          <el-button size="small" class="danger" @click="remove(row)">Delete</el-button>
        </div>
      </template>
    </DataTable>
    <OffsetPager :offset="offset" :limit="PAGE_SIZE" :total="items.length" @move="(o: number) => (offset = o)" />

    <KeyFormDialog
      v-model="formOpen"
      :editing="editing"
      :models="catalog"
      :busy="busy"
      :error="formError"
      @submit="submit"
    />
  </section>
</template>

<style scoped>
.view-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.strong {
  font-weight: 600;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.prefix {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--rl-muted);
  font-size: 12px;
}
.sub {
  color: var(--rl-muted);
  font-size: 12px;
}
.nowrap {
  white-space: nowrap;
}
.row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.danger {
  color: var(--rl-bad);
}
</style>
