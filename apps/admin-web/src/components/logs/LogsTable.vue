<script setup lang="ts">
import { computed } from "vue";
import type { LogRow } from "@/api/logs";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { providerClass } from "@/lib/providers";
import { clockMs, clockTime, durationText, errorTone, isSlow, logKey, splitRoute, statusTone } from "./log-window";

// The request list, drawn as Logs.dc.html (a desktop: eight columns), LogsTablet (five columns; the job and the key
// fold into the request line) and LogsPhone (a list of rows), with the states of LogsStates: a first read as flat bars,
// the list empty for the first time, empty under a filter, and a failed read. Every wide table scrolls inside its own
// box (the arrow scroller). A failed request is never tinted: its pill and its code carry it. Server strings reach the
// page as text.
const props = defineProps<{ rows: LogRow[]; loading: boolean; error: string | null; filtered: boolean }>();
const emit = defineEmits<{
  open: [row: LogRow];
  job: [id: string];
  retry: [];
  "clear-filters": [];
  "send-test": [];
}>();
const { isPhone, isTablet } = useBreakpoint();

const SKELETON = [0, 1, 2, 3, 4];
const first = computed(() => props.loading && props.rows.length === 0);
const bare = computed(() => !first.value && !props.error && props.rows.length === 0);
// the list's state, as the other lists tell it: loading (first read), refreshing (rows kept), ready, empty or error
const state = computed(() =>
  props.error
    ? "error"
    : first.value
      ? "loading"
      : props.loading
        ? "refreshing"
        : props.rows.length
          ? "ready"
          : "empty",
);

// a code that is not an error stays a dash; one on a request that went through says the job failed behind it
const codeText = (row: LogRow): string =>
  row.error_code && row.status_code < 400 ? `job failed: ${row.error_code}` : (row.error_code ?? "");
// the folded line of a tablet or phone row: the error code, else the job's title, then the key
function lineOf(row: LogRow): string {
  const lead = row.error_code ? codeText(row) : (row.job_title ?? (row.job_id ? row.job_id.slice(0, 8) : ""));
  return [lead, logKey(row)].filter(Boolean).join(" · ");
}

// inbox, search-x, cloud-off (JobsStates)
const ICONS = {
  inbox: ["M3 13h5l1.5 3h5L16 13h5", "M5 5h14l2 8v6H3v-6z"],
  search: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "m20 20-3.5-3.5", "m8.5 8.5 5 5", "m13.5 8.5-5 5"],
  cloud: ["M7 18a5 5 0 0 1-.9-9.9A6 6 0 0 1 17.7 8 4.5 4.5 0 0 1 18 18z", "m3 3 18 18"],
};
</script>

