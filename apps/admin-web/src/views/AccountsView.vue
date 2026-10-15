<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import {
  type Account,
  type AccountPatch,
  accountScreenshot,
  checkAccount,
  checkAll,
  deleteAccount,
  listAccounts,
  patchAccount,
  type QueuedCommand,
  recheckProxy,
  refreshCatalog,
  wakeAccount,
} from "@/api/accounts";
import {
  ago,
  asleepLines,
  AUTO_REFRESH_MS,
  checkSummary,
  PAGE_SIZE,
  pointsFigure,
  proxyCheck,
  proxyText,
  WAKE_LABEL,
} from "@/components/accounts/account-model";
import EditDialog from "@/components/accounts/EditDialog.vue";
import ImportDialog from "@/components/accounts/ImportDialog.vue";
import DataTable from "@/components/shared/DataTable.vue";
import ListToolbar from "@/components/shared/ListToolbar.vue";
import OffsetPager from "@/components/shared/OffsetPager.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useBusy } from "@/composables/use-busy";
import { useCommandPoll } from "@/composables/use-command-poll";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { fmtTime } from "@/lib/format";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";

// Accounts. Delete, check, Check all, proxy re-check and wake are commands run by the provider's worker: the page
// queues one and polls it. A used account can only be disabled, and its switch is hidden while a worker holds it.
const scope = useScopeStore();
const ui = useUiStore();
const { busy, run } = useBusy();
// Check all and Refresh catalog poll for a long time: each locks only its own button, as the other actions stay usable.
const { busy: checkingAll, run: runCheckAll } = useBusy();
const { busy: readingCatalogs, run: runReadCatalogs } = useBusy();
const { poll } = useCommandPoll();
const latest = useLatest();

const items = shallowRef<Account[]>([]);
const total = ref(0);
const offset = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);

const importOpen = ref(false);
const editOpen = ref(false);
const editing = shallowRef<Account | null>(null);
const confirming = ref(false);
// A dialog or a confirm box in front of the list: the auto-refresh leaves the list alone.
const dialogOpen = computed(() => importOpen.value || editOpen.value || confirming.value);

let disposed = false;

const rowTone = (a: Account) => (a.state === "dead" ? ("bad" as const) : undefined);

const columns = [
  { key: "account", label: "Account", minWidth: 200 },
  { key: "status", label: "Status", minWidth: 150 },
  { key: "points", label: "Points left / budget", minWidth: 190, align: "right" as const },
  { key: "asleep", label: "Asleep", minWidth: 170 },
  { key: "videos", label: "Videos", width: 90, align: "right" as const },
  { key: "images", label: "Images", width: 90, align: "right" as const },
  { key: "proxy", label: "Proxy", minWidth: 170 },
  { key: "last_error", label: "Last error", minWidth: 180 },
];

// Only the newest answer draws (or shows a failure): a slow older one must not bring old rows back.
async function load(): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const page = await listAccounts(scope.provider, PAGE_SIZE, offset.value);
    if (!latest.isCurrent(ticket)) return;
    items.value = page.items;
    total.value = page.total;
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
watch(
  () => scope.provider,
  () => {
    offset.value = 0;
    void load();
  },
);

// The timer exists only while this view is mounted (the Accounts tab is open); the switch itself lives in the ui
// store, so it outlives tab switches.
let timer: ReturnType<typeof setInterval> | undefined;
function tick(): void {
  if (!dialogOpen.value) void load();
}
watch(
  () => ui.accountsAutoRefresh,
  (on) => {
    clearInterval(timer);
    timer = on ? setInterval(tick, AUTO_REFRESH_MS) : undefined;
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  disposed = true;
  clearInterval(timer);
  latest.invalidate();
});

// Queue, toast, poll to the end, then read the list again (if the operator is still here).
async function queue(start: Promise<QueuedCommand>, label: string) {
  const out = await start;
  notify.info(`${label}: queued`);
  const command = await poll(out.command_id, label);
  if (!disposed) await load();
  return command;
}

