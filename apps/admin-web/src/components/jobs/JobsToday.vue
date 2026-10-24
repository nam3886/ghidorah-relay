<script setup lang="ts">
import { computed } from "vue";
import type { LiveAlert, ProvidersSummary } from "@/api/shell";
import PathIcon from "@/components/shared/icons/PathIcon.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { providerClass } from "@/lib/providers";
import {
  axisOf,
  causeOf,
  failedShare,
  failsALot,
  hourSlots,
  pathLabel,
  rowsOf,
  SPARK_PX,
  totalsOf,
  usableShare,
} from "./today";

// The Today section of Jobs, from the provider summary: Ledger.dc.html on a desktop (the jobs finished today as an
// hourly chart stacked by provider, three figures beside it, then one card per provider, a card picks its provider
// for the list and a second click clears it), JobsTablet on a tablet (three figure cards) and JobsPhone on a phone (one
// card: the figure, a spark line of the hours, three small tiles). Until the summary arrives nothing is drawn. Bars and
// meters are sized through :style (the CSSOM), never a style attribute in the markup (the CSP).
const props = defineProps<{ summary: ProvidersSummary | null; alerts?: readonly LiveAlert[]; selected: string }>();
const emit = defineEmits<{ pick: [provider: string] }>();
const { isPhone, isTablet } = useBreakpoint();

const n = (v: number): string => v.toLocaleString("en-US");
const rows = computed(() => (props.summary ? rowsOf(props.summary) : []));
const t = computed(() => totalsOf(rows.value));
const slots = computed(() => (props.summary ? hourSlots(props.summary) : []));
const spark = computed(() => (props.summary ? hourSlots(props.summary, SPARK_PX) : []));
const axis = computed(() => (props.summary ? axisOf(props.summary, Date.now()) : []));
const lookAt = computed(() => Math.max(0, t.value.accounts - t.value.usable));
const sparkPx = (s: { future: boolean; segs: { px: number }[] }): number =>
  s.future
    ? 3
    : Math.max(
        3,
        s.segs.reduce((a, b) => a + b.px, 0),
      );

const ICON = {
  clock: ["M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z", "M12 7v5l3 2"],
  warn: ["M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z", "M12 9v4", "M12 17h.01"],
  user: ["M9 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8z", "M2 21a7 7 0 0 1 14 0", "M17 11h5", "M19.5 8.5v5"],
};
const pick = (name: string): void => emit("pick", props.selected === name ? "" : name);
</script>

