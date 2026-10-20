<script setup lang="ts">
import { computed } from "vue";
import type { FixedLimit } from "@/api/settings";
import SectionState from "./SectionState.vue";
import "./settings-leader.css";
import "./settings-skeleton.css";

// The limits that are code, not settings: one block per group, in the order the server gives (the core first, then each
// provider). Read with the settings: their failure and their wait are this card's too.
const props = defineProps<{ rows: FixedLimit[]; loading: boolean; error: string | null }>();
const emit = defineEmits<{ retry: [] }>();

const groups = computed(() => {
  const out: { name: string; rows: FixedLimit[] }[] = [];
  for (const row of props.rows) {
    let block = out[out.length - 1];
    if (!block || block.name !== row.group) {
      block = { name: row.group, rows: [] };
      out.push(block);
    }
    block.rows.push(row);
  }
  return out;
});
const SKELETON = ["120px", "168px", "96px", "144px"];
</script>

<template>
  <div class="limits" :aria-busy="loading">
    <SectionState :error="error" :empty="!rows.length && !loading" empty-text="None" @retry="emit('retry')" />
    <dl v-if="!error && !rows.length && loading" class="set-leader" aria-hidden="true">
      <div v-for="w in SKELETON" :key="w" class="set-leader__row">
        <i class="set-skel" :style="{ width: w }" />
        <span class="set-leader__line" />
        <i class="set-skel" :style="{ width: '72px' }" />
      </div>
    </dl>
    <template v-else-if="!error && rows.length">
      <section v-for="g in groups" :key="g.name" class="limits__group">
        <h3 class="limits__name">{{ g.name }}</h3>
        <dl class="set-leader" :aria-label="`Fixed limits of ${g.name}`">
          <div v-for="row in g.rows" :key="row.name" class="set-leader__item">
            <div class="set-leader__row">
              <dt>{{ row.name }}</dt>
              <span class="set-leader__line" aria-hidden="true" />
              <dd class="num">{{ row.value }}</dd>
            </div>
            <dd class="set-leader__why">{{ row.reason }}</dd>
          </div>
        </dl>
      </section>
    </template>
  </div>
</template>

<style scoped>
.limits__name {
  margin: 0;
  padding: 10px 20px 6px;
  border-top: 1px solid var(--rl-line);
  background: var(--rl-surface);
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  color: var(--rl-muted);
}
@media (max-width: 767px) {
  .limits__name {
    padding: 10px 16px 6px;
  }
}
</style>
