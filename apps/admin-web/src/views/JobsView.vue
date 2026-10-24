<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  bulkRecheck,
  bulkRegenerate,
  IN_FLIGHT,
  JOB_RANGES,
  JOB_STATUSES,
  type JobRange,
  NEEDS_A_LOOK,
  type JobSummary,
  type KeyOption,
  listJobs,
  listKeyOptions,
} from "@/api/jobs";
import AttentionBanner from "@/components/layout/AttentionBanner.vue";
import JobsTable from "@/components/jobs/JobsTable.vue";
import JobsToday from "@/components/jobs/JobsToday.vue";
import { PAGE_SIZE, PAGE_SIZES } from "@/components/jobs/paging";
import { ALL, fromSelect, rememberedStatus, toSelect } from "@/components/jobs/job-filters";
import { jobChanges } from "@/components/jobs/job-changes";
import { dayKey, localZone } from "@/components/jobs/job-row";
import { keyName, loadFailedText, recheckToast, regenerateToast, shortId } from "@/components/jobs/job-text";
import RegenerateControl from "@/components/jobs/RegenerateControl.vue";
import { pickedIds } from "@/components/jobs/selection";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import { copyText } from "@/components/shared/clipboard";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { IconSearch } from "@/components/shared/icons";
import ListPager from "@/components/shared/ListPager.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { groupByDay, isDayRow } from "@/lib/day-rows";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { bulkAction } from "@/lib/operation-key";
import { useProvidersStore } from "@/stores/providers";
import { useScopeStore } from "@/stores/scope";
import { useStatsStore } from "@/stores/stats";
import { useUiStore } from "@/stores/ui";

// Jobs (Ledger.dc.html, JobsTablet, JobsPhone, JobsStates): the head with New test job, the Today section (the hourly
// chart, the figures and the provider cards, whose click scopes the list), the attention banner, and the Recent jobs
// card: status chips, the key filter, the bulk bar while jobs are ticked, the table and the pager. Newest first, 10 a
// page by offset with the server's total, grouped by day. The provider scope, the status (kept in memory) and the key
// all restart the paging. A job opens in the shared detail drawer through ui.openJob (also from a link:
// #/jobs?job=<id>). The list and the provider summary read again every 10 s while the tab is shown.
const route = useRoute();
const router = useRouter();
const scope = useScopeStore();
const ui = useUiStore();
const stats = useStatsStore();
const providers = useProvidersStore();
const { isPhone, isTablet } = useBreakpoint();
const compact = computed(() => isPhone.value || isTablet.value);

const POLL_MS = 10_000;
const SEARCH_PAUSE_MS = 300;
// The zone the days are cut in: the browser's own, the one the clock and the day headers use too.
const zone = localZone();
const dayTotals = shallowRef<Record<string, number>>({});

const items = shallowRef<JobSummary[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
// the clock the Age column counts from: the time of the last answer
const now = ref(Date.now());
const offset = ref(0);
const limit = ref(PAGE_SIZE);
const total = ref(0);
const latest = useLatest();

const status = rememberedStatus;
const keyId = ref("");
const keys = ref<KeyOption[]>([]);
// The range switch (Ledger: Today, 7 days, 30 days): the list's window and the Today section's figures.
const range = ref<JobRange>("today");
// The search box: sent once typing pauses; the start of a job id, the prompt or title, a key's or account's label.
const search = ref("");
const q = ref("");
const searchOpen = ref(false);
let typing: ReturnType<typeof setTimeout> | undefined;
watch(search, (text) => {
  clearTimeout(typing);
  typing = setTimeout(() => (q.value = text.trim()), SEARCH_PAUSE_MS);
});
// The jobs of each status under the other filters (the server's status_counts): the grouped chips' counts.
const counts = shallowRef<Record<string, number>>({});
const countOf = (statuses: string): number => statuses.split(",").reduce((n, s) => n + (counts.value[s] ?? 0), 0);
// The chips as drawn (Ledger): All, Needs a look · N, In flight · N, Done. A single status that came another way (a
// stats link: ?status=failed) gets a chip of its own after them while it is the filter, so what is on stays shown.
const capital = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);
const statusOptions = computed(() => [
  { value: ALL, label: "All" },
  { value: NEEDS_A_LOOK, label: `Needs a look · ${countOf(NEEDS_A_LOOK)}` },
  { value: IN_FLIGHT, label: `In flight · ${countOf(IN_FLIGHT)}` },
  { value: "done", label: "Done" },
  ...(status.value && !status.value.includes(",") && status.value !== "done"
    ? [{ value: status.value, label: capital(status.value) }]
    : []),
]);
// The status filter in words: the chip's name without its count.
const statusWords = computed(() =>
  status.value === NEEDS_A_LOOK ? "Needs a look" : status.value === IN_FLIGHT ? "In flight" : capital(status.value),
);
const keyOptions = computed(() => [
  { value: ALL, label: "All" },
  ...keys.value.map((k) => ({ value: k.id, label: keyName(k) })),
]);