<template>
  <div
    class="lt"
    :class="{ 'lt--tablet': isTablet, 'lt--phone': isPhone }"
    :data-state="state"
    data-testid="logs-table"
  >
    <!-- a first read: flat bars in the real columns, no shimmer -->
    <template v-if="first">
      <div v-if="isPhone" class="lt__list" aria-busy="true" aria-label="Loading requests">
        <div v-for="i in SKELETON" :key="i" class="p-row p-row--skel">
          <span class="bar bar--a" /><span class="bar bar--b" /><span class="bar bar--c" />
        </div>
      </div>
      <ArrowScroller v-else table label="Requests table">
        <table class="lt__table" aria-busy="true" aria-label="Loading requests" data-testid="logs-skeleton">
          <thead>
            <tr>
              <th>Time</th>
              <th>Status</th>
              <th>Request</th>
              <th class="r">Duration</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="i in SKELETON" :key="i">
              <td><span class="bar bar--time" /></td>
              <td><span class="bar bar--pill" /></td>
              <td><span class="bar bar--route" /></td>
              <td class="r"><span class="bar bar--dur" /></td>
            </tr>
          </tbody>
        </table>
      </ArrowScroller>
    </template>

    <!-- a failed read: the frame stays, Retry reads again -->
    <div v-else-if="error" class="lt__box" role="alert">
      <span class="lt__icon is-bad"><PathIcon :paths="ICONS.cloud" :size="22" :stroke="1.8" /></span>
      <b class="lt__title">{{ error }}</b>
      <div class="lt__btns"><button type="button" class="lt__primary" @click="emit('retry')">Retry</button></div>
    </div>

    <!-- nothing yet, or nothing under the filters -->
    <div v-else-if="bare" class="lt__box" role="status">
      <template v-if="filtered">
        <span class="lt__icon"><PathIcon :paths="ICONS.search" :size="22" :stroke="1.8" /></span>
        <b class="lt__title">No requests match these filters</b>
        <span class="lt__text">Widen the dates or the status, or clear the filters to see every request.</span>
        <div class="lt__btns">
          <button type="button" class="lt__primary" @click="emit('clear-filters')">Clear filters</button>
        </div>
      </template>
      <template v-else>
        <span class="lt__icon is-brand"><PathIcon :paths="ICONS.inbox" :size="22" :stroke="1.8" /></span>
        <b class="lt__title">No requests yet</b>
        <span class="lt__text"> Each call to the relay API lands here with its status, key, timing and bodies. </span>
        <div class="lt__btns">
          <button type="button" class="lt__primary" @click="emit('send-test')">Send a test job</button>
        </div>
      </template>
    </div>

    <!-- the phone: one row per request, the whole row opens it -->
    <div v-else-if="isPhone" class="lt__list">
      <button
        v-for="r in rows"
        :key="r.id"
        type="button"
        class="p-row"
        :aria-label="`Open request ${r.id}`"
        @click="emit('open', r)"
      >
        <span class="p-row__top">
          <span class="pill num" :class="`tone-${statusTone(r.status_code)}`">{{ r.status_code }}</span>
          <span v-if="r.provider" class="pchip" :class="providerClass(r.provider)">{{ r.provider }}</span>
          <span class="p-row__right num">{{ clockTime(r.at_ms) }} · {{ durationText(r.duration_ms) }}</span>
        </span>
        <span class="p-row__route mono" :title="r.route">{{ r.route }}</span>
        <span v-if="lineOf(r)" class="p-row__sub" :class="r.error_code ? `tone-${errorTone(r.status_code)}` : ''">{{
          lineOf(r)
        }}</span>
      </button>
    </div>

    <!-- a tablet: five columns, the job and the key fold into the request line -->
    <ArrowScroller v-else-if="isTablet" table label="Requests table">
      <table class="lt__table lt__table--tablet">
        <thead>
          <tr>
            <th>Status</th>
            <th>Time</th>
            <th>Request</th>
            <th>Provider</th>
            <th class="r">Duration</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" class="is-row" @click="emit('open', r)">
            <td>
              <span class="pill num" :class="`tone-${statusTone(r.status_code)}`">{{ r.status_code }}</span>
            </td>
            <td class="mono t-time">{{ clockTime(r.at_ms) }}</td>
            <td class="t-req">
              <button
                type="button"
                class="row-open"
                :aria-label="`Open request ${r.id}`"
                :title="r.route"
                @click.stop="emit('open', r)"
              >
                {{ r.route }}
              </button>
              <span
                v-if="lineOf(r)"
                class="t-sub mono"
                :class="r.error_code ? `tone-${errorTone(r.status_code)}` : ''"
                :title="lineOf(r)"
                >{{ lineOf(r) }}</span
              >
            </td>
            <td>
              <span v-if="r.provider" class="pchip" :class="providerClass(r.provider)">{{ r.provider }}</span
              ><span v-else class="faint mono">—</span>
            </td>
            <td class="r num" :class="{ slow: isSlow(r.duration_ms) }">{{ durationText(r.duration_ms) }}</td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>

    <!-- a desktop: eight columns -->
    <ArrowScroller v-else table label="Requests table">
      <table class="lt__table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Status</th>
            <th>Request</th>
            <th>Job</th>
            <th>Provider</th>
            <th>Key</th>
            <th>Error code</th>
            <th class="r">Duration</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" class="is-row" @click="emit('open', r)">
            <td class="num t-clock">
              {{ clockTime(r.at_ms) }}<span class="ms">{{ clockMs(r.at_ms) }}</span>
            </td>
            <td>
              <span class="pill num" :class="`tone-${statusTone(r.status_code)}`">{{ r.status_code }}</span>
            </td>
            <td class="mono t-route">
              <button
                type="button"
                class="row-open mono"
                :aria-label="`Open request ${r.id}`"
                :title="r.route"
                @click.stop="emit('open', r)"
              >
                <span
                  v-if="splitRoute(r.route).method"
                  class="method"
                  :class="{ post: splitRoute(r.route).method === 'POST' }"
                  >{{ splitRoute(r.route).method }}</span
                >
                {{ splitRoute(r.route).path }}
              </button>
            </td>
            <td class="t-job">
              <button
                v-if="r.job_id"
                type="button"
                class="job-link"
                :title="r.job_title ?? undefined"
                @click.stop="emit('job', r.job_id)"
              >
                {{ r.job_title || r.job_id.slice(0, 8) }}
              </button>
              <span v-else class="faint">—</span>
            </td>
            <td>
              <span v-if="r.provider" class="pchip" :class="providerClass(r.provider)">{{ r.provider }}</span
              ><span v-else class="faint">—</span>
            </td>
            <td class="body-cell t-key" :title="logKey(r)">{{ logKey(r) }}</td>
            <td class="mono t-err">
              <span v-if="r.error_code" :class="`tone-${errorTone(r.status_code)}`" :title="r.error_code">{{
                r.error_code
              }}</span>
              <span v-else class="faint">—</span>
            </td>
            <td class="r num" :class="{ slow: isSlow(r.duration_ms) }">{{ durationText(r.duration_ms) }}</td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>
  </div>
