<script setup lang="ts">
import { computed } from "vue";
import type { LogsToday } from "@/api/logs";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { durationText, thousands } from "./log-window";

// "Today in numbers" (Logs.dc.html): the requests logged since midnight in the dashboard zone, the share of each status
// family with its count (and the commonest error code of the 4xx and the 5xx), and the 95th-percentile duration with
// the median under it: one card of cells. A tablet (LogsTablet) draws four tiles without the duration and a phone
// (LogsPhone) the three families, each a tile of its own whose count takes the family's colour (a 5xx share in red).
// Only requests the log keeps are counted: a read that worked is not logged.
const props = defineProps<{ today: LogsToday }>();
const { isPhone, isTablet } = useBreakpoint();
const compact = computed(() => isPhone.value || isTablet.value);

// `GMT+7`, the dashboard zone's offset at the day's start
function offset(zone: string, at: number): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "shortOffset" })
      .formatToParts(at)
      .find((p) => p.type === "timeZoneName");
    return part?.value ?? zone;
  } catch {
    return zone;
  }
}

const share = (n: number): string => (props.today.requests ? `${((n / props.today.requests) * 100).toFixed(1)}%` : "—");

interface Cell {
  key: string;
  label: string;
  value: string;
  note: string;
  tone?: "ok" | "warn" | "bad";
}

const cells = computed<Cell[]>(() => {
  const t = props.today;
  const family = (k: "2xx" | "4xx" | "5xx", tone: Cell["tone"]): Cell => {
    const n = t.by_class[k];
    const top = k === "2xx" ? null : t.top_code[k];
    const count = compact.value ? thousands(n) : `${thousands(n)} ${n === 1 ? "request" : "requests"}`;
    return {
      key: k,
      label: k,
      value: share(n),
      note: top && !compact.value ? `${thousands(n)} · mostly ${top.code}` : count,
      tone,
    };
  };
  const requests: Cell = {
    key: "requests",
    label: "Requests today",
    value: thousands(t.requests),
    note: isTablet.value ? "since 00:00" : `since 00:00 ${offset(t.day_tz, t.day_start_ms)}`,
  };
  const p95: Cell = {
    key: "p95",
    label: "p95 duration",
    value: t.p95_ms === null ? "—" : durationText(t.p95_ms),
    note: t.p50_ms === null ? "no request yet" : `p50 ${durationText(t.p50_ms)}`,
  };
  const families = [family("2xx", "ok"), family("4xx", "warn"), family("5xx", "bad")];
  if (isPhone.value) return families;
  if (isTablet.value) return [requests, ...families];
  return [requests, ...families, p95];
});
</script>

<template>
  <section
    class="lv-stats"
    :class="{ 'is-phone': isPhone, 'is-tiles': compact }"
    :style="{ '--cols': cells.length }"
    aria-label="Today in numbers"
    data-testid="logs-stats"
  >
    <div v-for="c in cells" :key="c.key" class="lv-stats__cell" :data-cell="c.key">
      <span class="lv-stats__label"
        ><span v-if="c.tone && !compact" class="lv-stats__dot" :class="`tone-${c.tone}`" aria-hidden="true" />{{
          c.label
        }}</span
      >
      <span class="lv-stats__value num" :class="{ 'tone-bad': compact && c.tone === 'bad' }">{{ c.value }}</span>
      <span class="lv-stats__note num" :class="compact && c.tone ? `tone-${c.tone}` : ''" :title="c.note">{{
        c.note
      }}</span>
    </div>
  </section>
</template>

<style scoped>
.lv-stats {
  display: grid;
  grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.lv-stats__cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding: 16px 20px;
}
.lv-stats__cell + .lv-stats__cell {
  border-left: 1px solid var(--rl-rule);
}
.lv-stats__label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--rl-muted);
}
.lv-stats__dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.lv-stats__dot.tone-ok {
  background: var(--rl-ok);
}
.lv-stats__dot.tone-warn {
  background: var(--rl-warn);
}
.lv-stats__dot.tone-bad {
  background: var(--rl-bad);
}
.lv-stats__value {
  font-size: 22px;
  line-height: 28px;
  font-weight: 800;
  letter-spacing: -0.02em;
  color: var(--rl-fg);
}
.lv-stats__note {
  overflow: hidden;
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
  white-space: nowrap;
  text-overflow: ellipsis;
}
.num {
  font-variant-numeric: tabular-nums;
}
/* a tablet and a phone: one tile per cell, the count in its family's colour */
.lv-stats.is-tiles {
  gap: 12px;
  overflow: visible;
  border-radius: 0;
  background: none;
  box-shadow: none;
}
.lv-stats.is-tiles .lv-stats__cell {
  border-left: 0;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.lv-stats.is-tiles .lv-stats__note {
  font-weight: 600;
}
.lv-stats__value.tone-bad,
.lv-stats__note.tone-bad {
  color: var(--rl-bad);
}
.lv-stats__note.tone-ok {
  color: var(--rl-ok);
}
.lv-stats__note.tone-warn {
  color: var(--rl-warn);
}
.lv-stats.is-phone {
  gap: 8px;
}
.lv-stats.is-phone .lv-stats__cell {
  padding: 12px 14px;
}
.lv-stats.is-phone .lv-stats__value {
  font-size: 18px;
  line-height: 24px;
}
</style>
