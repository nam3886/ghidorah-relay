<script setup lang="ts">
import LedgerList, { type LedgerItem } from "@/components/shared/LedgerList.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";

// The card above the list: two ledger panels side by side (stacked on a phone). Left, the health of the accounts;
// right, the points and checks of the provider's pool (specs/accounts.md section 2).
// `count` is null until the list has been read (no "0 accounts" while it loads).
defineProps<{ count: number | null; health: LedgerItem[]; pool: LedgerItem[]; poolTitle: string }>();
const { isPhone, isTablet } = useBreakpoint();
</script>

<template>
  <section
    class="pool-card"
    :class="{ 'pool-card--tablet': isTablet, 'pool-card--phone': isPhone }"
    aria-labelledby="pool-health-title"
    data-testid="pool-card"
  >
    <div class="panel panel--health">
      <h2 id="pool-health-title" class="panel__title">
        Pool health
        <span v-if="count !== null" class="tnum count">· {{ count }} {{ count === 1 ? "account" : "accounts" }}</span>
      </h2>
      <LedgerList :items="health" variant="result" />
    </div>
    <div class="panel panel--pool">
      <h2 class="panel__title">{{ poolTitle }}</h2>
      <LedgerList :items="pool" variant="result" />
    </div>
  </section>
</template>

<style scoped>
.pool-card {
  display: flex;
  flex-wrap: wrap;
  overflow: hidden;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.pool-card--phone {
  display: block;
}
.panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  padding: 18px 20px;
}
.panel--health {
  flex: 1 1 420px;
}
.panel--pool {
  flex: 1 1 360px;
  background: var(--rl-raised);
}
.pool-card--tablet .panel {
  flex: 1 1 300px;
  gap: 10px;
  padding: 16px 20px;
}
.pool-card--phone .panel {
  gap: 10px;
  padding: 16px;
}
.panel__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}
.count {
  color: var(--rl-muted);
  font-weight: 400;
  font-variant-numeric: tabular-nums;
}
/* the rows sit 6 px apart and are 20 px high in this card (the shared list is 18 px high) */
.panel :deep(.ledger) {
  flex: none;
  align-self: stretch;
  gap: 6px;
  line-height: 20px;
}
</style>