async function check(a: Account) {
  await run(async () => {
    const command = await queue(checkAccount(a.id), "Check");
    const fresh = items.value.find((x) => x.id === a.id);
    if (command?.status === "done" && fresh) {
      const outcome = command.result?.outcome;
      notify[fresh.state === "dead" ? "error" : "info"](
        checkSummary(typeof outcome === "string" ? outcome : undefined, fresh),
      );
    }
  });
}

// The list is read again even when the change failed, so the switch goes back to what the server holds.
async function toggle(a: Account, enabled: boolean) {
  await run(async () => {
    try {
      await patchAccount(a.id, { enabled });
    } finally {
      if (!disposed) await load();
    }
  });
}

async function showShot(a: Account) {
  await run(async () => {
    const url = URL.createObjectURL(await accountScreenshot(a.id));
    window.open(url, "_blank", "noopener");
    // the new tab has long read it by then
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  });
}

async function remove(a: Account) {
  confirming.value = true;
  let ok: boolean;
  try {
    ok = await confirmDialog({
      title: "Delete account",
      message: `Delete account "${a.label}"? A used account cannot be deleted: disable it instead.`,
      confirmText: "Delete",
      danger: true,
    });
  } finally {
    confirming.value = false;
  }
  if (ok) await run(() => queue(deleteAccount(a.id), "Delete"));
}

function openEdit(a: Account) {
  editing.value = a;
  editOpen.value = true;
}

async function saveAccount(patch: AccountPatch) {
  const a = editing.value;
  if (!a) return;
  await run(async () => {
    const out = await patchAccount(a.id, patch);
    editOpen.value = false;
    notify.success("Saved");
    if (out.command_id) await poll(out.command_id, "Proxy check");
    if (!disposed) await load();
  });
}

const recheck = (a: Account) => run(() => queue(recheckProxy(a.id), "Proxy check"));

function more(command: string | number | object, a: Account) {
  if (command === "shot") void showShot(a);
  else if (command === "wake") void run(() => queue(wakeAccount(a.id), WAKE_LABEL));
  else if (command === "edit") openEdit(a);
  else if (command === "delete") void remove(a);
}

// One provider when the filter is set, else every provider one after another (the worker of each runs its own).
async function providerNames(): Promise<string[]> {
  if (scope.provider) return [scope.provider];
  await scope.loadCatalog();
  return scope.providers;
}

async function forEachProvider(runner: typeof run, label: string, start: (provider: string) => Promise<QueuedCommand>) {
  await runner(async () => {
    for (const name of await providerNames()) await queue(start(name), `${label} ${name}`);
  });
}

const checkEvery = () => forEachProvider(runCheckAll, "Check all", checkAll);
const readCatalogs = () => forEachProvider(runReadCatalogs, "Refresh catalog", refreshCatalog);
</script>

