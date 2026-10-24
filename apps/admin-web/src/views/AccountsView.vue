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
import AccountDetail from "@/components/accounts/AccountDetail.vue";
import AccountsHeader, { type Summary } from "@/components/accounts/AccountsHeader.vue";
import AccountsTable, { type Row } from "@/components/accounts/AccountsTable.vue";
import {
  andList,
  AUTO_REFRESH_MS,
  type ChipState,
  hhmm,
  unanswered,
  checkSummary,
  chipsAfterRead,
  commandChip,
  healthOf,
  healthRank,
  hhmmss,
  inIssue,
  type Issue,
  ISSUES,
  issueLine,
  PAGE_SIZE,
  type RowAction,
  WAKE_LABEL,
} from "@/components/accounts/account-model";
import type { CommandStatus } from "@/api/commands";
import EditDialog from "@/components/accounts/EditDialog.vue";
import ImportDialog from "@/components/accounts/ImportDialog.vue";
import { loadFailedText } from "@/components/jobs/job-text";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import IconButton from "@/components/shared/IconButton.vue";
import BottomSheet from "@/components/shared/overlays/BottomSheet.vue";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useRouteLeaveGuard } from "@/components/shared/use-leave-guard";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useCommandPoll } from "@/composables/use-command-poll";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { byProviderOrder, providerClass } from "@/lib/providers";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";

// Accounts (B · Ledger: Accounts, AccountsTablet, AccountsPhone, AccountsStates). One list, what needs a look first,
// filtered by two chip rows: Show (the issue) and Provider (the shared provider filter; a phone picks it in a sheet).
// A row's name opens its detail; Edit and Import are guarded drawers. Delete, check, Check all, proxy re-check and wake
// are commands run by the provider's worker: the page queues one and polls it. A used account can only be disabled,
// and its Taking jobs switch is hidden while a worker holds it. Ticked rows (desktop, tablet) take Check, Wake and
// Disable from the bulk bar. R re-checks the open account, or the row that has the focus.
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
    const page = await listAccounts(scope.provider, limit.value, offset.value);
    if (!latest.isCurrent(ticket)) return;
    items.value = page.items;
    total.value = page.total;
    if (auto) chips.value = chipsAfterRead(chips.value, ++autoReads);
    error.value = null;
    updatedAt.value = Date.now();
    // the tab counts come from /admin/stats; the table on top and the lane headers from the provider summary
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
  setChip(key, { state: "running", time: hhmmss(Date.now()), settled: false, read: autoReads });
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
  const primary = document.querySelector<HTMLButtonElement>(
    ".accounts-view button.head__primary, .accounts-view .head__plus button",
  );
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

// ---- the detail drawer: one drawer at a time, so Edit closes it first -------------------------------------------
const detailOpen = ref(false);
const detailId = ref<string | null>(null);
const detail = computed(() => items.value.find((a) => a.id === detailId.value) ?? null);
function openDetail(a: Account): void {
  detailId.value = a.id;
  detailOpen.value = true;
}
function onDetailAction(name: RowAction, a: Account): void {
  if (name === "edit" || name === "delete") detailOpen.value = false;
  onAction(name, a);
}
// a deleted account leaves the list: its drawer goes with it
watch(detail, (a) => {
  if (!a) detailOpen.value = false;
});

// R re-checks the account whose drawer is open, else the row that holds the focus; never while typing or with a
// drawer of a form or a question in front.
function onKey(event: KeyboardEvent): void {
  if (event.key !== "r" && event.key !== "R") return;
  if (event.ctrlKey || event.metaKey || event.altKey || dialogOpen.value) return;
  const target = event.target instanceof Element ? event.target : null;
  if (target?.closest("input, textarea, select, [contenteditable]")) return;
  const rowId = target?.closest<HTMLElement>("[data-account]")?.dataset.account;
  const a = detailOpen.value ? detail.value : items.value.find((x) => x.id === rowId);
  if (!a) return;
  event.preventDefault();
  void check(a);
}
document.addEventListener("keydown", onKey);
onBeforeUnmount(() => document.removeEventListener("keydown", onKey));

// ---- the list on screen: one health per row, the Show and Provider chips, the ticks ------------------------------
const pausedOf = (provider: string): boolean =>
  providers.data?.providers.find((p) => p.provider === provider)?.claims_enabled === false;
