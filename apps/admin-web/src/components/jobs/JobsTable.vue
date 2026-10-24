<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import type { JobSummary } from "@/api/jobs";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import IconButton from "@/components/shared/IconButton.vue";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { isDayRow, type DayItem } from "@/lib/day-rows";
import { providerClass } from "@/lib/providers";
import { age, isSlow, pointsText, statusPill } from "./job-row";
import { shortId } from "./job-text";

// The job list, drawn as Ledger.dc.html (a desktop: tick box, Status, Job, Provider, Account, Age, Points and two
// icon buttons), JobsTablet (Status, Job, Provider, Age, Points: the account folds into the job line) and JobsPhone
// (a list of rows, no tick box), with the states of JobsStates: a first read as flat bars in the real columns, the list
// empty for the first time, empty under a filter, a failed read, and ticked rows tinted. Day headers stay between the
// days. A wide table scrolls inside its own box (the arrow scroller). Server strings reach the page as text.
// The ticks belong to the view (`picked`): a box only reports a click.
const props = defineProps<{
  rows: DayItem<JobSummary>[];
  picked: ReadonlySet<string>;
  loading: boolean;
  error: string | null;
  filtered: boolean;
  now: number;
}>();
const emit = defineEmits<{
  toggle: [id: string, on: boolean];
  "toggle-all": [on: boolean];
  open: [id: string];
  copy: [id: string];
  "new-tab": [id: string];
  retry: [];
  "clear-filters": [];
  "new-test": [];
}>();
const { isPhone, isTablet } = useBreakpoint();

const jobs = computed(() => props.rows.filter((r): r is JobSummary => !isDayRow(r)));
const first = computed(() => props.loading && jobs.value.length === 0 && !props.error);
const bare = computed(() => !first.value && !props.error && jobs.value.length === 0);
const refreshing = computed(() => props.loading && jobs.value.length > 0 && !props.error);
const state = computed(() =>
  props.error ? "error" : first.value ? "loading" : refreshing.value ? "refreshing" : bare.value ? "empty" : "ready",
);
const allTicked = computed(() => jobs.value.length > 0 && jobs.value.every((j) => props.picked.has(j.id)));
const SKELETON = [0, 1, 2, 3, 4];
// the column count a day header spans
const span = computed(() => (isTablet.value ? 6 : 8));

// the line under a title: desktop "model · id8", tablet "model · account"; a failed job's code in red after it
const subOf = (j: JobSummary): string =>
  isTablet.value ? `${j.model} · ${j.account_label || "—"}` : `${j.model} · ${shortId(j.id)}`;
// the whole line, as its title attribute carries it when the cell ends it in an ellipsis
const subTitle = (j: JobSummary): string =>
  [subOf(j), j.model_substituted ? "model differs" : "", j.error_code ?? ""].filter(Boolean).join(" · ");
const label = (j: JobSummary): string => `Open job ${shortId(j.id)}, ${j.title || "(untitled)"}`;

// C copies the id of the row under the pointer or holding the focus, as its button's tooltip says ("Copy job id · C").
const hovered = ref<string | null>(null);
function onKey(event: KeyboardEvent): void {
  if (event.key !== "c" || event.ctrlKey || event.metaKey || event.altKey || !hovered.value) return;
  const el = event.target instanceof Element ? event.target : null;
  if (el?.closest("input, textarea, select, [contenteditable=true], [role=dialog]")) return;
  emit("copy", hovered.value);
}
onMounted(() => document.addEventListener("keydown", onKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onKey));

// inbox, search-x, cloud-off (JobsStates)
const ICONS = {
  inbox: ["M3 13h5l1.5 3h5L16 13h5", "M5 5h14l2 8v6H3v-6z"],
  search: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14z", "m20 20-3.5-3.5", "m8.5 8.5 5 5", "m13.5 8.5-5 5"],
  cloud: ["M7 18a5 5 0 0 1-.9-9.9A6 6 0 0 1 17.7 8 4.5 4.5 0 0 1 18 18z", "m3 3 18 18"],
};
const INTERACTIVE = "button, a, input, label";
function rowClick(j: JobSummary, event: MouseEvent): void {
  if ((event.target as HTMLElement | null)?.closest(INTERACTIVE)) return;
  emit("open", j.id);
}
</script>

