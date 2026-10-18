<script setup lang="ts">
import { computed } from "vue";
import type { PriceRow } from "@/api/settings";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import SectionState from "./SectionState.vue";

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
      :loading="loading"
      :error="error"
      :empty="!rows.length"
      empty-text="No job has an upstream figure yet"
      @retry="emit('retry')"
    />

    <ul v-if="!error && rows.length && isPhone" class="list">
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

    <div v-else-if="!error && rows.length" class="scroll">
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
        <tbody>
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
    </div>
  </div>
</template>

<style scoped>
.scroll {
  overflow-x: auto;
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
  font-weight: 500;
  text-align: left;
  color: var(--rl-muted);
  background: var(--rl-raised);
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
  border-bottom: 1px solid var(--rl-line);
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
  background: var(--rl-raised);
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
  border-top: 1px solid var(--rl-line);
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
