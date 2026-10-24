<script setup lang="ts">
import { computed } from "vue";
import { groupSentence } from "@/components/alerts/alerts-model";
import { IconAlertTriangle } from "@/components/shared/icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useStatsStore } from "@/stores/stats";

// The attention banner as drawn on Jobs (specs/shell.md section 5): one line, a bold sentence with the open alert
// groups, one muted sentence, and the link to Alerts. Jobs renders it after its band card. The muted sentence words
// the first open group (`Dola replied “…” to 4 jobs from 3 keys.`), as drawn even beside live conditions; with no open
// group it is the live condition, the first one and a count of the rest, so the banner never grows. No coloured side
// border (DESIGN section 6).
const stats = useStatsStore();
const { isPhone } = useBreakpoint();

const alerts = computed(() => stats.data?.alerts ?? []);
// a muted alert never vanishes: with nothing else open the banner still says how many are muted, quietly
const visible = computed(() => alerts.value.length > 0 || stats.openGroups > 0 || stats.muted > 0);
// 200 is the page cap of the groups call.
const groupCount = computed(() => (stats.openGroups === 200 ? "200+" : String(stats.openGroups)));
const headline = computed(() =>
  stats.openGroups > 0
    ? `${groupCount.value} open alert ${stats.openGroups === 1 ? "group" : "groups"}.`
    : alerts.value.length
      ? "Needs attention."
      : "Nothing open.",
);
const detail = computed(() => {
  if (stats.firstGroup) return groupSentence(stats.firstGroup, stats.firstReply);
  const [first, ...rest] = alerts.value;
  if (!first) return "";
  return rest.length ? `${first.message} (+${rest.length} more)` : first.message;
});
</script>

<template>
  <div v-if="visible" class="attention-banner" :class="{ 'attention-banner--phone': isPhone }" role="status">
    <div class="attention-row">
      <IconAlertTriangle class="attention-icon" :size="18" />
      <span class="attention-body">
        <strong class="attention-head" data-testid="banner-headline">{{ headline }}</strong>
        {{ " " }}
        <span v-if="detail" class="attention-detail" data-testid="banner-detail">{{ detail }}</span>
        <span v-if="stats.muted" class="attention-detail num" data-testid="banner-muted"
          >{{ detail ? " · " : "" }}{{ stats.muted }} muted</span
        >
      </span>
    </div>
    <router-link class="banner-link" :to="{ name: 'alerts' }" data-testid="review-alerts">
      Review in Alerts
    </router-link>
  </div>
</template>

<style scoped>
.attention-banner {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-radius: 12px;
  background: var(--rl-warn-bg);
  color: var(--rl-fg);
}
.attention-row {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 12px;
  min-width: 240px;
}
.attention-icon {
  flex: none;
  color: var(--rl-warn);
}
.attention-body {
  flex: 1;
  min-width: 0;
}
.attention-head {
  font-weight: 600;
}
.attention-detail {
  color: var(--rl-muted);
}
/* A link on the warn fill takes the warn colour (the primary fails contrast there) and is underlined. */
.attention-banner .banner-link {
  color: var(--rl-warn);
  font-size: inherit;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.attention-banner .banner-link:hover {
  color: var(--rl-warn);
  opacity: 0.8;
}
/* Phone: the icon row, then the link on its own line with a 44 px touch height. */
.attention-banner--phone {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
  gap: 8px;
}
.attention-banner--phone .attention-row {
  align-items: flex-start;
  gap: 10px;
  min-width: 0;
}
.attention-banner--phone .attention-icon {
  margin-top: 1px;
}
.attention-banner--phone .banner-link {
  display: flex;
  align-items: center;
  align-self: flex-start;
  min-height: 44px;
}
</style>