<template>
  <template v-if="summary">
    <!-- a phone: one card -->
    <section v-if="isPhone" class="jt-phone" aria-label="Today" data-testid="jobs-today">
      <div class="jt-phone__head">
        <div class="jt-col">
          <span class="jt-label">Finished today</span>
          <span class="num jt-phone__big">{{ n(t.done) }}</span>
        </div>
      </div>
      <div class="jt-spark" aria-hidden="true">
        <span
          v-for="(s, i) in spark"
          :key="i"
          class="jt-spark__bar"
          :class="{ 'is-future': s.future, 'is-now': s.now }"
          :style="{ height: `${sparkPx(s)}px` }"
        />
      </div>
      <div class="jt-tiles">
        <div class="jt-tile is-brand">
          <span>In flight</span><b class="num">{{ n(t.running) }}</b>
        </div>
        <div class="jt-tile is-bad">
          <span>Failed</span><b class="num">{{ n(t.failed) }}</b>
        </div>
        <div class="jt-tile is-warn">
          <span>Usable</span><b class="num">{{ t.usable }}/{{ t.accounts }}</b>
        </div>
      </div>
    </section>

    <!-- a tablet: three figure cards -->
    <section v-else-if="isTablet" class="jt-stats" aria-label="Today" data-testid="jobs-today">
      <div class="jt-stat">
        <span class="jt-stat__label">Finished today</span><span class="num jt-stat__value">{{ n(t.done) }}</span>
      </div>
      <div class="jt-stat">
        <span class="jt-stat__label">In flight</span><span class="num jt-stat__value">{{ n(t.running) }}</span>
        <span class="num jt-stat__note">{{ n(t.queued) }} queued</span>
      </div>
      <div class="jt-stat">
        <span class="jt-stat__label">Failed today</span><span class="num jt-stat__value is-bad">{{ n(t.failed) }}</span>
        <span class="num jt-stat__note is-bad">{{ failedShare(t.done, t.failed) }}</span>
      </div>
    </section>

    <!-- a desktop: the chart, the three figures, the provider cards -->
    <template v-else>
      <section class="jt-today" aria-label="Today" data-testid="jobs-today">
        <div class="jt-chart">
          <div class="jt-chart__top">
            <div class="jt-col">
              <span class="jt-label">Jobs finished today</span>
              <span class="num jt-big">{{ n(t.done) }}</span>
            </div>
            <span class="jt-legend">
              <span v-for="p in rows" :key="p.provider" class="jt-legend__item"
                ><span class="jt-legend__dot" :class="providerClass(p.provider)" />{{ p.provider }}</span
              >
            </span>
          </div>
          <div class="jt-bars" role="img" :aria-label="`${n(t.done)} jobs finished today, by hour`">
            <div v-for="(s, i) in slots" :key="i" class="jt-hour" :title="s.title" :class="{ 'is-now': s.now }">
              <span v-if="s.future || !s.segs.length" class="jt-stub" />
              <span
                v-for="seg in [...s.segs].reverse()"
                :key="seg.provider"
                class="jt-seg"
                :class="providerClass(seg.provider)"
                :style="{ height: `${seg.px}px` }"
              />
            </div>
          </div>
          <div class="num jt-axis">
            <span v-for="(a, i) in axis" :key="i" :class="{ 'is-now': i === 3 }">{{ a }}</span>
          </div>
        </div>

        <div class="jt-kpis">
          <div class="jt-kpi">
            <span class="jt-kpi__icon is-brand"><PathIcon :paths="ICON.clock" :size="20" :stroke="2" /></span>
            <div class="jt-col jt-kpi__text">
              <span class="jt-label">In flight now</span><span class="num jt-mid">{{ n(t.running) }}</span>
            </div>
            <span class="num jt-note is-brand">{{ n(t.queued) }} queued</span>
          </div>
          <div class="jt-kpi">
            <span class="jt-kpi__icon is-bad"><PathIcon :paths="ICON.warn" :size="20" :stroke="2" /></span>
            <div class="jt-col jt-kpi__text">
              <span class="jt-label">Failed today</span><span class="num jt-mid">{{ n(t.failed) }}</span>
            </div>
            <span class="num jt-note is-bad">{{ failedShare(t.done, t.failed) }}</span>
          </div>
          <div class="jt-kpi">
            <span class="jt-kpi__icon is-warn"><PathIcon :paths="ICON.user" :size="20" :stroke="2" /></span>
            <div class="jt-col jt-kpi__text">
              <span class="jt-label">Usable accounts</span
              ><span class="num jt-mid">{{ t.usable }} / {{ t.accounts }}</span>
            </div>
            <span v-if="lookAt" class="num jt-note is-warn">{{ lookAt }} need a look</span>
          </div>
        </div>
      </section>

      <section class="jt-cards" aria-label="Providers">
        <button
          v-for="p in rows"
          :key="p.provider"
          type="button"
          class="jt-card"
          :class="[
            providerClass(p.provider),
            { 'is-on': selected === p.provider, 'is-alarm': causeOf(alerts, p.provider) },
          ]"
          :aria-pressed="selected === p.provider"
          :aria-label="`Show only ${p.provider} jobs`"
          :data-testid="`provider-card-${p.provider}`"
          @click="pick(p.provider)"
        >
          <span class="jt-card__head">
            <span class="jt-chip">{{ p.provider }}</span>
            <span class="jt-card__path">{{ pathLabel(p) }}</span>
          </span>
          <span class="jt-card__done">
            <span class="num jt-mid">{{ n(p.done_today) }}</span>
            <span class="jt-small">done</span>
            <span class="num jt-card__fail" :class="{ 'is-bad': failsALot(p) }">{{
              p.failed_today ? `${n(p.failed_today)} failed` : "no failures"
            }}</span>
          </span>
          <span class="jt-card__acc">
            <span class="jt-card__line"
              ><span>Accounts usable</span
              ><span class="num jt-strong">{{ p.accounts.usable }} / {{ p.accounts.total }}</span></span
            >
            <span class="jt-track"
              ><span
                class="jt-meter"
                :class="{ 'is-low': usableShare(p) < 50 }"
                :style="{ width: `${usableShare(p)}%` }"
            /></span>
          </span>
          <span v-if="causeOf(alerts, p.provider)" class="jt-cause">{{ causeOf(alerts, p.provider) }}</span>
        </button>
      </section>
    </template>
  </template>
</template>