<template>
  <section class="accounts-view">
    <ListToolbar
      v-model:auto-refresh="ui.accountsAutoRefresh"
      :loading="loading"
      :updated-at="updatedAt"
      @refresh="load"
    >
      <h2 class="view-title">Accounts</h2>
      <template #actions>
        <el-button :disabled="checkingAll" title="Check every enabled account, one at a time" @click="checkEvery"
          >Check all</el-button
        >
        <el-button
          :disabled="readingCatalogs"
          title="Read the provider’s models and options again"
          @click="readCatalogs"
          >Refresh catalog</el-button
        >
        <el-button type="primary" @click="importOpen = true">Import</el-button>
      </template>
    </ListToolbar>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      :error="error"
      empty-text="No accounts"
      :row-tone="rowTone"
      @retry="load"
    >
      <template #cell-account="{ row }">
        <div class="strong">{{ row.label }}</div>
        <div class="sub">
          {{ row.display_name || row.identity || "" }} · {{ row.provider
          }}<template v-if="row.composer"> · composer {{ row.composer }}</template>
        </div>
      </template>
      <template #cell-status="{ row }">
        <StatusChip :status="row.state" />
        <StatusChip v-if="row.in_use" status="in use" class="gap-left" />
        <div v-if="row.state_reason" class="sub">{{ row.state_reason }}</div>
        <div class="sub nowrap" :title="fmtTime(row.last_checked_ms)">Checked {{ ago(row.last_checked_ms) }}</div>
      </template>
      <template #cell-points="{ row }">
        <template v-if="row.capacity.length">
          <div v-for="c in row.capacity" :key="c.pool" class="points nowrap">
            <span class="sub">{{ c.pool }}</span>
            <span class="figure">{{ pointsFigure(c) }}</span>
            <StatusChip v-if="c.drift === true" status="drift" />
          </div>
        </template>
        <template v-else>-</template>
      </template>
      <template #cell-asleep="{ row }">
        <template v-if="asleepLines(row).length">
          <div v-for="line in asleepLines(row)" :key="line" class="sub">{{ line }}</div>
        </template>
        <template v-else>-</template>
      </template>
      <template #cell-videos="{ row }">{{ row.made.video }}</template>
      <template #cell-images="{ row }">{{ row.made.image }}</template>
      <template #cell-proxy="{ row }">
        <template v-if="row.proxy_host">
          {{ proxyText(row) }}
          <StatusChip v-if="row.has_proxy_auth" status="auth" class="gap-left" />
          <div>
            <StatusChip :status="proxyCheck(row).label" :tone="proxyCheck(row).tone" />
            <span v-if="row.proxy_checked_ms" class="sub nowrap gap-left">{{ fmtTime(row.proxy_checked_ms) }}</span>
          </div>
        </template>
        <template v-else>-</template>
      </template>
      <template #cell-last_error="{ row }">
        <div v-if="row.last_error" class="last-error" :title="row.last_error">{{ row.last_error }}</div>
        <template v-else>-</template>
      </template>
      <template #actions="{ row }">
        <div class="row-actions">
          <el-switch
            v-if="!row.in_use"
            :model-value="row.state !== 'disabled'"
            :aria-label="`Enabled: ${row.label}`"
            @change="(v: string | number | boolean) => toggle(row, Boolean(v))"
          />
          <el-button size="small" @click="check(row)">Check</el-button>
          <el-dropdown trigger="click" placement="bottom-end" @command="(c: string | number | object) => more(c, row)">
            <el-button size="small" :aria-label="`More actions for ${row.label}`">More</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="row.has_screenshot" command="shot">Shot</el-dropdown-item>
                <el-dropdown-item command="wake" title="Clear the cooldown and the strike count of this account"
                  >Wake</el-dropdown-item
                >
                <el-dropdown-item command="edit">Edit</el-dropdown-item>
                <el-dropdown-item command="delete" divided class="danger-item">Delete</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </template>
    </DataTable>
    <OffsetPager
      :offset="offset"
      :limit="PAGE_SIZE"
      :total="total"
      @move="
        (o: number) => {
          offset = o;
          void load();
        }
      "
    />

    <ImportDialog v-model="importOpen" @imported="load" />
    <EditDialog
      v-model="editOpen"
      :account="editing"
      :busy="busy"
      @save="saveAccount"
      @recheck="(a: Account) => recheck(a)"
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
.sub {
  color: var(--rl-muted);
  font-size: 12px;
}
.nowrap {
  white-space: nowrap;
}
.gap-left {
  margin-left: 6px;
}
.last-error {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--rl-muted);
  font-size: 12px;
}
.row-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.points {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
}
.figure {
  font-variant-numeric: tabular-nums;
}
</style>

<!-- The menu is drawn in a popper outside this component, so scoped rules do not reach it. -->
<style>
.el-dropdown-menu__item.danger-item,
.el-dropdown-menu__item.danger-item:hover,
.el-dropdown-menu__item.danger-item:focus {
  color: var(--rl-bad);
}
</style>
