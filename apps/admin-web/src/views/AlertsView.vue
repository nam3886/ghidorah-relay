<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { acknowledgeGroup, type AlertDetail, type AlertGroup, getAlertGroup, listAlertGroups } from "@/api/alerts";
import { getStats, type LiveAlert } from "@/api/shell";
import AlertDetailPanel from "@/components/alerts/AlertDetailPanel.vue";
import {
  ackLabel,
  ackToast,
  type AlertAsk,
  ALERT_KINDS,
  alertTime,
  alertTitle,
  GROUP_LIMIT,
  groupLine,
  liveWord,
  loadFailedText,
  ONE_CUSTOMER_NOTE,
  oneCustomer,
  openSummary,
  ROW_LIMIT,
  seenText,
  type Severity,
  SEVERITIES,
  SEVERITY_CARD,
  severityOf,
} from "@/components/alerts/alerts-model";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import IconButton from "@/components/shared/IconButton.vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import SideDrawer from "@/components/shared/overlays/SideDrawer.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { providerClass } from "@/lib/providers";
import { useStatsStore } from "@/stores/stats";
import { useUiStore } from "@/stores/ui";

// Alerts (Alerts, AlertsTablet, AlertsPhone, AlertsStates, AlertDetail): one card per severity (Critical, Warnings,
// Info), each listing its stored groups (with an Acknowledge) and the live conditions the dashboard computes (no
// acknowledge: they clear themselves). The page has no provider filter (Alerts draws no strip). Titles and reply texts hold
// customer-influenced words, so every one reaches the page as text.
//
// `asked` is what the operator asked for last (the filters and the group whose detail is open); what is drawn (`groups`,
// `live`, `opened`) comes from one answer. A write takes its group from what was drawn, never from `asked`. Every
// load takes the next ticket, and an answer or a failure counts only if no later load started since it was asked.
//
// A desktop draws a group as one line (the provider chip, the title and its line, First seen and Count, the actions);
// a tablet and a phone stack it (the severity and provider chips with the count, the title, the line, the actions). A
// group's detail opens in the right drawer: the title button, a click on the row, or Enter on the title opens it. The
// drawer is shown while a detail is asked for (`asked.open`), reads while it loads and keeps the error of a read that
// failed.
const stats = useStatsStore();
const ui = useUiStore();
const { run } = useBusy();
const latest = useLatest();
const { isPhone, isTablet } = useBreakpoint();
const compact = computed(() => isPhone.value || isTablet.value);

const asked = ref<AlertAsk>({ status: "open", kind: "", open: null });
const groups = shallowRef<AlertGroup[]>([]);
const live = shallowRef<LiveAlert[]>([]);
const opened = shallowRef<AlertDetail | null>(null);
// the last detail that was read: the drawer keeps drawing it while it slides out, and never gets a null
const shownDetail = shallowRef<AlertDetail | null>(null);
const detailError = shallowRef<unknown>(null);
const error = ref<string | null>(null);
const updatedAt = ref<number | null>(null);
const loading = ref(0);
const writing = ref(false);
// While a view loads, only the controls that ask for another view stay usable: they are how it is replaced. While a
// write is in flight nothing is.
const locked = computed(() => writing.value || loading.value > 0);
// a group or a condition of no provider is the relay's own, in grey
const providerOf = (p: string | null | undefined): string => p || "relay";
// A group is named by its kind and its own key; a list wants one unique id per row.
const groupRows = computed(() => groups.value.map((g) => ({ ...g, id: `${g.kind}/${g.group}` })));
type GroupRow = (typeof groupRows.value)[number];