// The ticked jobs: what the bulk buttons act on is always the ticks on screen. The view owns them and the table only
// reports a click, so a table drawn anew (an error hid it, Retry drew it again) shows them as they are.
// - A load keeps the ticks of the jobs still on the page, another page starts with none.
// - A filter, a page size or a phone (which has no tick boxes) drops them at once.
// - An error hides the table: nothing is ticked on screen then, so nothing is acted on.
// Always read through pickedIds: only what is on the page is acted on, in table order.
const picked = ref<Set<string>>(new Set());
const ids = computed(() => (error.value ? [] : pickedIds(items.value, picked.value)));

function onToggle(id: string, on: boolean): void {
  const next = new Set(picked.value);
  if (on) next.add(id);
  else next.delete(id);
  picked.value = next;
}
const onToggleAll = (on: boolean): void => {
  picked.value = on ? new Set(items.value.map((j) => j.id)) : new Set();
};
const clearPicked = (): void => {
  picked.value = new Set();
};

// The stats cells link to /jobs?status=done|failed; a key id may come the same way, and a job id opens its drawer.
function applyQuery(): void {
  const wanted = route.query.status;
  if (typeof wanted === "string" && (JOB_STATUSES as readonly string[]).includes(wanted)) status.value = wanted;
  if (typeof route.query.key_id === "string") keyId.value = route.query.key_id;
  if (typeof route.query.job === "string" && route.query.job) ui.openJob(route.query.job);
}

// Loads the page that starts at `at` (the one on screen by default, so a reload after a bulk action stays on it). Only
// the newest answer counts; a failed page move leaves the pager where it was. A list that shrank under the page on
// screen comes back empty, and the pager then moves itself to its last page.
async function load(at: number = offset.value): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listJobs({
      status: status.value,
      provider: scope.provider,
      keyId: keyId.value,
      tz: zone,
      range: range.value,
      q: q.value,
      offset: at,
      limit: limit.value,
    });
    if (!latest.isCurrent(ticket)) return;
    // Another page starts with nothing ticked, even a job that moved onto it; a reload of this page keeps the ticks
    // of the jobs still on it.
    if (at !== offset.value) clearPicked();
    items.value = out.items;
    total.value = out.total;
    dayTotals.value = out.day_totals ?? {};
    counts.value = out.status_counts ?? {};
    hasJobs.value = out.has_jobs ?? false;
    offset.value = at;
    picked.value = new Set(pickedIds(out.items, picked.value));
    error.value = null;
    now.value = Date.now();
  } catch (err) {
    if (latest.isCurrent(ticket)) error.value = loadFailedText(err, errorText(err));
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}

// A new page size starts again from the first page.
function onSize(size: number): void {
  limit.value = size;
  clearPicked();
  void load(0);
}

