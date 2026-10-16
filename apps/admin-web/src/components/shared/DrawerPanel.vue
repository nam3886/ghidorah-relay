<script setup lang="ts">
import { drawerPanelId, drawerTabId } from "./drawer-tab-ids";

// The body of one drawer tab, a tab panel labelled by its tab. Give it the same `idPrefix` as the DrawerTabs and
// its tab's `name` (the tab key). Only the panel of the selected tab is shown; the others stay hidden in the page.
const props = defineProps<{ idPrefix: string; name: string; selected: string }>();
</script>

<template>
  <div
    v-show="props.name === props.selected"
    :id="drawerPanelId(props.idPrefix, props.name)"
    role="tabpanel"
    :aria-labelledby="drawerTabId(props.idPrefix, props.name)"
  >
    <slot />
  </div>
</template>