// A live condition is open by nature and has no kind of the stored ones: the Acknowledged view and a Kind filter leave
// them out (filtered as drawn, after the answer).
const liveShown = computed(() => (asked.value.status === "acknowledged" || asked.value.kind ? [] : live.value));
// The cards: a severity with nothing in it is not drawn.
const cards = computed(() =>
  SEVERITIES.map((sev) => ({
    sev,
    ...SEVERITY_CARD[sev],
    groups: groupRows.value.filter((g) => severityOf(g.severity) === sev),
    live: liveShown.value.filter((a) => severityOf(a.severity) === sev),
  })).filter((c) => c.groups.length + c.live.length > 0),
);
// what is open, by severity: the open groups and the live conditions
const summary = computed(() => {
  const n: Record<Severity, number> = { error: 0, warning: 0, info: 0 };
  for (const g of groups.value) if (g.open_count > 0) n[severityOf(g.severity)]++;
  for (const a of liveShown.value) n[severityOf(a.severity)]++;
  return openSummary(n);
});

interface View {
  groups: AlertGroup[];
  live: LiveAlert[];
  opened: AlertDetail | null;
  detailError: unknown;
}

// The list, the live conditions and the open detail, read together so they are drawn together. A detail that cannot
// be read does not fail the rest.
async function read(at: AlertAsk): Promise<View> {
  const [list, stat] = await Promise.all([listAlertGroups(at.status, at.kind, GROUP_LIMIT), getStats("")]);
  let detail: AlertDetail | null = null;
  let detailError: unknown = null;
  if (at.open) {
    try {
      detail = await getAlertGroup(at.open, ROW_LIMIT);
    } catch (err) {
      detailError = err;
    }
  }
  return { groups: list.items, live: stat.alerts, opened: detail, detailError };
}

function draw(view: View, at: AlertAsk): void {
  groups.value = view.groups;
  live.value = view.live;
  opened.value = view.opened;
  error.value = null;
  updatedAt.value = Date.now();
  // the close's own reload asks for no group: it must not wipe the error of a drawer that is leaving
  if (at.open) detailError.value = view.detailError;
}
watch(opened, (d) => {
  if (d) shownDetail.value = d;
});

// Throws the failure of the newest load; a dropped (stale) answer or failure returns quietly.
async function load(): Promise<void> {
  const ticket = latest.next();
  const at = { ...asked.value };
  loading.value++;
  try {
    let view: View;
    try {
      view = await read(at);
    } catch (err) {
      if (!latest.isCurrent(ticket)) return;
      error.value = loadFailedText(err, errorText(err));
      // a drawer still waiting for its group would say "Loading" for ever: it says what failed
      if (at.open && !shownDetail.value) detailError.value = err;
      throw err;
    }
    if (!latest.isCurrent(ticket)) return;
    draw(view, at);
  } finally {
    loading.value--;
  }
}

void run(load);
onBeforeUnmount(() => latest.invalidate());

const reload = () => run(load);

const STATUS_CHOICES = [
  { value: "all", label: "All" },
  { value: "open", label: "Open" },
  { value: "acknowledged", label: "Acknowledged" },
];
const KIND_CHOICES = [{ value: "", label: "All" }, ...ALERT_KINDS.map((k) => ({ value: k, label: k }))];

// A filter changed: ask for that view (the other filter and the open detail stay).
function pick(filter: "status" | "kind", value: string | number): void {
  asked.value =
    filter === "status"
      ? { ...asked.value, status: value as AlertAsk["status"] }
      : { ...asked.value, kind: String(value) };
  void reload();
}
const filtered = computed(() => asked.value.status !== "open" || asked.value.kind !== "");
function clearFilters(): void {
  asked.value = { ...asked.value, status: "open", kind: "" };
  void reload();
}

// The group whose detail is open, as the list marks it.
const isOpened = (g: AlertGroup): boolean => asked.value.open?.kind === g.kind && asked.value.open.group === g.group;
const rowClass = ({ row }: { row: GroupRow }): string =>
  [row.open_count === 0 ? "a-dim" : "", isOpened(row) ? "a-selected" : "", providerClass(providerOf(row.provider))]
    .filter(Boolean)
    .join(" ");

