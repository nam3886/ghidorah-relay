<script setup lang="ts">
import type { Wallet } from "@/api/keys";
import { computed } from "vue";
import { sortedWallets, usageLabel, usagePercent, walletText } from "./key-model";

// The wallets of one key as ledger lines: the kind, a dotted leader and the value; a limit wallet adds two bars (left =
// today, right = in total). `compact` is the tablet and phone form (smaller value, lines may wrap). The server lists
// wallets by name; the page lists a provider's video wallet first.
const props = defineProps<{ wallets: Wallet[]; compact?: boolean }>();
const ordered = computed(() => sortedWallets(props.wallets));
</script>

<template>
  <div class="wallets" :class="{ 'is-compact': compact }">
    <div v-for="w in ordered" :key="`${w.provider}/${w.kind}`" class="wallet" data-testid="wallet-line">
      <div class="wallet__line">
        <span class="wallet__kind">{{ w.provider }} {{ w.kind }}</span>
        <span class="wallet__leader" aria-hidden="true" />
        <span class="wallet__value" :class="`is-${w.mode}`">{{ walletText(w) }}</span>
      </div>
      <div v-if="w.mode === 'limit'" class="wallet__bars" role="img" :aria-label="usageLabel(w)">
        <span class="wallet__bar"
          ><span class="wallet__fill" :style="{ width: `${usagePercent(w.used_day, w.day_limit)}%` }"
        /></span>
        <span class="wallet__bar"
          ><span class="wallet__fill" :style="{ width: `${usagePercent(w.used_total, w.total_limit)}%` }"
        /></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wallets {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.wallet {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.wallet__line {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  line-height: 16px;
}
.is-compact .wallet__line {
  flex-wrap: wrap;
  gap: 0 8px;
}
.wallet__kind {
  color: var(--rl-muted);
  white-space: nowrap;
}
.wallet__leader {
  flex: 1;
  min-width: 8px;
  border-bottom: 1px dotted var(--rl-line-strong);
  transform: translateY(-3px);
}
.wallet__value {
  font-family: var(--rl-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  color: var(--rl-fg);
}
.is-compact .wallet__value {
  font-size: 11px;
}
.wallet__value.is-closed {
  color: var(--rl-faint);
}
.wallet__bars {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.wallet__bar {
  display: block;
  height: 4px;
  border-radius: 2px;
  background: var(--rl-line);
}
.wallet__fill {
  display: block;
  height: 4px;
  border-radius: 2px;
  background: var(--rl-primary);
}
</style>
