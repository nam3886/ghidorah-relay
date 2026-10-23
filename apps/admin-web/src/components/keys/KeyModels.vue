<script setup lang="ts">
import { computed } from "vue";
import type { CatalogModel } from "@/api/shell";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import { modelGroups } from "./key-model";

// The models a key may use: "any model" as a quiet tag, else one line per provider, its tile then a plain comma list;
// a paused model is greyed with "(paused)". `only`: that provider's line alone.
const props = defineProps<{ models: string[]; catalog: readonly CatalogModel[]; only?: string }>();
const groups = computed(() => modelGroups(props.models, props.catalog, props.only));
</script>

<template>
  <span v-if="models.includes('*')" class="k-tag">any model</span>
  <div v-else class="k-models">
    <span v-for="g in groups" :key="g.provider" class="k-mg">
      <ProviderTile v-if="g.provider" :provider="g.provider" class="k-mg__tile" />
      <span class="k-mg__list"
        ><span v-if="g.provider" class="sr-only">{{ g.provider }}: </span
        ><template v-for="(m, i) in g.models" :key="m.id"
          ><template v-if="i">, </template><span v-if="m.paused" class="k-mg__paused">{{ m.id }} (paused)</span
          ><template v-else>{{ m.id }}</template></template
        ></span
      >
    </span>
  </div>
</template>

<style scoped>
.k-tag {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 8px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
  font-size: 12px;
  line-height: 16px;
}
.k-models {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.k-mg {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 18px;
}
/* the small tile sits on the text's first line */
.k-mg .k-mg__tile {
  width: 16px;
  height: 16px;
  border-radius: 5px;
  font-size: 9px;
  transform: translateY(3px);
}
.k-mg__list {
  min-width: 0;
  color: var(--rl-fg);
  overflow-wrap: anywhere;
}
.k-mg__paused {
  color: var(--rl-faint);
}
</style>