// The state of the list: a first read draws flat bars, a failed one the error box, an empty one its box; the cards
// dim while the next answer is on its way.
const listState = computed(() =>
  error.value
    ? "error"
    : cards.value.length
      ? loading.value > 0
        ? "refreshing"
        : "ready"
      : updatedAt.value
        ? "empty"
        : "loading",
);

// the opener: where focus goes back when the drawer is gone; if it is not there any more (a click on a row, no
// button had focus) focus goes to the page's scroll area, never left on the document
let opener: HTMLElement | null = null;
// the job a click in the detail asked for, opened once the detail has left
let pendingJob: string | null = null;

function openGroup(g: AlertGroup) {
  if (writing.value) return;
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  shownDetail.value = null;
  detailError.value = null;
  pendingJob = null;
  asked.value = { ...asked.value, open: { kind: g.kind, group: g.group } };
  void reload();
}

// Closing also reloads the list, so the cards always match the filters even if one was changed while a load ran.
function closeDetail() {
  // the drawer reports a close once more when it has gone: nothing is left to close then
  if (!asked.value.open) return;
  asked.value = { ...asked.value, open: null };
  opened.value = null;
  void reload();
}

// the detail is gone; a group opened again meanwhile keeps what it has
function detailClosed(): void {
  if (asked.value.open) return;
  shownDetail.value = null;
  detailError.value = null;
  // a job asked for from the detail opens now, once this drawer has left the screen
  if (pendingJob) {
    const id = pendingJob;
    pendingJob = null;
    ui.openJob(id);
    return;
  }
  if (ui.openJobId) return;
  if (opener?.isConnected && opener !== document.body) return;
  document.querySelector<HTMLElement>(".app-scroll__wrap")?.focus({ preventScroll: true });
}

// a click on the row opens it, unless the click was on a button or a link of its own
function rowClick(event: MouseEvent, g: AlertGroup): void {
  if ((event.target as HTMLElement | null)?.closest("button, a")) return;
  openGroup(g);
}

// a job opens in its own drawer: this one closes first and the job opens when it is gone (`detailClosed`), so the two
// are never on the screen together; not while a write runs (the drawer stays until it ends)
function openJob(jobId: string): void {
  if (writing.value) return;
  pendingJob = jobId;
  closeDetail();
}

const detailHeading = computed(() => (shownDetail.value ? alertTitle(shownDetail.value) : "Alert details"));

// `g` is the group as it was drawn, from the list or from the open detail: its newest row is the one the operator saw.
async function acknowledge(g: AlertGroup) {
  await run(async () => {
    writing.value = true;
    try {
      const result = await acknowledgeGroup(g, g.newest_id);
      let failure: unknown = null;
      try {
        await load();
      } catch (err) {
        failure = err;
      }
      const said = ackToast(result.acknowledged);
      if (failure) notify.error(`${said}, but the list could not be reloaded: ${errorText(failure)}`);
      else notify.success(said);
    } finally {
      writing.value = false;
    }
    // the banner counts the open groups
    await stats.refresh();
  });
}

const groupCount = computed(() => `${groups.value.length} ${groups.value.length === 1 ? "group" : "groups"}`);

// the count a stacked row shows at its right: `×3 · since 14:05` on a tablet, `×3` on a phone
const rowRight = (g: AlertGroup): string =>
  isPhone.value || !g.first_ms ? `×${g.count}` : `×${g.count} · since ${alertTime(g.first_ms)}`;

// octagon, triangle and circle alerts (the cards' badges); inbox, search-x and cloud-off (the states)
const ICONS = {
  error: ["M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9Z", "M12 8v4", "M12 16h.01"],
  warning: ["M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z", "M12 9v4", "M12 17h.01"],
  info: ["M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z", "M12 11v5", "M12 8h.01"],
  inbox: ["M3 13h5l1.5 3h5L16 13h5", "M5 5h14l2 8v6H3v-6z"],
  search: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "m20 20-3.5-3.5", "m8.5 8.5 5 5", "m13.5 8.5-5 5"],
  cloud: ["M7 18a5 5 0 0 1-.9-9.9A6 6 0 0 1 17.7 8 4.5 4.5 0 0 1 18 18z", "m3 3 18 18"],
};
const SKELETON = [0, 1, 2];
</script>

