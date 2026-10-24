<script setup lang="ts">
// The design's "Today in numbers" (requests, 2xx, 4xx, 5xx, p95) needs an API that does not exist yet. The view keeps
// this out of the page until it does (LogsView `statsServed`); when the numbers come, they replace this text.
</script>

<template>
  <section class="lv-stats" aria-label="Today in numbers" data-testid="logs-stats">
    <b>Today in numbers</b>
    <span>Requests by status and the 95th-percentile duration are not served by the API yet.</span>
  </section>
</template>

<style scoped>
.lv-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
  padding: 14px 20px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
  font-size: 13px;
  color: var(--rl-muted);
}
.lv-stats b {
  color: var(--rl-fg);
  font-size: 15px;
}
</style>