<template>
  <div
    class="jobs__table jl"
    :class="{ 'jl--tablet': isTablet, 'jl--phone': isPhone, 'is-refreshing': refreshing }"
    :data-state="state"
    :aria-busy="loading"
  >
    <!-- a first read: flat bars in the real columns, no shimmer -->
    <template v-if="first">
      <div v-if="isPhone" class="jl__list" aria-label="Loading jobs">
        <div v-for="i in SKELETON" :key="i" class="p-row p-row--skel">
          <span class="bar bar--a" /><span class="bar bar--b" /><span class="bar bar--c" />
        </div>
      </div>
      <ArrowScroller v-else table label="Jobs table">
        <table class="jl__table" aria-label="Loading jobs" data-testid="jobs-skeleton">
          <thead>
            <tr>
              <th>Status</th>
              <th>Job</th>
              <th>Provider</th>
              <th v-if="!isTablet">Account</th>
              <th class="r">Age</th>
              <th class="r">Points</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="i in SKELETON" :key="i">
              <td><span class="bar bar--pill" /></td>
              <td><span class="bar bar--title" :class="`w${i}`" /></td>
              <td><span class="bar bar--chip" /></td>
              <td v-if="!isTablet"><span class="bar bar--acc" /></td>
              <td class="r"><span class="bar bar--age" /></td>
              <td class="r"><span class="bar bar--pts" /></td>
            </tr>
          </tbody>
        </table>
      </ArrowScroller>
    </template>

    <!-- a failed read: the frame stays, Retry reads again -->
    <div v-else-if="error" class="jl__box" role="alert">
      <span class="jl__icon is-bad"><PathIcon :paths="ICONS.cloud" :size="22" :stroke="1.8" /></span>
      <b class="jl__title">{{ error }}</b>
      <span class="jl__text">The last good read stays hidden so stale numbers are not mistaken for live ones.</span>
      <div class="jl__btns"><button type="button" class="jl__primary" @click="emit('retry')">Retry</button></div>
    </div>

    <!-- nothing yet, or nothing under the filters -->
    <div v-else-if="bare" class="jl__box" role="status">
      <template v-if="filtered">
        <span class="jl__icon"><PathIcon :paths="ICONS.search" :size="22" :stroke="1.8" /></span>
        <b class="jl__title">No jobs match these filters</b>
        <span class="jl__text">Pick another status or key, or clear the filters to see every job.</span>
        <div class="jl__btns">
          <button type="button" class="jl__primary" @click="emit('clear-filters')">Clear filters</button>
        </div>
      </template>
      <template v-else>
        <span class="jl__icon is-brand"><PathIcon :paths="ICONS.inbox" :size="22" :stroke="1.8" /></span>
        <b class="jl__title">No jobs yet</b>
        <span class="jl__text"
          >Jobs appear here as soon as a key sends one. Send a test job to see the whole path.</span
        >
        <div class="jl__btns">
          <a class="jl__btn" href="/reference" target="_blank" rel="noopener">Read the API docs</a>
          <button type="button" class="jl__primary" @click="emit('new-test')">New test job</button>
        </div>
      </template>
    </div>

    <!-- a phone: one row per job, the whole row opens it -->
    <div v-else-if="isPhone" class="jl__list">
      <template v-for="r in rows" :key="r.id">
        <div v-if="isDayRow(r)" class="jl__day" data-testid="day-row">
          {{ r.label }}<span v-if="r.note" class="jl__day-note"> · {{ r.note }}</span>
        </div>
        <button v-else type="button" class="p-row" :data-id="r.id" :aria-label="label(r)" @click="emit('open', r.id)">
          <span class="p-row__top">
            <span class="pill" :class="`tone-${statusPill(r.status).tone}`">{{ statusPill(r.status).text }}</span>
            <span class="num p-row__age" :class="{ slow: isSlow(r, now) }">{{ age(r.created_ms, now) }}</span>
          </span>
          <span class="p-row__title" :title="r.title || '(untitled)'">{{ r.title || "(untitled)" }}</span>
          <span class="p-row__line">
            <span class="pchip" :class="providerClass(r.provider)">{{ r.provider }}</span>
            <span class="mono p-row__model" :title="r.model">{{ r.model }}</span>
            <span class="num p-row__pts">{{ pointsText(r) }} <span class="p-row__unit">pts</span></span>
          </span>
          <span v-if="r.error_code" class="mono p-row__code">{{ r.error_code }}</span>
        </button>
      </template>
    </div>

    <!-- a desktop and a tablet -->
    <ArrowScroller v-else table label="Jobs table">
      <table class="jl__table">
        <thead>
          <tr>
            <th class="c-check">
              <input
                type="checkbox"
                class="check"
                aria-label="Select every row on this page"
                :checked="allTicked"
                @change="emit('toggle-all', !allTicked)"
              />
            </th>
            <th class="c-status">Status</th>
            <th>Job</th>
            <th>Provider</th>
            <th v-if="!isTablet">Account</th>
            <th class="r">Age</th>
            <th class="r" :class="{ 'c-last': isTablet }">Points</th>
            <th v-if="!isTablet" class="c-actions"><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="r in rows" :key="r.id">
            <tr v-if="isDayRow(r)" class="is-day">
              <td :colspan="span" data-testid="day-row">
                {{ r.label }}<span v-if="r.note" class="jl__day-note"> · {{ r.note }}</span>
              </td>
            </tr>
            <tr
              v-else
              class="is-row"
              :class="{ 'is-picked': picked.has(r.id) }"
              :data-id="r.id"
              @click="rowClick(r, $event)"
              @mouseenter="hovered = r.id"
              @mouseleave="hovered = null"
              @focusin="hovered = r.id"
            >
              <td class="c-check">
                <input
                  type="checkbox"
                  class="check"
                  :aria-label="`Select job ${shortId(r.id)}`"
                  :checked="picked.has(r.id)"
                  @change="emit('toggle', r.id, !picked.has(r.id))"
                />
              </td>
              <td class="c-status">
                <span class="pill" :class="`tone-${statusPill(r.status).tone}`">{{ statusPill(r.status).text }}</span>
              </td>
              <td class="c-job">
                <button type="button" class="title-link" :title="r.title || '(untitled)'" @click="emit('open', r.id)">
                  {{ r.title || "(untitled)" }}
                </button>
                <span class="mono sub" :title="subTitle(r)"
                  >{{ subOf(r) }}<span v-if="r.model_substituted" class="sub__warn"> · model differs</span
                  ><span v-if="r.error_code" class="sub__code"> · {{ r.error_code }}</span></span
                >
              </td>
              <td>
                <span class="pchip" :class="providerClass(r.provider)">{{ r.provider }}</span>
              </td>
              <td v-if="!isTablet" class="mono c-account">{{ r.account_label || "—" }}</td>
              <td class="r num c-age" :class="{ slow: isSlow(r, now) }">{{ age(r.created_ms, now) }}</td>
              <td class="r num c-pts" :class="{ 'c-last': isTablet }">{{ pointsText(r) }}</td>
              <td v-if="!isTablet" class="c-actions">
                <span class="acts">
                  <IconButton icon="copy" label="Copy job id" shortcut="C" @click="emit('copy', r.id)" />
                  <IconButton
                    icon="external"
                    label="Open in new tab"
                    placement="top-end"
                    @click="emit('new-tab', r.id)"
                  />
                </span>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </ArrowScroller>
  </div>
