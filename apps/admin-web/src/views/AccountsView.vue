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
import { getPoolStats, type PoolStats } from "@/api/shell";
import AccountsHeader from "@/components/accounts/AccountsHeader.vue";
import AccountLanes from "@/components/accounts/AccountLanes.vue";
import {
  AUTO_REFRESH_MS,
  type ChipState,
  checkSummary,
  chipsAfterRead,
  commandChip,
  healthItems,
  hhmmss,
  PAGE_SIZE,
  poolItems,
  type RowAction,
  WAKE_LABEL,
} from "@/components/accounts/account-model";
import type { CommandStatus } from "@/api/commands";
import EditDialog from "@/components/accounts/EditDialog.vue";
import ImportDialog from "@/components/accounts/ImportDialog.vue";
import PoolCard from "@/components/accounts/PoolCard.vue";
import ProviderTable from "@/components/jobs/ProviderTable.vue";
import { loadFailedText } from "@/components/jobs/job-text";
import { IconAlertCircle, IconPlus } from "@/components/shared/icons";
import ListPager from "@/components/shared/ListPager.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useRouteLeaveGuard } from "@/components/shared/use-leave-guard";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useCommandPoll } from "@/composables/use-command-poll";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";

// Accounts. The provider strip and the per-provider table on top filter the page; the accounts are drawn in one lane per
// provider (AccountLanes). Delete, check, Check all, proxy re-check and wake are commands run by the provider's worker: the page
// queues one and polls it. A used account can only be disabled, and its switch is hidden while a worker holds it.
// The list reads itself again every 10 s while the page is open (and no dialog or confirm box is in front of it).
const scope = useScopeStore();
const stats = useStatsStore();
const providers = useProvidersStore();
const { isPhone, isTablet } = useBreakpoint();
const { busy, run } = useBusy();
// the Edit drawer's own write (the wait for a proxy check and the patch): only this makes its close paths inert
const { busy: saving, run: runSave } = useBusy();
// Check all and Refresh catalog poll for a long time: each locks only its own entry, as the other actions stay usable.
const { busy: checkingAll, run: runCheckAll } = useBusy();
const { busy: readingCatalogs, run: runReadCatalogs } = useBusy();
// a proxy check polls as long: the Edit dialog stays open meanwhile and its Save stays usable
const { run: runProxyCheck } = useBusy();
const { poll } = useCommandPoll();
const latest = useLatest();

const items = shallowRef<Account[]>([]);
const total = ref(0);
const offset = ref(0);
const limit = ref(PAGE_SIZE);
const loading = ref(false);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);
const pool = shallowRef<PoolStats | null>(null);
const poolError = ref<string | null>(null);
// the accounts whose Check is running: their Check button is drawn disabled
const checking = ref<ReadonlySet<string>>(new Set());
// The chip line under each command's control (B-States "Command status"), by control: `check:<account id>`,
// `proxy:<account id>`, `checkAll`, `refreshCatalog`. A finished chip stays through the next automatic read of the list
// and goes with the one after (`chipsAfterRead`); `autoReads` counts the automatic reads that answered.
const chips = shallowRef<Record<string, ChipState>>({});
let autoReads = 0;

const importOpen = ref(false);
const editOpen = ref(false);
// the account in the Edit dialog, by id: it follows the list as the list is read again
const editingId = ref<string | null>(null);
const editing = computed(() => items.value.find((a) => a.id === editingId.value) ?? null);
const confirming = ref(false);
// something typed in an open drawer that a leave of the page would lose
const importDirty = ref(false);
const editDirty = ref(false);
// an import under way: its answer lists the rejected lines and must still be seen
const importBusy = ref(false);
useRouteLeaveGuard(
  () => (importOpen.value && importDirty.value) || (editOpen.value && editDirty.value),
  () => importBusy.value || saving.value,
);
// A dialog or a confirm box in front of the list: the auto-refresh leaves the list alone.
const dialogOpen = computed(() => importOpen.value || editOpen.value || confirming.value);

let disposed = false;

