<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef } from "vue";
import { type ApiKey, createKey, deleteKey, type KeyBody, listKeys, patchKey, setKeyEnabled } from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import PageHeader from "@/components/layout/PageHeader.vue";
import KeyFormDialog from "@/components/keys/KeyFormDialog.vue";
import KeySwitch from "@/components/keys/KeySwitch.vue";
import NewKeyCallout from "@/components/keys/NewKeyCallout.vue";
import WalletLines from "@/components/keys/WalletLines.vue";
import { createdText, type CreateError, createError, deleteToast, unfinishedText } from "@/components/keys/key-model";
import "@/components/logs/lane-controls.css";
import { copyText } from "@/components/shared/clipboard";
import DataTable from "@/components/shared/DataTable.vue";
import { IconCopy, IconPencil, IconPlus, IconTrash } from "@/components/shared/icons";
import StatusChip from "@/components/shared/StatusChip.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

// API keys. The list answers whole (a deployment has a handful of keys), so there is no pager. Keys ignore the global
// provider filter: a key spans providers.
const scope = useScopeStore();
const auth = useAuthStore();
const { busy, run } = useBusy();
const latest = useLatest();
const { isPhone, isTablet } = useBreakpoint();

const items = shallowRef<ApiKey[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

const formOpen = ref(false);
const editing = shallowRef<ApiKey | null>(null);
const catalog = shallowRef<CatalogModel[]>([]);
const formError = ref<CreateError | null>(null);

// The raw key of a key just created. It lives only here: not in a store, not in storage, not in the address, not in a
// log. It goes on Dismiss, when the operator leaves the tab (this view unmounts) and on sign-out. The id and the label
// beside it are not secret: they tint the new row and name the callout.
const rawKey = ref<string | null>(null);
const createdId = ref<string | null>(null);
const createdLabel = ref("");
// `disposed`: the operator left or signed out, so a create that is still in flight must not bring the key back.
let disposed = false;
const forgetSignOut = auth.onSignOut(() => {
  disposed = true;
  rawKey.value = null;
  createdId.value = null;
});
onBeforeUnmount(() => {
  disposed = true;
  rawKey.value = null;
  forgetSignOut();
  latest.invalidate();
});

const columns = computed(() => [
  { key: "label", label: "Label", minWidth: isTablet.value ? 130 : 150 },
  { key: "status", label: "Status", width: isTablet.value ? 84 : 108 },
  { key: "models", label: "Models", minWidth: 150 },
  { key: "wallets", label: "Wallets", minWidth: isTablet.value ? 214 : 300 },
  {
    key: "unfinished",
    label: isTablet.value ? "Unfinished · jobs" : "Unfinished / max",
    align: "right" as const,
    width: isTablet.value ? 112 : 128,
  },
  { key: "jobs", label: "Jobs total", align: "right" as const, width: 90 },
  { key: "created", label: "Created", width: 120 },
]);
const TABLET_COLUMNS = ["label", "status", "wallets", "unfinished"];

async function load(): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listKeys();
    if (!latest.isCurrent(ticket)) return;
    items.value = out.items;
    error.value = null;
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
      // Only the key text, its id and its label are kept from the answer; the rest is the list row the reload brings.
      let created: { key: string; id: string; label: string };
      try {
        const out = await createKey(body);
        created = { key: out.key, id: out.id, label: out.label };
      } catch (err) {
        // Shown from a fixed table, never from the server's message (see createError); no toast either.
        if (!disposed) formError.value = createError(err);
        return;
      }
      if (!disposed) {
        rawKey.value = created.key;
        createdId.value = created.id;
        createdLabel.value = created.label || body.label;
      }
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
    title: `Delete key ${k.label || k.prefix}?`,
    message: "A key that has jobs is only disabled.",
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

async function copyPrefix(k: ApiKey) {
  const outcome = await copyText(k.prefix);
  if (outcome === "select" || outcome === "failed") notify.error("Could not copy: select the text and copy it by hand");
}

const labelOf = (k: ApiKey): string => k.label || k.prefix;
const rowClass = ({ row }: { row: ApiKey }): string => (row.id === createdId.value ? "key-new" : "");
const modelsText = (k: ApiKey): string => k.models.join(" · ");
</script>

<template>
  <section class="keys-view">
    <PageHeader
      title="API keys"
      subtitle="Who may call the relay, which models they may use and how much each wallet may spend."
    >
      <template #actions>
        <button type="button" class="kla-btn kla-btn--primary kla-btn--fit" :disabled="busy" @click="openForm(null)">
          <IconPlus :size="isPhone ? 16 : 15" :stroke="2" />New key
        </button>
      </template>
    </PageHeader>

    <NewKeyCallout v-if="rawKey !== null" :raw-key="rawKey" :label="createdLabel" @dismiss="rawKey = null" />

    <section class="kla-card keys-card" aria-label="API keys">
      <DataTable
        class="keys-table"
        :actions-width="isTablet ? 128 : 141"
        :columns="columns"
        :rows="items"
        :loading="loading"
        :error="error"
        empty-text="No API keys yet."
        :tablet-columns="TABLET_COLUMNS"
        :row-class-name="rowClass"
        @retry="load"
      >
        <template #empty-action>
          <button type="button" class="kla-btn kla-btn--primary" :disabled="busy" @click="openForm(null)">
            <IconPlus :size="15" :stroke="2" />New key
          </button>
        </template>
        <template #cell-label="{ row }">
          <div class="k-label" :class="{ 'is-off': !row.enabled }">{{ row.label || "(no label)" }}</div>
          <div class="k-prefix">
            <span class="k-prefix__text kla-mono">{{ row.prefix }}</span>
            <button
              v-if="row.prefix"
              type="button"
              class="kla-icon-btn k-copy"
              title="Copy key prefix"
              :aria-label="`Copy key prefix of ${labelOf(row)}`"
              @click="copyPrefix(row)"
            >
              <IconCopy :size="12" />
            </button>
          </div>
          <span v-if="isTablet" class="k-models-line kla-mono">{{
            row.models.includes("*") ? "any model" : row.models.join(", ")
          }}</span>
        </template>
        <!-- Colour is for exceptions: an enabled key is the normal case and reads as plain text. -->
        <template #cell-status="{ row }">
          <StatusChip v-if="!row.enabled" status="disabled" />
          <span v-else class="k-enabled">enabled</span>
        </template>
        <template #cell-models="{ row }">
          <span v-if="row.models.includes('*')" class="k-tag">any model</span>
          <div v-else class="k-models kla-mono">
            <span v-for="m in row.models" :key="m">{{ m }}</span>
          </div>
        </template>
        <template #cell-wallets="{ row }">
          <WalletLines v-if="row.wallets.length" :wallets="row.wallets" :compact="isTablet" />
          <template v-else>-</template>
        </template>
        <template #cell-unfinished="{ row }">
          <span class="kla-mono k-num">{{ unfinishedText(row) }}</span>
          <template v-if="isTablet">
            <span class="kla-mono k-num"> · {{ row.jobs }} jobs</span>
            <div class="k-created kla-mono">{{ createdText(row.created_ms) }}</div>
          </template>
        </template>
        <template #cell-jobs="{ row }"
          ><span class="kla-mono k-num">{{ row.jobs }}</span></template
        >
        <template #cell-created="{ row }"
          ><span class="k-created kla-mono">{{ createdText(row.created_ms) }}</span></template
        >
        <template #actions="{ row }">
          <div class="k-actions">
            <KeySwitch
              :model-value="row.enabled"
              :label="`Enable key ${labelOf(row)}`"
              @change="(v: boolean) => toggle(row, v)"
            />
            <button
              type="button"
              class="kla-icon-btn"
              title="Edit key"
              :aria-label="`Edit key ${labelOf(row)}`"
              :disabled="busy"
              @click="openForm(row)"
            >
              <IconPencil :size="16" />
            </button>
            <button
              type="button"
              class="kla-icon-btn kla-icon-btn--bad"
              title="Delete key"
              :aria-label="`Delete key ${labelOf(row)}`"
              @click="remove(row)"
            >
              <IconTrash :size="16" />
            </button>
          </div>
        </template>

        <!-- the phone list: one block per key -->
        <template #row="{ row }">
          <div class="p-key">
            <div class="p-key__line">
              <span class="p-key__label" :class="{ 'is-off': !row.enabled }">{{ row.label || "(no label)" }}</span>
              <StatusChip v-if="!row.enabled" status="disabled" />
              <span v-else class="p-key__enabled">enabled</span>
              <KeySwitch
                :model-value="row.enabled"
                :label="`Enable key ${labelOf(row)}`"
                @change="(v: boolean) => toggle(row, v)"
              />
            </div>
            <div class="p-key__prefix">
              <span class="kla-mono">{{ row.prefix }}</span>
              <button
                v-if="row.prefix"
                type="button"
                class="kla-icon-btn kla-icon-btn--lg p-key__copy"
                title="Copy key prefix"
                :aria-label="`Copy key prefix of ${labelOf(row)}`"
                @click="copyPrefix(row)"
              >
                <IconCopy :size="14" />
              </button>
            </div>
            <div class="p-key__models">
              <span class="p-key__models-word">Models</span>
              <span v-if="row.models.includes('*')" class="k-tag k-tag--phone">any model</span>
              <span v-else class="kla-mono p-key__models-list">{{ modelsText(row) }}</span>
            </div>
            <WalletLines v-if="row.wallets.length" :wallets="row.wallets" compact />
            <div class="p-key__foot">
              <span class="p-key__facts"
                >Unfinished <span class="kla-mono p-key__fg">{{ unfinishedText(row) }}</span> ·
                <span class="kla-mono">{{ row.jobs }}</span> jobs · created
                <span class="kla-mono">{{ createdText(row.created_ms) }}</span></span
              >
              <span class="p-key__buttons">
                <button
                  type="button"
                  class="kla-icon-btn kla-icon-btn--lg"
                  title="Edit key"
                  :aria-label="`Edit key ${labelOf(row)}`"
                  :disabled="busy"
                  @click="openForm(row)"
                >
                  <IconPencil :size="18" />
                </button>
                <button
                  type="button"
                  class="kla-icon-btn kla-icon-btn--lg kla-icon-btn--bad"
                  title="Delete key"
                  :aria-label="`Delete key ${labelOf(row)}`"
                  @click="remove(row)"
                >
                  <IconTrash :size="18" />
                </button>
              </span>
            </div>
          </div>
        </template>
      </DataTable>
      <p v-if="items.length" class="keys-note">
        A key that has jobs is only disabled when deleted. Wallet bars: left = today, right = total.
      </p>
    </section>

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
.keys-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
@media (max-width: 1279px) {
  .keys-view {
    gap: 16px;
  }
}
.keys-card {
  min-width: 0;
}
/* the table: one rhythm, cells top-aligned, 16 px at both ends of a row */
.keys-table :deep(.el-table) {
  min-width: 1120px;
}
.keys-table.is-tablet :deep(.el-table) {
  min-width: 0;
}
.keys-table :deep(td.el-table__cell) {
  padding: 12px 0;
  vertical-align: top;
}
.keys-table :deep(td.el-table__cell:first-child .cell),
.keys-table :deep(th.el-table__cell:first-child .cell) {
  padding-left: 16px;
}
.keys-table :deep(td.el-table__cell:last-child .cell),
.keys-table :deep(th.el-table__cell:last-child .cell) {
  padding-right: 16px;
}
.keys-table :deep(td.data-table__actions) {
  padding: 10px 0;
}
.keys-table :deep(tr.key-new > td.el-table__cell) {
  background: var(--rl-primary-soft);
}
.keys-table :deep(tr.key-new:hover > td.el-table__cell) {
  background: var(--rl-raised);
}
.k-label {
  font-weight: 600;
  color: var(--rl-fg);
}
.k-label.is-off {
  color: var(--rl-muted);
}
.k-prefix {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
}
.k-prefix__text {
  font-size: 12px;
  color: var(--rl-faint);
}
.k-copy {
  width: 24px;
  height: 24px;
  border-radius: 8px;
}
.k-models-line {
  display: block;
  font-size: 11px;
  line-height: 16px;
  color: var(--rl-muted);
  word-break: break-word;
}
.k-enabled {
  color: var(--rl-muted);
  line-height: 24px;
}
.k-tag {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 8px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
  font-size: 12px;
  line-height: 16px;
}
.k-models {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12px;
  line-height: 18px;
}
.k-num {
  font-size: 13px;
  white-space: nowrap;
}
.k-created {
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-muted);
  white-space: nowrap;
}
.is-tablet .k-created {
  font-size: 11px;
}
.k-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 4px;
}
.keys-note {
  margin: 0;
  padding: 14px 16px;
  font-size: 13px;
  color: var(--rl-muted);
}

/* phone */
.keys-card :deep(.data-table__main) {
  padding: 12px 16px 6px !important;
}
.p-key {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.p-key__line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.p-key__label {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.p-key__label.is-off {
  color: var(--rl-muted);
}
.p-key__enabled {
  font-size: 13px;
  color: var(--rl-muted);
}
.p-key__prefix {
  display: flex;
  align-items: center;
  font-size: 12px;
  color: var(--rl-muted);
}
.p-key__copy {
  margin: -12px 0;
}
.p-key__models {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
}
.p-key__models-word {
  font-size: 12px;
  color: var(--rl-muted);
}
.k-tag--phone {
  padding: 2px 8px;
}
.p-key__models-list {
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}
.p-key__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.p-key__facts {
  font-size: 12px;
  color: var(--rl-muted);
}
.p-key__fg {
  color: var(--rl-fg);
}
.p-key__buttons {
  display: inline-flex;
  margin-right: -8px;
}
</style>
