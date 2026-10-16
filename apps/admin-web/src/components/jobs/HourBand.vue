<script setup lang="ts">
import { computed } from "vue";
import type { HourBucket, Stats } from "@/api/shell";
import {
  axisLabels,
  bandLedger,
  bandTotals,
  barTitle,
  chartLabel,
  scaleOf,
  segmentHeight,
} from "@/components/jobs/hour-band";
import LedgerList from "@/components/shared/LedgerList.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";

// The "24 hours" card of the Jobs page: the title with the three legend counts, one column per hour (a link that
// filters the list to that hour), the hour axis, and the ledger of the live totals beside it (under it on a tablet
// and a phone). The columns draw the hourly stats; the ledger draws the stats strip.
const props = defineProps<{ hours: readonly HourBucket[]; stats: Stats | null }>();
const emit = defineEmits<{ pick: [hourMs: number] }>();
const { isPhone, isTablet } = useBreakpoint();

const totals = computed(() => bandTotals(props.hours));
const scale = computed(() => scaleOf(props.hours));
const label = computed(() => chartLabel(props.hours));
const axis = computed(() => axisLabels(props.hours, isPhone.value ? 6 : 3));
const ledger = computed(() => bandLedger(props.stats, isPhone.value || isTablet.value));
const bars = computed(() =>
  props.hours.map((h) => ({
    key: h.hour_ms,
    hour: h.hour_ms,
    title: barTitle(h),
    done: segmentHeight(h.delivered, scale.value),
    fail: segmentHeight(h.failed, scale.value),
    run: segmentHeight(h.in_progress, scale.value),
  })),
);
const legend = computed(() => [
  { key: "done", label: `delivered ${totals.value.delivered}` },
  { key: "fail", label: `failed ${totals.value.failed}` },
  { key: "run", label: `in progress ${totals.value.in_progress}` },
]);
</script>

<template>
  <section
    class="band"
    :class="{ 'band--tablet': isTablet, 'band--phone': isPhone }"
    aria-labelledby="bandTitle"
    data-testid="hour-band"
  >
    <div class="band__chart">
      <div class="band__head">
        <h2 id="bandTitle" class="band__title">
          Last 24 hours <span class="num band__count">· {{ totals.all }} jobs</span>
        </h2>
        <div class="band__legend">
          <span v-for="l in legend" :key="l.key" class="band__legend-item"
            ><span class="band__swatch" :class="`band__swatch--${l.key}`" />{{ l.label }}</span
          >
        </div>
      </div>
      <div class="band__bars" role="group" :aria-label="label">
        <a
          v-for="b in bars"
          :key="b.key"
          class="band__bar"
          href="#"
          :title="b.title"
          :aria-label="b.title"
          data-testid="hour-bar"
          @click.prevent="emit('pick', b.hour)"
        >
          <span class="band__seg band__seg--done" :style="{ height: `${b.done}%` }" />
          <span class="band__seg band__seg--fail" :style="{ height: `${b.fail}%` }" />
          <span class="band__seg band__seg--run" :style="{ height: `${b.run}%` }" />
        </a>
      </div>
      <div class="num band__axis" :class="{ 'band__axis--phone': isPhone }" aria-hidden="true">
        <span v-for="(t, i) in axis" :key="i">{{ t }}</span>
      </div>
    </div>
    <!-- the band variant of LedgerList draws the raised panel, its padding and its rows: nothing wraps it -->
    <LedgerList :items="ledger" :columns="isTablet ? 2 : 1" />
  </section>
</template>

<style scoped>
.band {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  overflow: hidden;
}
.band--tablet,
.band--phone {
  display: block;
}
.band__chart {
  flex: 3 1 560px;
  min-width: 0;
  padding: 18px 20px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.band--tablet .band__chart {
  padding: 18px 20px 16px;
}
.band--phone .band__chart {
  padding: 16px 16px 12px;
  gap: 10px;
}
.band__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.band--tablet .band__head {
  gap: 8px 12px;
}
.band__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.band__count {
  font-weight: 400;
  color: var(--rl-muted);
}
.band__legend {
  display: flex;
  gap: 14px;
  font-size: 12px;
  color: var(--rl-muted);
}
.band--tablet .band__legend,
.band--phone .band__legend {
  flex-wrap: wrap;
  gap: 4px 14px;
}
.band--phone .band__legend {
  flex-basis: 100%;
}
.band__legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.band__swatch {
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.band__swatch--done {
  background: var(--rl-bar-done);
}
.band__swatch--fail {
  background: var(--rl-bar-fail);
}
.band__swatch--run {
  background: var(--rl-bar-run);
}
.band__bars {
  display: grid;
  grid-template-columns: repeat(24, minmax(0, 1fr));
  gap: 4px;
  align-items: end;
  height: 120px;
  border-bottom: 1px solid var(--rl-line-strong);
}
.band--tablet .band__bars {
  gap: 3px;
  height: 112px;
}
.band--phone .band__bars {
  gap: 2px;
  height: 96px;
}
.band__bar {
  display: flex;
  flex-direction: column-reverse;
  height: 100%;
  justify-content: flex-start;
  gap: 1px;
  text-decoration: none;
}
.band__bar:hover {
  opacity: 0.8;
}
.band__seg {
  display: block;
  border-radius: 3px;
}
.band__seg--done {
  background: var(--rl-bar-done);
  border-radius: 3px 3px 0 0;
}
.band__seg--fail {
  background: var(--rl-bar-fail);
}
.band__seg--run {
  background: var(--rl-bar-run);
}
.band--phone .band__seg {
  border-radius: 2px;
}
.band--phone .band__seg--done {
  border-radius: 2px 2px 0 0;
}
.band__axis {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  font-size: 11px;
  color: var(--rl-faint);
  font-family: var(--rl-mono);
  text-align: left;
}
.band__axis--phone {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
</style>