applyQuery();
// A filter drops the ticks at once: the old rows still on screen until the new list arrives show none.
// The range moves the Today section's figures too.
watch(range, (r) => {
  providers.range = r;
  void providers.refresh();
});
watch([status, keyId, () => scope.provider, range, q], () => {
  offset.value = 0;
  clearPicked();
  // the totals belong to the old filters: until the new answer lands the headers count the rows on screen
  dayTotals.value = {};
  void load(0);
});
watch(() => route.query, applyQuery);
watch(jobChanges, () => void load());
watch(isPhone, (phone) => phone && clearPicked());

// Reads again while the tab is shown; a read still under way is not overtaken (it would draw an older page).
let timer: ReturnType<typeof setInterval> | undefined;
function refreshAll(): void {
  void load();
  void stats.refresh();
  void providers.refresh();
}
function poll(): void {
  if (document.hidden || loading.value) return;
  refreshAll();
}
const onVisible = (): void => {
  if (!document.hidden) poll();
};

onMounted(() => {
  void load(0);
  void providers.refresh();
  listKeyOptions().then(
    (out) => (keys.value = out.items),
    (err) => notify.error(errorText(err)),
  );
  timer = setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", onVisible);
});
onBeforeUnmount(() => {
  clearInterval(timer);
  clearTimeout(typing);
  // the strip and Settings read the shared summary as today's
  if (providers.range !== "today") {
    providers.range = "today";
    void providers.refresh();
  }
  document.removeEventListener("visibilitychange", onVisible);
});

const { busy: rechecking, run } = useBusy();
const recheck = () =>
  run(async () => {
    const sent = ids.value;
    if (!sent.length) {
      notify.error("Tick at least one job");
      return;
    }
    const out = await bulkRecheck(sent);
    clearPicked();
    await load();
    const toast = recheckToast(out);
    notify[toast.type](toast.text);
  });

// Runs once the confirm is accepted, on the ticks on screen then: while the confirm is open the table cannot be
// clicked, so only a reload can change them, and only by dropping jobs (one that left the page, or every tick when
// the reload failed). A failure leaves the selection as it was: the retry of the same selection sends the same key,
// and a different selection is a different action with its own key.
async function regenerate(): Promise<void> {
  if (!ids.value.length) {
    notify.error("Tick at least one job");
    return;
  }
  const out = await bulkRegenerate(ids.value);
  clearPicked();
  await load();
  const toast = regenerateToast(out);
  notify[toast.type](toast.text);
}

const regenerateConfirm = computed(() => ({
  title: "Regenerate",
  message: `Regenerate ${ids.value.length} job(s)? Each makes a NEW job at today’s price and charges its key.`,
  confirmText: "Regenerate",
  danger: true,
}));

// Day headers. Today's says how many of its jobs are on this page ("8 jobs shown"). Every earlier day says how many
// jobs that day has under the current filters (the server's day_totals, counted in the same zone the days are cut in:
// "212 jobs"); an earlier day the server gave no total for says how many are on this page.
const jobs = (n: number) => `${n} ${n === 1 ? "job" : "jobs"}`;
const grouped = computed(() => {
  const out = groupByDay(items.value, { at: (j) => j.created_ms, note: (n) => `${jobs(n)} shown` });
  const today = dayKey(Date.now(), zone);
  out.items.forEach((item, i) => {
    const first = out.items[i + 1];
    if (!isDayRow(item) || !first || isDayRow(first)) return;
    const day = dayKey((first as JobSummary).created_ms, zone);
    const total = dayTotals.value[day];
    if (day !== today && total !== undefined) item.note = jobs(total);
  });
  return out;
});

