<script setup lang="ts">
import type { Tone } from "./status-tones";

// A key/value summary set like a ledger: label, dotted leader, value. Values are text only (never HTML).
export interface LedgerItem {
  label: string;
  value: string | number;
  // colours the value; `neutral` or none keeps the text colour
  tone?: Tone;
  // IBM Plex Mono for data (ids, times, quantities); labels are never mono
  mono?: boolean;
}

const props = withDefaults(defineProps<{ items: LedgerItem[]; columns?: 1 | 2 }>(), { columns: 1 });
</script>

<template>
  <dl class="ledger" :class="`ledger--${props.columns}`" data-testid="ledger-list">
    <div v-for="(item, i) in props.items" :key="`${i}-${item.label}`" class="ledger__row">
      <dt class="ledger__label">{{ item.label }}</dt>
      <span class="ledger__leader" aria-hidden="true" />
      <dd
        class="ledger__value"
        :class="[item.mono ? 'is-mono' : '', item.tone && item.tone !== 'neutral' ? `tone-${item.tone}` : '']"
      >
        {{ item.value }}
      </dd>
    </div>
  </dl>
</template>

<style scoped>
.ledger {
  margin: 0;
  display: grid;
  gap: 8px 32px;
  font-size: 13px;
  line-height: 18px;
}
.ledger--1 {
  grid-template-columns: minmax(0, 1fr);
}
.ledger--2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.ledger__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.ledger__label {
  color: var(--rl-muted);
  white-space: nowrap;
}
.ledger__leader {
  flex: 1;
  min-width: 8px;
  border-bottom: 1px dotted var(--rl-line-strong);
  transform: translateY(-4px);
}
.ledger__value {
  margin: 0;
  font-variant-numeric: tabular-nums;
  text-align: right;
  overflow-wrap: anywhere;
}
.ledger__value.is-mono {
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-weight: 500;
}
.tone-ok {
  color: var(--rl-ok);
}
.tone-warn {
  color: var(--rl-warn);
}
.tone-bad {
  color: var(--rl-bad);
}
.tone-info {
  color: var(--rl-info);
}
</style>