const all = computed<Row[]>(() =>
  items.value
    .map((a) => ({ a, h: healthOf(a, pausedOf(a.provider)) }))
    .sort((x, y) => healthRank(x.h) - healthRank(y.h)),
);
const look = computed(() => all.value.filter((r) => inIssue("look", r.h)).length);
// Show starts on Needs a look when something needs one at the first read, else on All; then it is the operator's
const issue = ref<Issue | null>(null);
watch(
  () => items.value,
  () => {
    if (issue.value === null && updatedAt.value) issue.value = look.value ? "look" : "all";
  },
);
const shownIssue = computed<Issue>(() => issue.value ?? "all");
const rows = computed(() => all.value.filter((r) => inIssue(shownIssue.value, r.h)));
const issueChips = computed(() =>
  // a phone draws no Switched off chip; a tablet neither that nor Proxy failing (AccountsTablet), Needs a look holds it
  ISSUES.filter(
    (i) => !(isPhone.value && i.key === "off") && !(isTablet.value && (i.key === "off" || i.key === "proxy")),
  ).map((i) => ({
    ...i,
    n: all.value.filter((r) => inIssue(i.key, r.h)).length,
  })),
);
const providerChips = computed(() => byProviderOrder(scope.providers));
function pickProvider(name: string): void {
  scope.provider = scope.provider === name ? "" : name;
}
void scope.loadCatalog().catch(() => undefined);

const selected = ref<ReadonlySet<string>>(new Set());
function toggleTick(ids: string[], on: boolean): void {
  const next = new Set(selected.value);
  for (const id of ids) {
    if (on) next.add(id);
    else next.delete(id);
  }
  selected.value = next;
}
// a tick follows the rows on screen: a row that left the list (or the filter) is no longer ticked
watch(rows, (now) => {
  const seen = new Set(now.map((r) => r.a.id));
  if ([...selected.value].some((id) => !seen.has(id))) {
    selected.value = new Set([...selected.value].filter((id) => seen.has(id)));
  }
});
const ticked = computed(() => rows.value.filter((r) => selected.value.has(r.a.id)).map((r) => r.a));
function bulk(name: "check" | "wake" | "disable"): void {
  for (const a of ticked.value) {
    if (name !== "disable") void (name === "check" ? check(a) : wake(a));
    else if (!a.in_use && a.state !== "disabled") void onToggle(a, false);
  }
  selected.value = new Set();
}

const summary = computed<Summary | null>(() => {
  const s = stats.data?.accounts;
  if (!s) return null;
  const healths = all.value.map((r) => r.h);
  const count = (k: string) => healths.filter((h) => h.kind === k).length;
  return {
    usable: s.usable,
    total: s.available + s.dead + s.disabled,
    look: look.value,
    line: issueLine(healths),
    bars: [s.usable, count("dead"), count("proxy"), count("asleep"), count("low")],
  };
});

const pausedHere = computed(() => (scope.provider && pausedOf(scope.provider) ? scope.provider : ""));
// AccountsStates "Partial": the providers whose last check could not read an account; their rows show the last answer
const partial = computed(() => unanswered(items.value));
const partialText = computed(() => {
  const p = partial.value;
  if (!p.providers.length) return "";
  const since = p.since ? `, from ${hhmm(p.since)}` : "";
  return `The health check of ${andList(p.providers)} ${p.timedOut ? "timed out" : "failed"}. Their rows show the last answer${since}.`;
});
const badProviders = computed(() => new Set(partial.value.providers));
// Check again: Check all, for the providers the banner names only
const checkAgain = () => forEachProvider(runCheckAll, "Check all", "checkAll", checkAll, partial.value.providers);
const empty = computed(() => {
  if (!items.value.length && !scope.provider) {
    return {
      kind: "first" as const,
      title: "No accounts yet",
      text: "Import accounts to start taking jobs. Paste one credential per line; secrets are stored encrypted.",
      primary: "Import accounts",
    };
  }
  const chip = ISSUES.find((i) => i.key === shownIssue.value);
  const word = chip && chip.kinds?.length === 1 ? `${chip.label.toLowerCase()} ` : "";
  const prov = scope.provider ? `${scope.provider} ` : "";
  return {
    kind: "filter" as const,
    title: shownIssue.value === "look" ? `No ${prov}account needs a look` : `No ${word}${prov}accounts`,
    text:
      shownIssue.value === "look"
        ? `Every ${prov}account answered its last check.`
        : "Change the filters, or clear them to see every account.",
    primary: "Clear filters",
  };
});
function onEmpty(): void {
  if (empty.value.kind === "first") openImport();
  else {
    issue.value = "all";
    scope.provider = "";
  }
}

const size = computed(() => (isPhone.value ? "phone" : isTablet.value ? "tablet" : "desktop"));
const filterOpen = ref(false);

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
  names?: string[],
) {
  await runner(() =>
    withChip(key, async () => {
      let ended: CommandStatus | null = null;
      for (const name of names ?? (await providerNames())) {
        const command = await queue(start(name), `${label} ${name}`, false);
        if (ended?.status !== "failed") ended = command;
      }
      return ended;
    }),
  );
}