<template>
  <section class="alerts-view" :class="{ 'is-compact': compact, 'is-phone': isPhone }">
    <div class="av-head">
      <div v-if="!compact" class="av-head__text">
        <h1>Alerts</h1>
        <p v-if="updatedAt" class="av-sub" data-testid="alerts-summary">
          <b>{{ summary }}</b>
        </p>
      </div>
      <h1 v-else class="sr-only">Alerts</h1>
      <span class="av-head__actions">
        <IconButton icon="rotate" label="Refresh" placement="bottom-end" :disabled="loading > 0" @click="reload" />
      </span>
    </div>
    <p v-if="compact && updatedAt" class="av-sub av-sub--compact" data-testid="alerts-summary">{{ summary }}</p>

    <div class="av-bar" data-testid="alerts-toolbar">
      <FilterSelect
        :model-value="asked.status"
        variant="field"
        label="Status"
        :options="STATUS_CHOICES"
        :disabled="writing"
        @update:model-value="(v) => pick('status', v)"
      />
      <FilterSelect
        :model-value="asked.kind"
        variant="field"
        label="Kind"
        :options="KIND_CHOICES"
        :disabled="writing"
        @update:model-value="(v) => pick('kind', v)"
      />
      <span class="av-bar__count num" data-testid="alerts-count">{{ groupCount }}</span>
    </div>

    <!-- a first read: flat bars in a card, no shimmer -->
    <section
      v-if="listState === 'loading'"
      class="av-card"
      data-state="loading"
      aria-busy="true"
      aria-label="Loading the alerts"
    >
      <div v-for="i in SKELETON" :key="i" class="av-skel">
        <span class="bar bar--chip" /><span class="bar bar--title" /><span class="bar bar--meta" />
      </div>
    </section>

    <!-- a failed read: Retry reads again -->
    <section v-else-if="listState === 'error'" class="av-card av-box" role="alert" data-testid="alerts-error">
      <span class="av-box__icon is-bad"><PathIcon :paths="ICONS.cloud" :size="22" :stroke="1.8" /></span>
      <b class="av-box__title" data-testid="alerts-error-text">{{ error }}</b>
      <span class="av-box__text">The last good read stays hidden so stale alerts are not mistaken for live ones.</span>
      <button type="button" class="av-primary" @click="reload">Retry</button>
    </section>

    <!-- nothing open, or nothing under the filters -->
    <section v-else-if="listState === 'empty'" class="av-card av-box" role="status" data-testid="alerts-empty">
      <template v-if="filtered">
        <span class="av-box__icon"><PathIcon :paths="ICONS.search" :size="22" :stroke="1.8" /></span>
        <b class="av-box__title">No alert groups with this filter</b>
        <span class="av-box__text">Nothing matches the status, kind and provider chosen.</span>
        <button type="button" class="av-primary" @click="clearFilters">Clear filters</button>
      </template>
      <template v-else>
        <span class="av-box__icon is-brand"><PathIcon :paths="ICONS.inbox" :size="22" :stroke="1.8" /></span>
        <b class="av-box__title">No alerts</b>
        <span class="av-box__text">Nothing needs a look.</span>
      </template>
    </section>

    <!-- one card per severity -->
    <div v-else class="av-cards" :data-state="listState">
      <section
        v-for="c in cards"
        :key="c.sev"
        class="av-card"
        :aria-label="c.label"
        :data-testid="`alerts-card-${c.sev}`"
      >
        <div class="av-card__head">
          <span v-if="!compact" class="av-badge" :class="`tone-${c.tone}`"
            ><PathIcon :paths="ICONS[c.sev]" :size="16" :stroke="2.2"
          /></span>
          <h2>{{ c.label }}</h2>
          <span v-if="!compact" class="av-count num" :class="`tone-${c.tone}`">{{
            c.groups.length + c.live.length
          }}</span>
          <span v-if="!isPhone" class="av-card__note">{{ compact ? c.note.toLowerCase() : c.note }}</span>
        </div>

        <div
          v-for="g in c.groups"
          :key="g.id"
          class="av-item"
          :class="rowClass({ row: g })"
          data-testid="alert-group"
          @click="rowClick($event, g)"
        >
          <span v-if="compact" class="av-item__top">
            <span class="sev" :class="`tone-${c.tone}`" :data-tone="c.tone">{{ c.word }}</span>
            <span class="pchip">{{ providerOf(g.provider) }}</span>
            <span class="av-item__right num">{{ rowRight(g) }}</span>
          </span>
          <span v-else class="pchip pchip--wide">{{ providerOf(g.provider) }}</span>
          <div class="av-item__main">
            <button
              type="button"
              class="g-title g-open-btn"
              aria-haspopup="dialog"
              :disabled="writing"
              :aria-label="`Details of ${alertTitle(g)}`"
              @click="openGroup(g)"
            >
              {{ alertTitle(g) }}
            </button>
            <span class="g-kind num">{{ groupLine(g) }}</span>
            <span v-if="oneCustomer(g)" class="g-note">{{ ONE_CUSTOMER_NOTE }}</span>
          </div>
          <dl v-if="!compact" class="av-meta">
            <div>
              <dt>First seen</dt>
              <dd class="num">{{ seenText(g.first_ms) }}</dd>
            </div>
            <div>
              <dt>Count</dt>
              <dd class="num">×{{ g.count }}</dd>
            </div>
          </dl>
          <div class="av-item__acts">
            <span v-if="g.open_count === 0" class="state">Acknowledged</span>
            <IconButton
              v-if="g.open_count > 0"
              icon="check"
              label="Acknowledge"
              :disabled="locked"
              @click="acknowledge(g)"
            />
          </div>
        </div>

        <div
          v-for="a in c.live"
          :key="`${a.kind}:${a.message}`"
          class="av-item is-live"
          :class="providerClass(providerOf(a.provider))"
          data-testid="alert-live"
        >
          <span v-if="compact" class="av-item__top">
            <span class="sev" :class="`tone-${c.tone}`" :data-tone="c.tone">{{ c.word }}</span>
            <span class="pchip">{{ providerOf(a.provider) }}</span>
            <span class="av-item__right">live</span>
          </span>
          <span v-else class="pchip pchip--wide">{{ providerOf(a.provider) }}</span>
          <div class="av-item__main">
            <span class="g-title">{{ liveWord(a.kind) }}: {{ a.message }}</span>
            <span class="g-kind">Computed now: it cannot be acknowledged and clears when the condition does.</span>
          </div>
          <dl v-if="!compact" class="av-meta">
            <div>
              <dt>First seen</dt>
              <dd>now</dd>
            </div>
            <div>
              <dt>Count</dt>
              <dd>live</dd>
            </div>
          </dl>
          <div class="av-item__acts"><span class="state">Clears by itself</span></div>
        </div>
      </section>
      <p v-if="groups.length >= GROUP_LIMIT" class="alerts-more" data-testid="alerts-more">
        Showing the newest {{ GROUP_LIMIT }} groups; older groups are not listed.
      </p>
    </div>

    <SideDrawer
      :model-value="!!asked.open"
      :title="detailHeading"
      testid="alert-detail"
      heading-testid="alert-detail-title"
      back-label="Back to Alerts"
      :busy="writing"
      @update:model-value="(o) => !o && closeDetail()"
      @back="closeDetail"
      @closed="detailClosed"
    >
      <template v-if="shownDetail" #sub>
        <span
          class="sev sev--lg"
          :class="`tone-${SEVERITY_CARD[severityOf(shownDetail.severity)].tone}`"
          :data-tone="SEVERITY_CARD[severityOf(shownDetail.severity)].tone"
          >{{ SEVERITY_CARD[severityOf(shownDetail.severity)].word }}</span
        >
        <span class="pchip" :class="providerClass(providerOf(shownDetail.provider))">{{
          providerOf(shownDetail.provider)
        }}</span>
        <span class="ad-help num"
          >{{ shownDetail.open_count }} open of {{ shownDetail.count }} rows · since
          {{ alertTime(shownDetail.first_ms) }}</span
        >
      </template>
      <p v-if="detailError" class="ad-error" role="alert" data-testid="alert-detail-error">
        {{ errorText(detailError) }}
      </p>
      <AlertDetailPanel v-else-if="shownDetail" :detail="shownDetail" :writing="writing" @open-job="openJob" />
      <p v-else class="ad-loading" role="status">Loading the group…</p>
      <template v-if="shownDetail && !detailError && shownDetail.open_count > 0" #footer>
        <button type="button" class="av-primary ad-ack" :disabled="locked" @click="acknowledge(shownDetail)">
          {{ ackLabel(shownDetail.open_count) }}
        </button>
      </template>
    </SideDrawer>
  </section>
