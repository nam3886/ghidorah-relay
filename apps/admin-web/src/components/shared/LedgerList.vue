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
  // an old value shown struck through before the value (`1 → 0`), and a short word after it in the ok colour
  was?: string | number;
  note?: string;
}

// `variant` picks the drawn box (specs/shared.md section 5):
//   band    the Jobs band ledger: a raised panel, every value mono 500; `columns` 2 is the tablet grid
//   drawer  a drawer summary: no panel, values mono only where the row says so, weight 400
//   result  the Test result ledger: no panel, every value mono 500
//   plain   a summary in the body font: no panel, every value weight 400 and never mono
// The `value` slot (props `item`, `index`) replaces the value text, so a value can be a link.
const props = withDefaults(
  defineProps<{ items: LedgerItem[]; columns?: 1 | 2; variant?: "band" | "drawer" | "result" | "plain" }>(),
  {
    columns: 1,
    variant: "band",
  },
);
</script>

<template>
  <dl class="ledger" :class="[`ledger--${props.columns}`, `ledger--${props.variant}`]" data-testid="ledger-list">
    <div v-for="(item, i) in props.items" :key="`${i}-${item.label}`" class="ledger__row">
      <dt class="ledger__label">{{ item.label }}</dt>
      <span class="ledger__leader" aria-hidden="true" />
      <dd
        class="ledger__value"
        :class="[item.mono ? 'is-mono' : '', item.tone && item.tone !== 'neutral' ? `tone-${item.tone}` : '']"
      >
        <slot name="value" :item="item" :index="i">
          <s v-if="item.was !== undefined" class="ledger__was">{{ item.was }}</s>
          <template v-if="item.was !== undefined"> → </template>{{ item.value }}
          <span v-if="item.note" class="ledger__note">{{ item.note }}</span>
        </slot>
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
  line-height: 20px;
}
.ledger--1 {
  grid-template-columns: minmax(0, 1fr);
}
.ledger--2 {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-content: start;
}
.ledger--band {
  flex: 1 1 280px;
  padding: 16px 20px;
  background: var(--rl-raised);
}
.ledger--band.ledger--1 {
  gap: 6px;
}
.ledger--result {
  flex: 1 1 200px;
  align-self: center;
  gap: 6px;
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
.ledger--band .ledger__value,
.ledger--result .ledger__value,
.ledger__value.is-mono {
  font-family: var(--rl-mono, "IBM Plex Mono", ui-monospace, monospace);
  font-weight: 500;
}
.ledger--drawer .ledger__value.is-mono {
  font-weight: 400;
}
.ledger--plain .ledger__value,
.ledger--plain .ledger__value.is-mono {
  font-family: inherit;
  font-weight: 400;
}
@media (max-width: 767.98px) {
  .ledger--band {
    padding: 14px 16px;
    gap: 8px;
  }
}
.ledger__was {
  color: var(--rl-muted);
}
.ledger__note {
  color: var(--rl-ok);
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