// An empty list is "nothing matches" under a filter, or under a range that leaves out jobs the relay has (the server's
// has_jobs); only a relay with no job at all shows the first-run box.
const hasJobs = ref(false);
const filtered = computed(() => !!(status.value || keyId.value || q.value || (hasJobs.value && range.value !== "30d")));
const rangeLabel = computed(() => JOB_RANGES.find((r) => r.value === range.value)!.label);
// JobsStates 03: the filters that are on, as chips that each take theirs off, and in words in the empty box.
const activeChips = computed(() =>
  [
    status.value && { id: "status", label: statusWords.value, clear: () => (status.value = "") },
    keyId.value && {
      id: "key",
      label: `Key: ${keyOptions.value.find((k) => k.value === keyId.value)?.label ?? keyId.value}`,
      clear: () => (keyId.value = ""),
    },
    q.value && { id: "q", label: `“${q.value}”`, clear: () => (search.value = q.value = "") },
    range.value !== "30d" && { id: "range", label: rangeLabel.value, clear: () => (range.value = "30d") },
  ].filter((c): c is { id: string; label: string; clear: () => string } => !!c),
);
const noMatch = computed(() => filtered.value && !loading.value && !error.value && total.value === 0);
const filterNote = computed(() => {
  const key = keyId.value ? ` for ${keyOptions.value.find((k) => k.value === keyId.value)?.label ?? "this key"}` : "";
  const what = status.value ? `Nothing is “${statusWords.value}”` : "No job";
  const words = q.value ? ` matches “${q.value}”` : status.value ? "" : " was made";
  const when = range.value === "today" ? "today" : `in the last ${range.value === "7d" ? "7" : "30"} days`;
  return `${what}${key}${words} ${when}.`;
});
function clearFilters(): void {
  status.value = "";
  keyId.value = "";
  clearTimeout(typing);
  search.value = "";
  q.value = "";
  range.value = "30d";
}

const newTest = (): void => void router.push({ name: "test" });
async function copyId(id: string): Promise<void> {
  const outcome = await copyText(id);
  if (outcome === "clipboard" || outcome === "exec") notify.success(`Copied job id ${shortId(id)}`);
  else notify.error("Could not copy: select the text and copy it by hand");
}
function openInNewTab(id: string): void {
  const href = router.resolve({ name: "jobs", query: { job: id } }).href;
  window.open(href, "_blank", "noopener");
}
</script>