</template>

<style scoped>
.lt {
  min-width: 0;
}
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.faint {
  color: var(--rl-faint);
}
.r {
  text-align: right;
}

/* the table: 12 px head on the head fill, rows ruled, nothing tinted */
.lt__table {
  width: 100%;
  min-width: 1120px;
  border-collapse: collapse;
}
.lt__table--tablet {
  min-width: 860px;
}
.lt__table th {
  padding: 10px 8px;
  background: var(--rl-head);
  color: var(--rl-faint);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}
.lt__table th.r {
  text-align: right;
}
.lt__table th:first-child,
.lt__table td:first-child {
  padding-left: 20px;
}
.lt__table th:last-child,
.lt__table td:last-child {
  padding-right: 20px;
}
.lt__table td {
  padding: 11px 8px;
  border-top: 1px solid var(--rl-rule);
  color: var(--rl-body);
  vertical-align: middle;
}
.lt__table td.r {
  text-align: right;
  white-space: nowrap;
}
.is-row {
  cursor: pointer;
}
.is-row:hover td {
  background: var(--rl-head);
}
.t-clock {
  padding-left: 20px;
  color: var(--rl-fg);
  font-weight: 600;
  white-space: nowrap;
}
.ms {
  color: var(--rl-faint);
  font-weight: 500;
}
.t-time {
  font-size: 12px;
  color: var(--rl-body);
  white-space: nowrap;
}
.t-route {
  font-size: 12px;
  white-space: nowrap;
}
.t-job {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.t-key {
  white-space: nowrap;
}
.t-err {
  font-size: 12px;
  white-space: nowrap;
}
.lt__table td.slow,
.slow {
  color: var(--rl-warn);
  font-weight: 700;
}
.method {
  color: var(--rl-muted);
  font-weight: 500;
}
.method.post {
  color: var(--rl-brand-ink);
}
.t-req {
  max-width: 340px;
}
.t-sub {
  display: block;
  overflow: hidden;
  font-size: 11px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--rl-faint);
}

/* the buttons that open a row or its job: plain text */
.row-open {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-fg);
  font-size: 12px;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}
/* a tablet's request line is the title cell of JobsTablet: the body face at 14/600, the folded line under it in mono */
.t-req .row-open {
  display: block;
  max-width: 100%;
  overflow: hidden;
  font-family: inherit;
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
}
.row-open:focus-visible,
.job-link:focus-visible,
.p-row:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.job-link {
  display: block;
  max-width: 100%;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-weight: 600;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.job-link:hover {
  color: var(--rl-link);
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* the pill: the code, in its family */
.pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-width: 44px;
  height: 24px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 800;
}
.pill.tone-ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.pill.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.pill.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.tone-warn {
  color: var(--rl-warn);
}
.tone-bad {
  color: var(--rl-bad);
}
/* the provider chip: its fill and its ink, the name uncoloured elsewhere */
.pchip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  background: var(--ps);
  color: var(--pi);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

/* the phone list */
.lt__list {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--rl-rule);
}
.p-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
  box-sizing: border-box;
  min-height: 44px;
  padding: 14px 16px;
  border: 0;
  border-top: 1px solid var(--rl-rule);
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.lt__list > .p-row:first-child {
  border-top: 0;
}
.p-row__top {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.p-row__right {
  margin-left: auto;
  color: var(--rl-muted);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.p-row__route {
  font-size: 13px;
  line-height: 21px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.p-row__sub {
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-faint);
  overflow-wrap: anywhere;
}
.p-row__sub.tone-warn {
  color: var(--rl-warn);
}
.p-row__sub.tone-bad {
  color: var(--rl-bad);
}

/* the flat bars of a first read */
.bar {
  display: block;
  height: 12px;
  max-width: 100%;
  border-radius: 6px;
  background: var(--rl-skel);
}
.lt__table td:has(.bar) {
  padding-top: 18px;
  padding-bottom: 18px;
}
.bar--time {
  width: 70px;
}
.bar--pill {
  width: 44px;
}
.bar--route {
  width: 200px;
}
.bar--dur {
  width: 50px;
  margin-left: auto;
}
.p-row--skel .bar--a {
  width: 120px;
}
.p-row--skel .bar--b {
  width: 70%;
}
.p-row--skel .bar--c {
  width: 50%;
}

/* empty and failed: a box in the card's frame */
.lt__box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 40px 24px;
  border-top: 1px solid var(--rl-rule);
  text-align: center;
}
.lt__icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.lt__icon.is-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.lt__icon.is-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.lt__title {
  font-size: 15px;
}
.lt__text {
  max-width: 420px;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.lt__btns {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}
.lt__primary {
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.lt__primary:hover {
  background: var(--rl-primary-hover);
}
.lt--phone .lt__primary {
  height: 44px;
}
</style>