<style scoped>
.num {
  font-variant-numeric: tabular-nums;
}
.jt-col {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.jt-label {
  color: var(--rl-muted);
  font-weight: 500;
}
.jt-big {
  font-size: 40px;
  line-height: 44px;
  font-weight: 800;
  letter-spacing: -0.03em;
}
.jt-mid {
  font-size: 24px;
  line-height: 30px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.jt-small {
  font-size: 12px;
  color: var(--rl-muted);
}
.jt-strong {
  font-weight: 600;
  color: var(--rl-fg);
}

/* the desktop: chart (two thirds) and the figures */
.jt-today {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));
  gap: 20px;
}
.jt-chart {
  grid-column: span 2;
  min-width: 0;
  padding: 22px 24px 18px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.jt-chart__top {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 8px 40px;
}
.jt-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-muted);
}
.jt-legend__item {
  display: flex;
  align-items: center;
  gap: 6px;
}
.jt-legend__dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: var(--pc);
}
.jt-bars {
  position: relative;
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 170px;
  margin-top: 18px;
  border-bottom: 1px solid var(--rl-line);
}
.jt-hour {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 1px;
  height: 170px;
  min-width: 0;
}
.jt-seg {
  display: block;
  background: var(--pc);
  opacity: 0.9;
}
.jt-hour.is-now .jt-seg {
  opacity: 1;
}
.jt-seg:first-child {
  border-radius: 4px 4px 0 0;
}
.jt-stub {
  display: block;
  height: 3px;
  border-radius: 2px;
  background: var(--rl-track);
}
.jt-axis {
  display: flex;
  justify-content: space-between;
  padding-top: 8px;
  font-size: 12px;
  color: var(--rl-faint);
}
.jt-axis .is-now {
  font-weight: 600;
  color: var(--rl-brand-ink);
}
.jt-kpis {
  display: grid;
  grid-template-rows: repeat(3, minmax(0, 1fr));
  gap: 20px;
}
.jt-kpi {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px 20px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.jt-kpi__text {
  gap: 2px;
}
.jt-kpi__icon {
  display: grid;
  place-items: center;
  flex: none;
  width: 40px;
  height: 40px;
  border-radius: 12px;
}
.jt-note {
  margin-left: auto;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.is-brand.jt-kpi__icon,
.is-brand.jt-note {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.is-bad.jt-kpi__icon,
.is-bad.jt-note {
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.is-warn.jt-kpi__icon,
.is-warn.jt-note {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}

/* the provider cards: they pick the provider of the list */
.jt-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 1fr));
  gap: 16px;
}
.jt-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  box-sizing: border-box;
  padding: 16px 18px;
  border: 0;
  border-top: 3px solid var(--pc);
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
  color: var(--rl-fg);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.jt-card.is-alarm {
  box-shadow:
    0 1px 2px rgba(16, 24, 40, 0.05),
    0 0 0 1px var(--rl-bad-bg);
}
.jt-card.is-on {
  box-shadow: 0 0 0 2px var(--rl-brand);
}
.jt-card:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.jt-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.jt-chip {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  background: var(--ps);
  color: var(--pi);
  font-size: 12px;
  font-weight: 700;
}
.jt-card__path {
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-faint);
}
.jt-card__done {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.jt-card__fail {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-faint);
}
.jt-card__fail.is-bad,
.jt-cause {
  color: var(--rl-bad);
}
.jt-card__acc {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.jt-card__line {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--rl-muted);
}
.jt-track {
  display: block;
  height: 6px;
  overflow: hidden;
  border-radius: 3px;
  background: var(--rl-track);
}
.jt-meter {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: var(--pc);
}
.jt-meter.is-low {
  background: var(--rl-amber);
}
.jt-cause {
  font-size: 12px;
  line-height: 16px;
  font-weight: 600;
  overflow-wrap: anywhere;
}

/* a tablet: three cards */
.jt-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.jt-stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 16px 18px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.jt-stat__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-muted);
}
.jt-stat__value {
  font-size: 22px;
  line-height: 1.2;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.jt-stat__note {
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-faint);
}
.jt-stat__value.is-bad,
.jt-stat__note.is-bad {
  color: var(--rl-bad);
}

/* a phone: one card */
.jt-phone {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.jt-phone__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 8px;
}
.jt-phone .jt-label {
  font-size: 13px;
}
.jt-phone .jt-col {
  gap: 2px;
}
.jt-phone__big {
  font-size: 32px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.03em;
}
.jt-spark {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 44px;
}
.jt-spark__bar {
  flex: 1;
  border-radius: 3px 3px 1px 1px;
  background: color-mix(in srgb, var(--rl-brand) 38%, var(--rl-surface));
}
.jt-spark__bar.is-now {
  background: var(--rl-brand);
}
.jt-spark__bar.is-future {
  background: var(--rl-track);
}
.jt-tiles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}
.jt-tile {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 10px 12px;
  border-radius: 12px;
  font-size: 12px;
  color: var(--rl-muted);
}
.jt-tile b {
  font-size: 18px;
  font-weight: 800;
}
.jt-tile.is-brand {
  background: color-mix(in srgb, var(--rl-info-bg) 60%, var(--rl-surface));
}
.jt-tile.is-brand b {
  color: var(--rl-info);
}
.jt-tile.is-bad {
  background: color-mix(in srgb, var(--rl-bad-bg) 45%, var(--rl-surface));
}
.jt-tile.is-bad b {
  color: var(--rl-bad);
}
.jt-tile.is-warn {
  background: color-mix(in srgb, var(--rl-warn-bg) 40%, var(--rl-surface));
}
.jt-tile.is-warn b {
  color: var(--rl-warn);
}
</style>
