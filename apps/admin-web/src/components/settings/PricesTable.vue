<script setup lang="ts">
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import { computed } from "vue";
import type { PriceRow } from "@/api/settings";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import SectionState from "./SectionState.vue";
import "./settings-skeleton.css";

// What upstream reported per job against what the relay charged. A row is one (provider, model, duration). Desktop is
// the full table; tablet keeps five columns and folds provider, duration and substituted under the model; a phone gets
// a list.
type KeyedPrice = PriceRow & { id: string };
const props = defineProps<{ rows: KeyedPrice[]; loading: boolean; error: string | null }>();
const emit = defineEmits<{ retry: [] }>();
const { isPhone, isTablet } = useBreakpoint();

// What the sweep and the tests read: the load state of the card
const state = computed(() =>
  props.error ? "error" : props.loading && !props.rows.length ? "loading" : !props.rows.length ? "empty" : "ready",
);
// The first read: the table's own header over five rows of bars (the B-States loading pattern)
const skeleton = computed(() => props.loading && !props.rows.length && !props.error);
const SKELETON = [
  { model: "104px", pill: "40px" },
  { model: "88px", pill: "128px" },
  { model: "112px", pill: "40px" },
  { model: "96px", pill: "40px" },
  { model: "100px", pill: "40px" },
];
const NONE = "—";
const duration = (row: PriceRow): string => (row.duration === null ? NONE : `${row.duration} s`);
const lowest = (row: PriceRow): string => (row.lowest_upstream === null ? NONE : String(row.lowest_upstream));
const pillText = (row: PriceRow): string => (row.disagreed ? `Upstream disagreed ${row.disagreed}` : "0");
// "dola · 5 s" and, when some jobs were charged another way, " · 3 substituted"
const second = (row: PriceRow): string =>
  `${row.provider} · ${duration(row)}${row.substituted > 0 ? ` · ${row.substituted} substituted` : ""}`;
</script>

<template>
  <div class="prices" :class="{ 'is-tablet': isTablet }" :aria-busy="loading" :data-state="state">
    <SectionState
      :error="error"
      :empty="!rows.length && !loading"
      empty-text="No job has an upstream figure yet"
      @retry="emit('retry')"
    />

    <ul v-if="skeleton && isPhone" class="list" aria-hidden="true">
      <li v-for="(w, n) in SKELETON" :key="n">
        <div class="list__line">
          <i class="set-skel" :style="{ width: w.model }" /><i
            class="set-skel set-skel--pill"
            :style="{ width: w.pill }"
          />
        </div>
        <i class="set-skel set-skel--sub" :style="{ width: '220px' }" />
      </li>
    </ul>
    <ul v-else-if="!error && rows.length && isPhone" class="list">
      <li v-for="row in rows" :key="row.id">
        <div class="list__line">
          <span class="list__model"
            >{{ row.model }} <span class="list__dur">· {{ duration(row) }}</span></span
          >
          <StatusChip
            status="neutral"
            :label="pillText(row)"
            :tone="row.disagreed ? 'bad' : 'neutral'"
            class="list__pill"
          />
        </div>
        <span class="list__meta tnum">
          <span>{{ row.provider }}</span>
          <span
            >charged <b class="mono">{{ row.charged }}</b></span
          >
          <span
            >jobs <b class="mono">{{ row.jobs }}</b></span
          >
          <span
            >lowest <b class="mono">{{ lowest(row) }}</b></span
          >
          <span
            >substituted <b class="mono">{{ row.substituted }}</b></span
          >
        </span>
      </li>
    </ul>

    <ArrowScroller v-else-if="!error && (rows.length || skeleton)" class="scroll" label="Measured prices" table>
      <table class="table">
        <thead>
          <tr v-if="isTablet">
            <th scope="col" class="first">Model</th>
            <th scope="col" class="right">Charged</th>
            <th scope="col" class="right">Jobs</th>
            <th scope="col">Upstream disagreed</th>
            <th scope="col" class="right last">Lowest</th>
          </tr>
          <tr v-else>
            <th scope="col" class="first">Provider</th>
            <th scope="col">Model</th>
            <th scope="col" class="right">Duration</th>
            <th scope="col" class="right">Charged</th>
            <th scope="col" class="right">Jobs with a figure</th>
            <th scope="col">Upstream disagreed</th>
            <th scope="col" class="right">Lowest upstream</th>
            <th scope="col" class="right last">Substituted (not counted)</th>
          </tr>
        </thead>
        <tbody v-if="skeleton" aria-hidden="true">
          <tr v-for="(w, n) in SKELETON" :key="n" class="set-skel-row">
            <template v-if="isTablet">
              <td class="first first--two">
                <i class="set-skel" :style="{ width: w.model }" /><i
                  class="set-skel set-skel--sub"
                  :style="{ width: '64px' }"
                />
              </td>
              <td><i class="set-skel set-skel--end" :style="{ width: '16px' }" /></td>
              <td><i class="set-skel set-skel--end" :style="{ width: '24px' }" /></td>
              <td><i class="set-skel set-skel--pill" :style="{ width: w.pill }" /></td>
              <td class="last"><i class="set-skel set-skel--end" :style="{ width: '16px' }" /></td>
            </template>
            <template v-else>
              <td class="first"><i class="set-skel" :style="{ width: '28px' }" /></td>
              <td><i class="set-skel" :style="{ width: w.model }" /></td>
              <td><i class="set-skel set-skel--end" :style="{ width: '24px' }" /></td>
              <td><i class="set-skel set-skel--end" :style="{ width: '16px' }" /></td>
              <td><i class="set-skel set-skel--end" :style="{ width: '24px' }" /></td>
              <td><i class="set-skel set-skel--pill" :style="{ width: w.pill }" /></td>
              <td><i class="set-skel set-skel--end" :style="{ width: '16px' }" /></td>
              <td class="last"><i class="set-skel set-skel--end" :style="{ width: '16px' }" /></td>
            </template>
          </tr>
        </tbody>
        <tbody v-else>
          <tr v-for="row in rows" :key="row.id">
            <template v-if="isTablet">
              <td class="first first--two">
                <span class="model mono">{{ row.model }}</span>
                <span class="second mono tnum">{{ second(row) }}</span>
              </td>
              <td class="right num mono">{{ row.charged }}</td>
              <td class="right num mono">{{ row.jobs }}</td>
              <td>
                <StatusChip status="neutral" :label="pillText(row)" :tone="row.disagreed ? 'bad' : 'neutral'" />
              </td>
              <td class="right num mono last">{{ lowest(row) }}</td>
            </template>
            <template v-else>
              <td class="first">{{ row.provider }}</td>
              <td class="mono">{{ row.model }}</td>
              <td class="right num mono">{{ duration(row) }}</td>
              <td class="right num mono">{{ row.charged }}</td>
              <td class="right num mono">{{ row.jobs }}</td>
              <td>
                <StatusChip status="neutral" :label="pillText(row)" :tone="row.disagreed ? 'bad' : 'neutral'" />
              </td>
              <td class="right num mono">{{ lowest(row) }}</td>
              <td class="right num mono muted last">{{ row.substituted }}</td>
            </template>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>
  </div>