// Only the newest answer draws (or shows a failure): a slow older one must not bring old rows back.
async function load(auto = false): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const [page, poolRead] = await Promise.all([
      listAccounts(scope.provider, limit.value, offset.value),
      // the pool figures are a side panel: a failure keeps the old figures, says so, and never hides the list
      getPoolStats(scope.provider).then(
        (value) => ({ value }),
        (err: unknown) => ({ err }),
      ),
    ]);
    if (!latest.isCurrent(ticket)) return;
    items.value = page.items;
    total.value = page.total;
    if (auto) chips.value = chipsAfterRead(chips.value, ++autoReads);
    if ("value" in poolRead) {
      pool.value = poolRead.value;
      poolError.value = null;
    } else {
      poolError.value = loadFailedText(poolRead.err, errorText(poolRead.err), "the pool figures");
    }
    error.value = null;
    updatedAt.value = Date.now();
    // the tab counts and the ready / dead / disabled figures come from /admin/stats; the table on top and the lane
    // headers from the provider summary
    void stats.refresh();
    void providers.refresh();
  } catch (err) {
    if (!latest.isCurrent(ticket)) return;
    error.value = loadFailedText(err, errorText(err), "the accounts");
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

// The timer exists only while this view is mounted (the Accounts tab is open). A hidden tab does not read, and a
// read still in flight is never joined by a second one; a tab that comes back reads at once.
function tick(): void {
  if (!dialogOpen.value && !document.hidden && !loading.value) void load(true);
}
const timer = setInterval(tick, AUTO_REFRESH_MS);
document.addEventListener("visibilitychange", onVisible);
function onVisible(): void {
  if (!document.hidden) tick();
}
onBeforeUnmount(() => {
  disposed = true;
  clearInterval(timer);
  document.removeEventListener("visibilitychange", onVisible);
  latest.invalidate();
});

// Queue, poll to the end, then read the list again (if the operator is still here). A command whose control has a
// chip under it says it is under way there; the others say "queued" in a toast (`toast`).
async function queue(start: Promise<QueuedCommand>, label: string, toast = true) {
  const out = await start;
  if (toast) notify.info(`${label}: queued`);
  const command = await poll(out.command_id, label);
  if (!disposed) await load();
  return command;
}

// One run of an action per account at a time (`<action>:<account id>`): a second click while the first is under way
// does nothing, so a double click never sends twice.
const inFlight = new Set<string>();
async function once(key: string, action: () => Promise<unknown>): Promise<void> {
  if (inFlight.has(key)) return;
  inFlight.add(key);
  try {
    await action();
  } finally {
    inFlight.delete(key);
  }
}

function setChip(key: string, chip: ChipState): void {
  chips.value = { ...chips.value, [key]: chip };
}

// The command's chip under its control: running while `work` queues and polls it, then how it ended, kept until the
// next read of the list. The toasts go on as before (B-States draws both).
async function withChip(key: string, work: () => Promise<CommandStatus | null>): Promise<CommandStatus | null> {
  setChip(key, { state: "running", settled: false, read: autoReads });
  try {
    const command = await work();
    setChip(key, commandChip(command, autoReads));
    return command;
  } catch (err) {
    setChip(key, { state: "error", message: errorText(err), settled: true, read: autoReads });
    throw err;
  }
}

async function check(a: Account) {
  // the phone's Check is a menu item: it is not drawn disabled, so a second pick is refused here
  if (checking.value.has(a.id)) return;
  checking.value = new Set(checking.value).add(a.id);
  try {
    await run(async () => {
      const command = await withChip(`check:${a.id}`, () => queue(checkAccount(a.id), "Check", false));
      const fresh = items.value.find((x) => x.id === a.id);
      if (command?.status === "done" && fresh) {
        const outcome = command.result?.outcome;
        notify[fresh.state === "dead" ? "error" : "info"](
          checkSummary(typeof outcome === "string" ? outcome : undefined, fresh),
        );
      }
    });
  } finally {
    const rest = new Set(checking.value);
    rest.delete(a.id);
    checking.value = rest;
  }
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

const remove = (a: Account) =>
  once(`delete:${a.id}`, async () => {
    confirming.value = true;
    let ok: boolean;
    try {
      // as B-States draws a delete confirm: the title asks, naming the item; one sentence says the consequence
      ok = await confirmDialog({
        title: `Delete account ${a.label}?`,
        message: "A used account cannot be deleted: disable it instead.",
        confirmText: "Delete",
        danger: true,
      });
    } finally {
      confirming.value = false;
    }
    if (ok) await run(() => queue(deleteAccount(a.id), "Delete"));
  });

// The drawer is gone and Element Plus has sent focus back to the control that opened it. The list reads itself again,
// so that control (a row menu item) may no longer exist: then focus goes to the primary button of the page, else to
// its scroll area, so it is never left on the document.
let opener: HTMLElement | null = null;
const rememberOpener = (): void => {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
};
function drawerClosed(): void {
  // a menu item is hidden, not removed, when its menu closes: it counts only while it can be seen
  if (opener?.isConnected && opener !== document.body && opener.getClientRects().length) return;
  const primary = document.querySelector<HTMLButtonElement>(".accounts-view button.primary");
  if (primary && !primary.disabled) primary.focus();
  else document.querySelector<HTMLElement>(".app-scroll__wrap")?.focus({ preventScroll: true });
}

function openImport() {
  rememberOpener();
  importOpen.value = true;
}

function openEdit(a: Account) {
  rememberOpener();
  editingId.value = a.id;
  editOpen.value = true;
}

async function saveAccount(patch: AccountPatch) {
  const a = editing.value;
  if (!a) return;
  const out = await runSave(async () => {
    // a Check proxy of this account still under way ends first: its read of the list never lands after the save
    await proxyRuns.get(a.id);
    const answer = await patchAccount(a.id, patch);
    editOpen.value = false;
    notify.success("Saved");
    return answer;
  });
  if (!out) return;
  // the drawer is gone: the proxy check the server queued and the new read of the list are the page's general work
  await run(async () => {
    if (out.command_id) await poll(out.command_id, "Proxy check");
    if (!disposed) await load();
  });
}

// The Check proxy runs under way, by account: Save of that account waits for its run to end.
const proxyRuns = new Map<string, Promise<unknown>>();
function recheck(a: Account) {
  const running = runProxyCheck(() => withChip(`proxy:${a.id}`, () => queue(recheckProxy(a.id), "Proxy check", false)));
  proxyRuns.set(a.id, running);
  void running.finally(() => {
    if (proxyRuns.get(a.id) === running) proxyRuns.delete(a.id);
  });
  return running;
}

const wake = (a: Account) => once(`wake:${a.id}`, () => run(() => queue(wakeAccount(a.id), WAKE_LABEL)));

// The switch, once per account at a time (`toggle` itself reads the list again whatever the answer).
const onToggle = (a: Account, enabled: boolean) => once(`toggle:${a.id}`, () => toggle(a, enabled));

function onAction(name: RowAction, a: Account) {
  if (name === "check") void check(a);
  else if (name === "shot") void showShot(a);
  else if (name === "wake") void wake(a);
  else if (name === "edit") openEdit(a);
  else void remove(a);
}

// One provider when the filter is set, else every provider one after another (the worker of each runs its own).
async function providerNames(): Promise<string[]> {
  if (scope.provider) return [scope.provider];
  await scope.loadCatalog();
  return scope.providers;
}

// The chip of a page command says how the whole round ended: the first provider that failed, else the last one.
async function forEachProvider(
  runner: typeof run,
  label: string,
  key: string,
  start: (provider: string) => Promise<QueuedCommand>,
) {
  await runner(() =>
    withChip(key, async () => {
      let ended: CommandStatus | null = null;
      for (const name of await providerNames()) {
        const command = await queue(start(name), `${label} ${name}`, false);
        if (ended?.status !== "failed") ended = command;
      }
      return ended;
    }),
  );
}

const checkEvery = () => forEachProvider(runCheckAll, "Check all", "checkAll", checkAll);
const readCatalogs = () => forEachProvider(runReadCatalogs, "Refresh catalog", "refreshCatalog", refreshCatalog);

const health = computed(() => healthItems(stats.data?.accounts ?? null));
const poolFigures = computed(() => poolItems(pool.value));
const poolTitle = computed(
  () => `${scope.provider || (scope.providers.length === 1 ? scope.providers[0] : "provider")} pool`,
);
const updated = computed(() => (updatedAt.value ? hhmmss(updatedAt.value) : null));
const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit.value)));
const showing = computed(() =>
  total.value === 0
    ? "Showing 0 of 0"
    : `Showing ${offset.value + 1}–${Math.min(offset.value + limit.value, total.value)} of ${total.value.toLocaleString("en-US")}`,
);
const skeleton = computed(() => loading.value && items.value.length === 0 && !error.value);
const refreshing = computed(() => loading.value && items.value.length > 0 && !error.value);
const laneSize = computed(() => (isPhone.value ? "phone" : isTablet.value ? "tablet" : "desktop"));
</script>