</template>

<style scoped>
.jl {
  min-width: 0;
}
.mono {
  font-family: var(--rl-mono);
}
.num {
  font-variant-numeric: tabular-nums;
}
.r {
  text-align: right;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
.is-refreshing .jl__table tbody,
.is-refreshing .jl__list {
  opacity: 0.6;
}

/* the table: 12 px head on the head fill, rows ruled, a ticked row tinted */
.jl__table {
  width: 100%;
  min-width: 960px;
  border-collapse: collapse;
}
.jl--tablet .jl__table {
  min-width: 700px;
}
.jl__table th {
  padding: 10px 8px;
  background: var(--rl-head);
  color: var(--rl-faint);
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}
.jl__table th.r {
  text-align: right;
}
.jl__table td {
  padding: 12px 8px;
  border-top: 1px solid var(--rl-rule);
  color: var(--rl-body);
  vertical-align: middle;
}
.jl__table th:first-child,
.jl__table td:first-child {
  padding-left: 20px;
}
.jl--tablet .jl__table th:first-child,
.jl--tablet .jl__table td:first-child {
  padding-left: 16px;
}
.c-check {
  width: 16px;
}
.jl__table th.c-check,
.jl__table td.c-check {
  padding-right: 4px;
}
.c-status {
  width: 130px;
  white-space: nowrap;
}
.c-job {
  width: 46%;
  max-width: 0;
}
.c-account {
  max-width: 160px;
  font-size: 12px;
  overflow-wrap: anywhere;
}
.c-age {
  white-space: nowrap;
}
.c-pts {
  font-weight: 600;
  color: var(--rl-fg);
  white-space: nowrap;
}
.jl__table th.c-last,
.jl__table td.c-last {
  padding-right: 16px;
}
.c-actions {
  width: 1%;
  white-space: nowrap;
}
.jl__table th.c-actions,
.jl__table td.c-actions {
  padding: 8px 16px 8px 4px;
  text-align: right;
}
.acts {
  display: inline-flex;
  gap: 2px;
}
.is-row {
  cursor: pointer;
}
.is-row:hover td {
  background: var(--rl-head);
}
.is-row.is-picked td {
  background: color-mix(in srgb, var(--rl-primary-soft) 45%, var(--rl-surface));
}
.slow {
  color: var(--rl-warn);
  font-weight: 700;
}
.check {
  width: 16px;
  height: 16px;
  margin: 0;
  vertical-align: middle;
  accent-color: var(--rl-primary);
  cursor: pointer;
}
.is-day td {
  padding: 8px 20px;
  background: var(--rl-head);
  color: var(--rl-fg);
  font-size: 12px;
  font-weight: 700;
}
.jl__day-note {
  color: var(--rl-faint);
  font-weight: 500;
}

/* the title: one line, its whole text in the title attribute and the drawer */
.title-link {
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
.title-link:hover {
  color: var(--rl-link);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.title-link:focus-visible,
.p-row:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.sub {
  display: block;
  overflow: hidden;
  font-size: 12px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--rl-faint);
}
.sub__code {
  color: var(--rl-bad);
}
.sub__warn {
  color: var(--rl-warn);
}

/* the status pill and the provider chip */
.pill {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.tone-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.tone-neutral {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.tone-ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.tone-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.tone-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.pchip {
  display: inline-flex;
  flex: none;
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
.jl__list {
  display: flex;
  flex-direction: column;
}
.jl__day {
  padding: 8px 16px;
  border-top: 1px solid var(--rl-rule);
  background: var(--rl-head);
  font-size: 12px;
  font-weight: 700;
}
.jl__list > :first-child {
  border-top: 0;
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
.p-row__top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.p-row__age {
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-faint);
}
.p-row__title {
  display: -webkit-box;
  overflow: hidden;
  font-size: 15px;
  line-height: 21px;
  font-weight: 700;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow-wrap: anywhere;
}
.p-row__line {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.p-row__model {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 12px;
  color: var(--rl-faint);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.p-row__pts {
  font-weight: 700;
  white-space: nowrap;
}
.p-row__unit {
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-faint);
}
.p-row__code {
  font-size: 12px;
  color: var(--rl-bad);
  overflow-wrap: anywhere;
}

/* the flat bars of a first read */
.bar {
  display: block;
  height: 12px;
  max-width: 100%;
  border-radius: 6px;
  background: var(--rl-skel);
}
.jl__table td:has(.bar) {
  padding-top: 18px;
  padding-bottom: 18px;
}
.bar--pill {
  width: 80px;
}
.bar--title {
  width: 220px;
}
.bar--title.w1 {
  width: 180px;
}
.bar--title.w3 {
  width: 240px;
}
.bar--chip {
  width: 48px;
}
.bar--acc {
  width: 80px;
}
.bar--age,
.bar--pts {
  width: 32px;
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
.jl__box {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 40px 24px;
  border-top: 1px solid var(--rl-rule);
  text-align: center;
}
.jl__icon {
  display: grid;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.jl__icon.is-brand {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.jl__icon.is-bad {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.jl__title {
  max-width: 520px;
  font-size: 15px;
  overflow-wrap: anywhere;
}
.jl__text {
  max-width: 420px;
  font-size: 13px;
  line-height: 20px;
  color: var(--rl-muted);
}
.jl__btns {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 4px;
}
.jl__primary,
.jl__btn {
  display: inline-flex;
  align-items: center;
  box-sizing: border-box;
  height: 36px;
  padding: 0 16px;
  border-radius: 10px;
  font: inherit;
  font-weight: 700;
  text-decoration: none;
  cursor: pointer;
}
.jl__primary {
  border: 0;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
}
.jl__primary:hover {
  background: var(--rl-primary-hover);
}
.jl__btn {
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-weight: 600;
}
.jl__btn:hover {
  background: var(--rl-head);
}
.jl--phone .jl__primary,
.jl--phone .jl__btn {
  height: 44px;
}
</style>