</template>

<style scoped>
/* the loading rows are as tall as loaded ones: 45 on a desktop, 57 on a tablet */
.prices {
  --set-skel-row: 45px;
}
.prices.is-tablet {
  --set-skel-row: 57px;
}
.table {
  width: 100%;
  min-width: 900px;
  border-collapse: separate;
  border-spacing: 0;
}
.is-tablet .table {
  min-width: 0;
}
.table th {
  height: 38px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  color: var(--rl-faint);
  background: var(--rl-head);
}
.table th.first {
  padding-left: 16px;
}
.table .right {
  text-align: right;
}
.table th.last,
.table td.last {
  padding-right: 16px;
}
.table td {
  padding: 0 12px;
  border-bottom: 1px solid var(--rl-rule);
}
/* as drawn: the first cell is 44 px of content, so a row is 45 with its line */
.table td.first {
  height: 44px;
  padding-left: 16px;
}
.table td.first--two {
  height: auto;
  padding: 8px 12px 8px 16px;
}
/* a block of its own, as drawn: inline beside the cell's own font its line would grow by a pixel */
.model {
  display: block;
}
.table tbody tr:hover td {
  background: var(--rl-head);
}
.mono {
  font-family: var(--rl-mono);
  font-size: 13px;
}
/* the canvas's `num`: tabular figures only (the app's global .num also aligns right) */
.tnum {
  font-variant-numeric: tabular-nums;
}
.muted {
  color: var(--rl-muted);
}
.second {
  display: block;
  font-size: 12px;
  color: var(--rl-muted);
}
.list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.list li {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  min-height: 72px;
  padding: 10px 16px;
  border-top: 1px solid var(--rl-rule);
}
.list__line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.list__model {
  font-family: var(--rl-mono);
  font-size: 13px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.list__dur {
  font-weight: 400;
  color: var(--rl-muted);
}
.list__pill {
  flex: none;
}
.list__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 14px;
  font-size: 12px;
  color: var(--rl-muted);
}
.list__meta .mono {
  font-size: 12px;
  font-weight: 400;
  color: var(--rl-fg);
}
</style>