<template>
  <section class="jobs" :class="{ 'jobs--phone': isPhone, 'jobs--tablet': isTablet }">
    <div class="jobs__head">
      <h1 :class="{ 'sr-only': compact }">Jobs</h1>
      <div class="jobs__range" role="group" aria-label="Range">
        <button
          v-for="r in JOB_RANGES"
          :key="r.value"
          type="button"
          class="jobs__range-btn"
          :class="{ 'is-on': range === r.value }"
          :aria-pressed="range === r.value"
          @click="range = r.value"
        >
          {{ r.label }}
        </button>
      </div>
      <template v-if="isPhone">
        <IconButton
          icon="search"
          label="Search jobs"
          placement="bottom-end"
          :class="{ 'is-on': searchOpen }"
          @click="searchOpen = !searchOpen"
        />
        <IconButton icon="plus" label="New test job" placement="bottom-end" @click="newTest" />
      </template>
      <template v-else>
        <!-- a tablet (JobsTablet): Search jobs beside New test job opens the search box in the toolbar -->
        <button
          v-if="isTablet"
          type="button"
          class="jobs__find"
          :aria-pressed="searchOpen"
          @click="searchOpen = !searchOpen"
        >
          Search jobs
        </button>
        <button type="button" class="jobs__new" @click="newTest">New test job</button>
      </template>
    </div>

    <JobsToday
      :summary="providers.data"
      :alerts="stats.data?.alerts"
      :selected="scope.provider"
      @pick="(p: string) => (scope.provider = p)"
    />

    <AttentionBanner />

    <section class="jobs__card" aria-label="Recent jobs">
      <div class="jobs__bar">
        <h2 v-if="!isPhone" class="jobs__title">Recent jobs</h2>
        <div v-if="noMatch" class="jobs__chips jobs__active" role="group" aria-label="Filters on">
          <button
            v-for="c in activeChips"
            :key="c.id"
            type="button"
            class="chip is-on"
            :aria-label="`Remove the filter ${c.label}`"
            @click="c.clear()"
          >
            {{ c.label }} <span aria-hidden="true">×</span>
          </button>
        </div>
        <div v-else class="jobs__chips">
          <ArrowScroller label="Filter jobs" :count="statusOptions.length" :active-key="status">
            <button
              v-for="o in statusOptions"
              :key="o.value"
              type="button"
              class="chip"
              :class="{ 'is-on': toSelect(status) === o.value }"
              :aria-pressed="toSelect(status) === o.value"
              @click="status = fromSelect(String(o.value))"
            >
              {{ o.label }}
            </button>
          </ArrowScroller>
        </div>
        <FilterSelect
          class="jobs__filter"
          label="Key"
          :model-value="toSelect(keyId)"
          :options="keyOptions"
          @update:model-value="(v) => (keyId = fromSelect(String(v)))"
        />
        <label v-if="!compact || searchOpen || search" class="jobs__search">
          <IconSearch :size="16" :stroke="2" />
          <input
            v-model="search"
            type="search"
            aria-label="Search jobs"
            placeholder="Search title, id, account"
            maxlength="200"
            @keydown.esc="search = ''"
          />
        </label>
        <div v-if="!isPhone && ids.length" class="jobs__bulk" role="region" aria-label="Bulk actions">
          <b class="num jobs__count">{{ ids.length }} selected</b>
          <button type="button" class="jobs__bulk-btn" :disabled="rechecking" @click="recheck">Re-check</button>
          <RegenerateControl
            :action="bulkAction(ids)"
            label="Regenerate…"
            :disabled="!ids.length"
            :confirm="regenerateConfirm"
            :perform="regenerate"
          />
          <IconButton icon="close" label="Clear the selection" @click="clearPicked" />
        </div>
      </div>

      <JobsTable
        :rows="grouped.items"
        :picked="picked"
        :loading="loading"
        :error="error"
        :filtered="filtered"
        :filter-note="filterNote"
        :now="now"
        @toggle="onToggle"
        @toggle-all="onToggleAll"
        @open="ui.openJob"
        @copy="copyId"
        @new-tab="openInNewTab"
        @retry="load()"
        @clear-filters="clearFilters"
        @new-test="newTest"
      />

      <!-- the empty, loading and error boxes have no pager; it stays mounted (hidden) so it can still move itself back
           to the last page when the list shrinks under the page on screen -->
      <ListPager
        v-show="total > 0 && !error"
        :offset="offset"
        :limit="limit"
        :total="total"
        :sizes="PAGE_SIZES"
        @move="(o: number) => load(o)"
        @size="onSize"
      />
    </section>
  </section>
</template>