const checkEvery = () => forEachProvider(runCheckAll, "Check all", "checkAll", checkAll);
const readCatalogs = () => forEachProvider(runReadCatalogs, "Refresh catalog", "refreshCatalog", refreshCatalog);

// Paging, only past the server's largest page: Showing a–b of N, Previous and Next.
const first = computed(() => (items.value.length ? offset.value + 1 : 0));
const last = computed(() => offset.value + items.value.length);
function move(to: number): void {
  offset.value = Math.max(0, to);
  void load();
}
</script>

<template>
  <section class="accounts-view" :class="`accounts-view--${size}`">
    <AccountsHeader
      :size="size"
      :summary="summary"
      :checking-all="checkingAll"
      :reading-catalogs="readingCatalogs"
      :check-all-chip="chips.checkAll ?? null"
      :refresh-chip="chips.refreshCatalog ?? null"
      @import="openImport"
      @check-all="checkEvery"
      @refresh-catalog="readCatalogs"
      @filter="filterOpen = true"
    />

    <!-- the phone's Show chips sit on the page, above the list -->
    <ArrowScroller v-if="isPhone" label="Show" :count="issueChips.length" :active-key="shownIssue" class="av-chips">
      <button
        v-for="c in issueChips"
        :key="c.key"
        type="button"
        class="av-chip"
        :aria-pressed="shownIssue === c.key ? 'true' : 'false'"
        @click="issue = c.key"
      >
        {{ c.label }} <span class="num av-chip__n">{{ c.n }}</span>
      </button>
    </ArrowScroller>

    <section class="av-card" :aria-label="isPhone ? 'Accounts that need a look' : 'Account list'" :aria-busy="loading">
      <div v-if="!isPhone" class="av-toolbar" :class="{ 'is-stacked': isTablet }">
        <template v-if="ticked.length">
          <span role="region" aria-label="Bulk actions" class="av-bulk">
            <b class="num">{{ ticked.length }} selected</b>
            <button type="button" class="av-bulk__btn" @click="bulk('check')">Check</button>
            <button type="button" class="av-bulk__btn" @click="bulk('wake')">Wake</button>
            <button type="button" class="av-bulk__btn" @click="bulk('disable')">Disable</button>
            <IconButton icon="close" label="Clear the selection" @click="selected = new Set()" />
          </span>
        </template>
        <template v-else>
          <b v-if="isTablet" class="av-title" aria-hidden="true">Accounts</b>
          <ArrowScroller label="Show" :active-key="shownIssue" class="av-row av-row--show">
            <button
              v-for="c in issueChips"
              :key="c.key"
              type="button"
              class="av-chip"
              :aria-pressed="shownIssue === c.key ? 'true' : 'false'"
              @click="issue = c.key"
            >
              {{ c.label }} <span class="num av-chip__n">{{ isTablet ? `· ${c.n}` : c.n }}</span>
            </button>
          </ArrowScroller>
          <ArrowScroller label="Provider" :active-key="scope.provider" class="av-row av-row--prov">
            <button
              v-if="!isTablet"
              type="button"
              class="av-chip"
              :aria-pressed="scope.provider === '' ? 'true' : 'false'"
              @click="scope.provider = ''"
            >
              All providers
            </button>
            <button
              v-for="p in providerChips"
              :key="p"
              type="button"
              class="av-chip"
              :class="[providerClass(p), { 'is-bad': badProviders.has(p) }]"
              :aria-pressed="scope.provider === p ? 'true' : 'false'"
              @click="pickProvider(p)"
            >
              <span class="av-chip__dot" aria-hidden="true" />{{ p }}
            </button>
          </ArrowScroller>
        </template>
      </div>

      <div v-if="partialText" class="av-banner av-banner--warn" role="status" data-testid="accounts-partial">
        <svg
          class="av-banner__icon"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
        </svg>
        <span>{{ partialText }}</span>
        <button type="button" class="av-banner__btn" :disabled="checkingAll" @click="checkAgain">Check again</button>
      </div>

      <div v-if="pausedHere" class="av-banner" role="status">
        <svg
          class="av-banner__icon"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
        </svg>
        <span
          ><b>{{ pausedHere }} is paused in Settings.</b> Its accounts stay healthy but take no jobs; checks still
          run.</span
        >
        <button type="button" class="av-banner__btn" @click="$router.push({ name: 'settings' })">Open Settings</button>
      </div>

      <AccountsTable
        :rows="rows"
        :size="size"
        :checking="checking"
        :chips="chips"
        :selected="selected"
        :loading="loading"
        :error="error"
        :empty="rows.length ? null : empty"
        @open="openDetail"
        @action="onAction"
        @select="toggleTick"
        @retry="load()"
        @primary="onEmpty"
      />

      <div v-if="!error && (offset > 0 || last < total)" class="av-foot">
        <span class="av-foot__text num"
          >Showing {{ first.toLocaleString("en-US") }}–{{ last.toLocaleString("en-US") }} of
          {{ total.toLocaleString("en-US") }}</span
        >
        <span class="av-foot__btns">
          <IconButton
            icon="prev"
            label="Previous page"
            :disabled="offset === 0 || loading"
            @click="move(offset - limit)"
          />
          <IconButton
            icon="next"
            label="Next page"
            :disabled="last >= total || loading"
            @click="move(offset + limit)"
          />
        </span>
      </div>
    </section>

    <BottomSheet
      v-model="filterOpen"
      title="Provider"
      sub="Show the accounts of one provider"
      testid="accounts-filter-sheet"
    >
      <div class="av-sheet-chips" role="group" aria-label="Provider">
        <button
          type="button"
          class="av-chip"
          :aria-pressed="scope.provider === '' ? 'true' : 'false'"
          @click="scope.provider = ''"
        >
          All providers
        </button>
        <button
          v-for="p in providerChips"
          :key="p"
          type="button"
          class="av-chip"
          :class="providerClass(p)"
          :aria-pressed="scope.provider === p ? 'true' : 'false'"
          @click="pickProvider(p)"
        >
          <span class="av-chip__dot" aria-hidden="true" />{{ p }}
        </button>
      </div>
      <template #footer>
        <button type="button" class="av-done" @click="filterOpen = false">Done</button>
      </template>
    </BottomSheet>

    <AccountDetail
      v-model="detailOpen"
      :account="detail"
      :paused="detail ? pausedOf(detail.provider) : false"
      :checking="!!detail && checking.has(detail.id)"
      :check-chip="detail ? (chips[`check:${detail.id}`] ?? null) : null"
      :proxy-chip="detail ? (chips[`proxy:${detail.id}`] ?? null) : null"
      @action="onDetailAction"
      @proxy="recheck"
    />
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
  gap: 14px;
}
.num {
  font-variant-numeric: tabular-nums;
}
.av-card {
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
/* the toolbar: Show (3 parts) and Provider (2 parts) side by side; stacked on a tablet */
.av-toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  min-height: 38px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--rl-rule);
}
.av-toolbar.is-stacked {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  padding: 16px 24px;
}
.av-row--show {
  flex: 3 1 0;
}
.av-row--prov {
  flex: 2 1 0;
}
.av-toolbar.is-stacked .av-row {
  flex: none;
}
.av-chip {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--rl-line);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.av-chip[aria-pressed="true"] {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.av-chip__n {
  opacity: 0.7;
}
.av-chip__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pc);
}
/* a tablet's chips are 40 px, a phone's 44 px and 14 px (JobsTablet, AccountsPhone) */
.accounts-view--tablet .av-chip {
  height: 40px;
  padding: 0 14px;
  border-color: var(--rl-line-strong);
  color: var(--rl-body);
}
.accounts-view--phone .av-chip {
  height: 44px;
  padding: 0 16px;
  border-color: var(--rl-line-strong);
  color: var(--rl-body);
  font-size: 14px;
}
.accounts-view--tablet .av-chip[aria-pressed="true"],
.accounts-view--phone .av-chip[aria-pressed="true"] {
  border-color: var(--rl-brand);
  color: var(--rl-brand-ink);
}
.av-bulk {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 0 6px 0 14px;
  border-radius: 12px;
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.av-bulk b {
  margin-right: 4px;
}
.av-bulk__btn {
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 8px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.av-bulk :deep(.ib-wrap) {
  margin-left: auto;
}
.av-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  margin: 14px 20px 0;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
  font-size: 13px;
  line-height: 19px;
}
.av-title {
  margin: 0 0 4px;
  font-size: 16px;
  line-height: 24px;
  font-weight: 700;
}
.av-banner__icon {
  flex: none;
  align-self: flex-start;
  margin-top: 2px;
}
.av-banner--warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.av-chip.is-bad {
  border-color: var(--rl-bad);
  color: var(--rl-bad);
}
.av-chip.is-bad .av-chip__dot {
  background: var(--rl-bad);
}
.av-banner > span {
  flex: 1;
  min-width: 0;
}
.av-banner__btn {
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
.av-foot {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  border-top: 1px solid var(--rl-rule);
}
.av-foot__text {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.av-foot__btns {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.av-sheet-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.av-done {
  box-sizing: border-box;
  width: 100%;
  height: 44px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
</style>
