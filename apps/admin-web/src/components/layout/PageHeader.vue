<script setup lang="ts">
import { useBreakpoint } from "@/composables/use-breakpoint";

// The head of every page: the title with one muted sentence on the left, `meta` ("Updated hh:mm:ss") and `actions`
// (Refresh, the primary action) on the right. On a phone the meta line is left out and the actions stay beside the
// title, as drawn.
defineProps<{ title: string; subtitle?: string }>();
const { isPhone, isTablet } = useBreakpoint();
</script>

<template>
  <div
    class="page-header"
    :class="{ 'page-header--phone': isPhone, 'page-header--tablet': isTablet, 'page-header--top': $slots.submeta }"
  >
    <div class="page-header__text">
      <h1 class="page-header__title">{{ title }}</h1>
      <p v-if="subtitle" class="page-header__subtitle">{{ subtitle }}</p>
      <span v-if="$slots.submeta" class="page-header__submeta"><slot name="submeta" /></span>
    </div>
    <div v-if="($slots.meta && !isPhone) || $slots.actions" class="page-header__side">
      <span v-if="$slots.meta && !isPhone" class="page-header__meta"><slot name="meta" /></span>
      <span v-if="$slots.actions" class="page-header__actions"><slot name="actions" /></span>
    </div>
  </div>
</template>

<style scoped>
.page-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
}
.page-header--phone {
  flex-wrap: nowrap;
  align-items: center;
}
.page-header--top {
  align-items: flex-start;
}
.page-header__text {
  min-width: 0;
}
.page-header__title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 600;
  letter-spacing: -0.015em;
}
.page-header--tablet .page-header__title {
  font-size: 24px;
  line-height: 32px;
  letter-spacing: -0.012em;
}
.page-header--phone .page-header__title {
  font-size: 22px;
  line-height: 30px;
  letter-spacing: -0.01em;
}
.page-header__subtitle {
  margin: 4px 0 0;
  color: var(--rl-muted);
}
.page-header--tablet .page-header__subtitle,
.page-header--phone .page-header__subtitle {
  margin-top: 2px;
}
.page-header__submeta {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-faint);
  font-variant-numeric: tabular-nums;
}
.page-header__side {
  display: flex;
  flex: none;
  align-items: center;
  gap: 10px;
}
.page-header__meta {
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-faint);
  font-variant-numeric: tabular-nums;
}
.page-header__actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
</style>
