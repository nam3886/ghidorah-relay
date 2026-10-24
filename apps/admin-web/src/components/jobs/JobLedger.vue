<script lang="ts">
export interface LedgerRow {
  label: string;
  value: string;
  mono?: boolean;
  tone?: "ok" | "bad";
}
</script>

<script setup lang="ts">
// The drawer's ledger (JobDetail, JobDetailTabs, JobDetailPhone): a ruled box, 12 px radius; each row the label in the
// faint ink and the value in 600 (or mono), a long value breaking anywhere. A desktop and a tablet set the label in a
// 150 px column (160 in the Technical tab: `wide`, its values 12 px: `small`); `stacked` is the phone card's row, the
// label left and the value right.
withDefaults(
  defineProps<{ items: LedgerRow[]; wide?: boolean; small?: boolean; stacked?: boolean; testid?: string }>(),
  { wide: false, small: false, stacked: false, testid: undefined },
);
</script>

<template>
  <dl class="jl-ledger" :class="{ 'is-wide': wide, 'is-small': small, 'is-stacked': stacked }" :data-testid="testid">
    <div v-for="r in items" :key="r.label" class="jl-ledger__row">
      <dt>{{ r.label }}</dt>
      <dd class="num" :class="[{ mono: r.mono }, r.tone ? `tone-${r.tone}` : '']">{{ r.value }}</dd>
    </div>
  </dl>
</template>

<style scoped>
.jl-ledger {
  display: flex;
  flex-direction: column;
  margin: 0;
  overflow: hidden;
  border: 1px solid var(--rl-rule);
  border-radius: 12px;
}
.jl-ledger__row {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 12px;
  padding: 9px 14px;
}
.jl-ledger__row + .jl-ledger__row {
  border-top: 1px solid var(--rl-rule);
}
.is-wide .jl-ledger__row {
  grid-template-columns: 160px minmax(0, 1fr);
  padding: 8px 14px;
}
dt {
  font-size: 13px;
  color: var(--rl-faint);
}
dd {
  min-width: 0;
  margin: 0;
  color: var(--rl-fg);
  font-size: 13px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.is-small dd {
  font-size: 12px;
  line-height: 20px;
  font-weight: 400;
}
.num {
  font-variant-numeric: tabular-nums;
}
.mono {
  font-family: var(--rl-mono);
}
.tone-ok {
  color: var(--rl-ok);
}
.tone-bad {
  color: var(--rl-bad);
}
.is-stacked .jl-ledger__row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  box-sizing: border-box;
  min-height: 40px;
  padding: 10px 14px;
}
.is-stacked dt {
  flex: none;
}
.is-stacked dd {
  text-align: right;
}
</style>