</template>

<style scoped>
.alerts-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.alerts-view.is-phone {
  gap: 14px;
}

/* the head: the title and the summary, the icon buttons at the right */
.av-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
}
.av-head__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.av-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.av-sub {
  margin: 0;
  color: var(--rl-muted);
}
.av-sub b {
  color: var(--rl-fg);
  font-weight: 700;
}
.av-sub--compact {
  margin-top: -12px;
  font-size: 13px;
  line-height: 1.55;
}
.is-phone .av-sub--compact {
  margin-top: -8px;
}
.av-head__actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.is-compact .av-head {
  justify-content: flex-end;
  gap: 0;
}

/* the filters and the count */
.av-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
}
.av-bar__count {
  margin-left: auto;
  font-size: 13px;
  line-height: 34px;
  color: var(--rl-muted);
}
.is-phone .av-bar {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}
.is-phone .av-bar :deep(.filter-field.is-chip) {
  width: 100%;
}
.is-phone .av-bar :deep(.filter-field.is-chip .filter-select.is-field) {
  flex: 1;
  text-align: left;
}
.is-phone .av-bar__count {
  margin-left: 0;
  line-height: 20px;
}

/* the cards */
.av-cards {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.is-phone .av-cards {
  gap: 14px;
}
.av-cards[data-state="refreshing"] .av-card {
  opacity: 0.6;
  transition: opacity 0.15s;
}
.av-card {
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.av-card__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  background: var(--rl-head);
  border-bottom: 1px solid var(--rl-rule);
}
.av-card__head h2 {
  margin: 0;
  font-size: 16px;
  line-height: 22px;
  font-weight: 700;
}
.av-card__note {
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-faint);
}
.av-badge {
  display: grid;
  place-items: center;
  flex: none;
  width: 32px;
  height: 32px;
  border-radius: 10px;
}
.av-count {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}
.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.tone-info {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}