<style scoped>
.jobs {
  display: flex;
  flex-direction: column;
  gap: 24px;
  min-width: 0;
}
.jobs--tablet {
  gap: 20px;
}
.jobs--phone {
  gap: 14px;
}
.num {
  font-variant-numeric: tabular-nums;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* the head: the title, New test job at the right */
.jobs__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}
.jobs__head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.jobs__new {
  margin-left: auto;
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  box-shadow:
    0 1px 2px rgba(16, 24, 40, 0.12),
    inset 0 -1px 0 rgba(0, 0, 0, 0.12);
  cursor: pointer;
}
.jobs--tablet .jobs__new {
  height: 40px;
  font-weight: 700;
}
.jobs__find {
  margin-left: auto;
  height: 40px;
  padding: 0 16px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.jobs__find[aria-pressed="true"] {
  border-color: var(--rl-brand);
  color: var(--rl-brand-ink);
}
.jobs__find + .jobs__new {
  margin-left: 0;
}
.jobs__find:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.jobs__active {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.jobs__new:hover {
  background: var(--rl-primary-hover);
}
.jobs__new:focus-visible,
.chip:focus-visible,
.jobs__bulk-btn:focus-visible,
.jobs__bulk :deep(.ib:focus-visible) {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.jobs__range + :deep(.ib-wrap) {
  margin-left: auto;
}
.jobs--phone .jobs__head {
  margin: -6px 0 -4px;
}
.jobs__head :deep(.ib) {
  color: var(--rl-brand-ink);
}
.jobs__head :deep(.ib-wrap:first-of-type .ib) {
  color: var(--rl-body);
}

/* the range switch (Ledger): a segmented control after the title */
.jobs__range {
  display: flex;
  gap: 2px;
  margin-left: 8px;
  padding: 3px;
  border-radius: 10px;
  background: var(--rl-track);
}
.jobs--phone .jobs__range {
  margin-left: 0;
}
.jobs__range-btn {
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  white-space: nowrap;
  cursor: pointer;
}
.jobs__range-btn.is-on {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
}
.jobs__range-btn:focus-visible,
.jobs__search:focus-within {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.jobs--phone .jobs__range-btn {
  height: 38px;
}

/* the search box at the end of the toolbar (Ledger: 260 px) */
.jobs__search {
  display: flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  width: 260px;
  max-width: 100%;
  height: 34px;
  margin-left: auto;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  color: var(--rl-faint);
}
.jobs__search input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
}
.jobs--phone .jobs__search {
  width: 100%;
  height: 44px;
  background: var(--rl-surface);
}

/* the card */
.jobs__card {
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.jobs__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
}
.jobs__title {
  flex: none;
  margin: 0 12px 0 0;
  font-size: 16px;
  font-weight: 700;
}
.jobs__chips {
  flex: 1 1 0;
  min-width: 0;
}
.jobs--tablet .jobs__bar {
  padding: 16px 24px 12px;
}
.jobs--tablet .jobs__title {
  flex-basis: 100%;
  font-size: 15px;
}
/* a phone: the chips sit on the page between the Today card and the list, which is a card of its own */
.jobs--phone .jobs__card {
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: visible;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.jobs--phone .jobs__bar {
  padding: 0;
}
.jobs--phone .jobs__card > :deep(.jl),
.jobs--phone .jobs__card > :deep(.pager) {
  overflow: hidden;
  border-top: 0;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.chip {
  flex: none;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-body);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.chip.is-on {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.jobs--tablet .chip {
  height: 40px;
  padding: 0 14px;
}
.jobs--phone .chip {
  height: 44px;
  padding: 0 16px;
  font-size: 14px;
}

/* the bulk bar: dark, at the end of the toolbar */
.jobs__bulk {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  margin-left: auto;
  padding: 0 4px 0 12px;
  border-radius: 10px;
  background: #1a1b25;
  color: #ffffff;
  font-size: 13px;
}
html.dark .jobs__bulk {
  background: #262838;
  color: #ecedf3;
}
.jobs__count {
  margin-right: 4px;
  white-space: nowrap;
}
.jobs__bulk-btn,
.jobs__bulk :deep(.regenerate__go.el-button) {
  height: 28px;
  margin: 0;
  padding: 0 10px;
  border: 0;
  border-radius: 7px;
  background: #34364a;
  color: #ffffff;
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  line-height: normal;
  cursor: pointer;
}
html.dark .jobs__bulk-btn,
html.dark .jobs__bulk :deep(.regenerate__go.el-button) {
  background: #3a3d52;
}
.jobs__bulk :deep(.regenerate__go.el-button:hover),
.jobs__bulk :deep(.regenerate__go.el-button:focus-visible),
.jobs__bulk-btn:hover {
  background: #45485e;
  color: #ffffff;
}
.jobs__bulk-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.jobs__bulk :deep(.regenerate .el-button.is-link) {
  color: #fcd34d;
  font-size: 13px;
}
.jobs__bulk :deep(.ib) {
  width: 28px;
  height: 28px;
  border-radius: 7px;
  color: #c3c6d4;
}
html.dark .jobs__bulk :deep(.ib) {
  color: #a3a6b8;
}
.jobs__bulk :deep(.ib:hover) {
  background: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}
</style>