<template>
  <section class="accounts-view" :class="{ 'accounts-view--phone': isPhone }">
    <AccountsHeader
      :updated="updated"
      :checking-all="checkingAll"
      :reading-catalogs="readingCatalogs"
      :check-all-chip="chips.checkAll ?? null"
      :refresh-chip="chips.refreshCatalog ?? null"
      @import="openImport"
      @check-all="checkEvery"
      @refresh-catalog="readCatalogs"
    />

    <ProviderTable :summary="providers.data" :selected="scope.provider" @pick="(p: string) => (scope.provider = p)" />

    <!-- the count is the list's: none until the list has been read once -->
    <PoolCard :count="updatedAt ? total : null" :health="health" :pool="poolFigures" :pool-title="poolTitle" />

    <div v-if="poolError" class="state-error" role="alert" data-testid="pool-error">
      <IconAlertCircle :size="18" />
      <span class="state-error__text">{{ poolError }}</span>
    </div>

    <section class="list" aria-label="Account list" :aria-busy="loading">
      <div v-if="error" class="state-error" role="alert" data-state="error">
        <IconAlertCircle :size="18" />
        <span class="state-error__text">{{ error }}</span>
        <button type="button" class="retry" @click="load()">Retry</button>
      </div>
      <div v-else-if="items.length === 0 && !skeleton" class="list-card state-empty" data-state="empty">
        <p>No accounts yet.</p>
        <button type="button" class="primary" @click="openImport"><IconPlus :size="15" :stroke="2" />Import</button>
      </div>
      <!-- the first read draws one lane of flat bars (the B-States loading pattern) -->
      <div v-else :class="{ 'is-refreshing': refreshing }" :data-state="skeleton ? 'loading' : 'ready'">
        <AccountLanes
          :rows="items"
          :checking="checking"
          :chips="chips"
          :summary="providers.data"
          :selected="scope.provider"
          :size="laneSize"
          :skeleton="skeleton"
          @toggle="onToggle"
          @action="onAction"
          @pick="(p: string) => (scope.provider = p)"
        />
      </div>
      <!-- the phone's one footer line, once there are rows to count -->
      <div v-if="isPhone && pageCount <= 1 && items.length" class="list-card foot-text">
        <span class="tnum">{{ showing }}</span>
      </div>
      <div v-else-if="!error && items.length" class="list-card">
        <ListPager
          :offset="offset"
          :limit="limit"
          :total="total"
          @move="
            (o: number) => {
              offset = o;
              void load();
            }
          "
          @size="
            (s: number) => {
              limit = s;
              offset = 0;
              void load();
            }
          "
        />
      </div>
    </section>

    <ImportDialog
      v-model="importOpen"
      @imported="load"
      @update:busy="(b: boolean) => (importBusy = b)"
      @update:dirty="(d: boolean) => (importDirty = d)"
      @closed="drawerClosed"
    />
    <EditDialog
      v-model="editOpen"
      :account="editing"
      :busy="busy"
      :saving="saving"
      :proxy-chip="editing ? (chips[`proxy:${editing.id}`] ?? null) : null"
      @save="saveAccount"
      @recheck="recheck"
      @update:dirty="(d: boolean) => (editDirty = d)"
      @closed="drawerClosed"
    />
  </section>
</template>

<style scoped>
.accounts-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.accounts-view--phone {
  gap: 16px;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.list-card {
  overflow: hidden;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.is-refreshing {
  opacity: 0.6;
  transition: opacity 0.15s;
}
.foot-text {
  padding: 12px 16px;
  color: var(--rl-muted);
  font-size: 13px;
}
.tnum {
  font-variant-numeric: tabular-nums;
}
.state-error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.state-error__text {
  flex: 1;
  min-width: 220px;
  color: var(--rl-fg);
}
.retry {
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--rl-bad);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-bad);
  /* the page's family; the line height stays the button's own (normal), as drawn */
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.state-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 44px 16px 52px;
  text-align: center;
}
.state-empty p {
  margin: 0;
  color: var(--rl-muted);
}
.primary {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 14px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font-family: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.primary:hover {
  background: var(--rl-primary-hover);
}
</style>