/* a group: one wrapping line on a desktop */
.av-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 20px;
  padding: 16px 20px;
  border-top: 1px solid var(--rl-rule);
  cursor: pointer;
}
.av-card__head + .av-item {
  border-top: 0;
}
.av-item.is-live {
  cursor: default;
}
.av-item.a-dim {
  opacity: 0.6;
}
.av-item.a-selected {
  background: var(--rl-primary-soft);
}
.av-item:not(.a-selected):not(.is-live):hover {
  background: var(--rl-head);
}
.av-item__main {
  display: flex;
  flex-direction: column;
  gap: 3px;
  flex: 999 1 380px;
  min-width: 0;
}
.g-title {
  font-weight: 700;
  font-size: 15px;
  line-height: 22px;
  overflow-wrap: anywhere;
}
.g-open-btn {
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.g-open-btn:hover:not(:disabled) {
  color: var(--rl-brand-ink);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.g-open-btn:disabled {
  cursor: default;
}
.g-kind,
.g-note {
  font-size: 13px;
  line-height: 19px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.g-note {
  font-size: 12px;
  color: var(--rl-faint);
}
.av-meta {
  display: flex;
  gap: 24px;
  flex: none;
  min-width: 170px;
  margin: 0;
}
.av-meta div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.av-meta dt {
  font-size: 12px;
  color: var(--rl-faint);
}
.av-meta dd {
  margin: 0;
  font-weight: 700;
  color: var(--rl-body);
  white-space: nowrap;
}
.av-item__acts {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex: none;
  min-width: 180px;
}
.state {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}

/* the provider chip and the severity pill: one pill shape */
.pchip,
.sev {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  flex: none;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.pchip {
  background: var(--ps);
  color: var(--pi);
}
.pchip--wide {
  min-width: 64px;
  height: 24px;
  padding: 0 10px;
}
.sev--lg {
  height: 24px;
}

/* a tablet and a phone: the group stacks (chips and count, title, line, actions) */
.is-compact .av-card {
  padding-top: 16px;
}
.is-compact .av-card__head {
  align-items: baseline;
  gap: 4px 10px;
  padding: 0 24px 12px;
  background: transparent;
}
.is-compact .av-card__head h2 {
  font-size: 15px;
}
.is-compact .av-card__note {
  margin-left: 0;
}
.is-compact .av-item {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
  gap: 6px;
  min-height: 44px;
  padding: 14px 24px;
}
.is-compact .av-card__head + .av-item {
  border-top: 1px solid var(--rl-rule);
}
.is-compact .av-item__main {
  flex: none;
  gap: 6px;
}
.is-compact .g-title {
  line-height: 21px;
}
.is-compact .g-kind {
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-faint);
}
.is-compact .av-item__acts {
  justify-content: flex-start;
  min-width: 0;
}
.is-compact .av-item__acts:empty {
  display: none;
}
.av-item__top {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.av-item__right {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-muted);
  white-space: nowrap;
}
.is-phone .av-card__head,
.is-phone .av-item {
  padding-left: 16px;
  padding-right: 16px;
}

.alerts-more {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}

/* the first read: flat bars */
.av-skel {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 22px 20px;
  border-top: 1px solid var(--rl-rule);
}
.av-skel:first-child {
  border-top: 0;
}
.bar {
  display: block;
  height: 12px;
  border-radius: 6px;
  background: var(--rl-skel);
}
.bar--chip {
  width: 64px;
}
.bar--title {
  flex: 1;
  max-width: 360px;
}
.bar--meta {
  width: 120px;
  margin-left: auto;
}

/* empty and failed: a box in a card */
.av-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 40px 24px;
  text-align: center;
}
.av-box__icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.av-box__icon.is-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.av-box__icon.is-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.av-box__title {
  font-size: 15px;
  overflow-wrap: anywhere;
}
.av-box__text {
  max-width: 420px;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.av-primary {
  height: 36px;
  margin-top: 4px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.av-primary:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.av-primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.is-phone .av-primary {
  height: 44px;
}

/* the drawer: the line under the chips, the footer's Acknowledge at the right, the loading and failed bodies */
.ad-help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.ad-ack {
  margin: 0 0 0 auto;
}
.ad-loading,
.ad-error {
  margin: 0;
  color: var(--rl-muted);
}
.ad-error {
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
  font-size: 13px;
}
</style>

<!-- the drawer's title is the group's name as drawn: 18/26, 800 -->
<style>
[data-testid="alert-detail"] .rl-drawer__title {
  font-weight: 800;
  letter-spacing: -0.01em;
}
</style>
