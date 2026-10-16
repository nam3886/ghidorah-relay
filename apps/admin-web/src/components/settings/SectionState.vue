<script setup lang="ts">
// What a settings card shows in place of its rows: the empty text, or the error with Retry (shared.md 12.5). The rows
// themselves keep their own layout; a load in flight with no rows says so.
defineProps<{ loading: boolean; error: string | null; empty: boolean; emptyText: string }>();
const emit = defineEmits<{ retry: [] }>();
</script>

<template>
  <div v-if="error" class="state__error" role="alert">
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5" />
      <path d="M12 16.5h.01" />
    </svg>
    <span class="state__text">{{ error }}</span>
    <button type="button" class="state__retry" @click="emit('retry')">Retry</button>
  </div>
  <p v-else-if="loading && empty" class="state__note" role="status">Loading…</p>
  <p v-else-if="empty" class="state__note">{{ emptyText }}</p>
</template>

<style scoped>
.state__error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 0 16px 16px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
}
.state__text {
  flex: 1;
  min-width: 220px;
  color: var(--rl-fg);
}
.state__retry {
  height: 32px;
  padding: 0 14px;
  border: 1px solid var(--rl-bad);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-bad);
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
@media (max-width: 767px) {
  .state__retry {
    min-width: 44px;
    min-height: 44px;
  }
}
.state__note {
  margin: 0;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-line);
  font-size: 13px;
  color: var(--rl-muted);
}
</style>
