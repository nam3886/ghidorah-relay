<script setup lang="ts">
import { computed } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { IconCheck, IconMoreHorizontal, IconPlus, IconRefresh } from "@/components/shared/icons";
import ActionMenu, { type MenuItem } from "@/components/shared/ActionMenu.vue";
import CommandChip from "@/components/shared/CommandChip.vue";
import { type ChipState, chipProps } from "./account-model";

// The head of the Accounts page, drawn per width (specs/accounts.md section 1): Check all and Refresh catalog sit
// beside Import on the desktop and fold into a More menu on a tablet and a phone; the Updated line moves under the
// subtitle. Each page-wide command locks only its own entry while it runs, and has its command chip (B-States
// "Command status") under its button on a desktop, under the More menu's trigger on a tablet and a phone.
const props = defineProps<{
  updated: string | null;
  checkingAll: boolean;
  readingCatalogs: boolean;
  checkAllChip?: ChipState | null;
  refreshChip?: ChipState | null;
}>();
const emit = defineEmits<{ import: []; checkAll: []; refreshCatalog: [] }>();
const { isPhone, isTablet } = useBreakpoint();
const pageChips = computed(() =>
  [
    { key: "checkAll", chip: props.checkAllChip },
    { key: "refreshCatalog", chip: props.refreshChip },
  ].filter((c): c is { key: string; chip: ChipState } => !!c.chip),
);

const SUBTITLE = "The dola account pool: health, points, cooldowns and proxies.";
const updatedLine = computed(() => (props.updated ? `Updated ${props.updated} · auto every 10 s` : null));
const items = computed<MenuItem[]>(() => [
  { key: "checkAll", label: "Check all", icon: IconCheck, disabled: props.checkingAll },
  { key: "refreshCatalog", label: "Refresh catalog", icon: IconRefresh, disabled: props.readingCatalogs },
]);

function pick(key: string): void {
  if (key === "checkAll") emit("checkAll");
  else emit("refreshCatalog");
}
</script>

<template>
  <div v-if="isPhone" class="head head--phone">
    <div class="title-row">
      <h1 class="title">Accounts</h1>
      <div class="cluster">
        <button type="button" class="primary primary--phone" @click="emit('import')">
          <IconPlus :size="16" :stroke="2" />Import
        </button>
        <ActionMenu
          :items="items"
          trigger-label="More actions"
          menu-label="More actions"
          variant="headerPhone"
          :size="44"
          :icon-size="18"
          bordered
          ink-text
          @select="pick"
        />
      </div>
    </div>
    <!-- the page commands sit in the More menu: their chips go under its trigger -->
    <div v-if="pageChips.length" class="cmd-line cmd-line--end">
      <CommandChip v-for="c in pageChips" :key="c.key" v-bind="chipProps(c.chip)" />
    </div>
    <p class="subtitle">{{ SUBTITLE }}</p>
    <p v-if="updatedLine" class="tnum updated updated--phone">{{ updatedLine }}</p>
  </div>
  <div v-else-if="isTablet" class="head head--tablet">
    <div class="text">
      <h1 class="title">Accounts</h1>
      <p class="subtitle">{{ SUBTITLE }}</p>
      <span v-if="updatedLine" class="tnum updated updated--block">{{ updatedLine }}</span>
    </div>
    <div class="cluster-col">
      <div class="cluster">
        <ActionMenu
          :items="items"
          trigger-label="More actions: Check all, Refresh catalog"
          menu-label="More actions"
          variant="header"
          :icon="IconMoreHorizontal"
          :size="36"
          bordered
          @select="pick"
        />
        <button type="button" class="primary" @click="emit('import')"><IconPlus :size="15" :stroke="2" />Import</button>
      </div>
      <!-- the page commands sit in the More menu: their chips go under its trigger -->
      <div v-if="pageChips.length" class="cmd-line">
        <CommandChip v-for="c in pageChips" :key="c.key" v-bind="chipProps(c.chip)" />
      </div>
    </div>
  </div>
  <div v-else class="head head--desktop" :class="{ 'has-chips': pageChips.length }">
    <div class="text">
      <h1 class="title">Accounts</h1>
      <p class="subtitle">{{ SUBTITLE }}</p>
    </div>
    <div class="cluster cluster--wide">
      <span v-if="updatedLine" class="tnum updated">{{ updatedLine }}</span>
      <!-- each command's chip under its button; the head makes room below while one shows -->
      <span class="cmd">
        <button
          type="button"
          class="secondary"
          :aria-disabled="props.checkingAll ? 'true' : undefined"
          @click="!props.checkingAll && emit('checkAll')"
        >
          <IconCheck :size="15" />Check all
        </button>
        <span v-if="props.checkAllChip" class="cmd__chip"><CommandChip v-bind="chipProps(props.checkAllChip)" /></span>
      </span>
      <span class="cmd">
        <button
          type="button"
          class="secondary"
          :aria-disabled="props.readingCatalogs ? 'true' : undefined"
          @click="!props.readingCatalogs && emit('refreshCatalog')"
        >
          <IconRefresh :size="15" />Refresh catalog
        </button>
        <span v-if="props.refreshChip" class="cmd__chip"><CommandChip v-bind="chipProps(props.refreshChip)" /></span>
      </span>
      <button type="button" class="primary" @click="emit('import')"><IconPlus :size="15" :stroke="2" />Import</button>
    </div>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
.head--desktop {
  flex-wrap: wrap;
  align-items: flex-end;
}
.head--tablet {
  align-items: flex-start;
}
.head--phone {
  flex-direction: column;
  gap: 0;
}
.text {
  min-width: 0;
}
.title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 600;
  letter-spacing: -0.015em;
}
.head--tablet .title {
  font-size: 24px;
  line-height: 32px;
  letter-spacing: -0.012em;
}
.head--phone .title {
  font-size: 22px;
  line-height: 30px;
  letter-spacing: -0.01em;
}
.subtitle {
  margin: 4px 0 0;
  color: var(--rl-muted);
}
.head--tablet .subtitle,
.head--phone .subtitle {
  margin-top: 2px;
}
.title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.cluster {
  display: flex;
  align-items: center;
  flex: none;
  gap: 8px;
}
.cluster--wide {
  flex-wrap: wrap;
  gap: 10px;
}
/* the command chips (B-States "Command status"): desktop, under each button, in the room the head makes below it */
.head--desktop.has-chips {
  padding-bottom: 32px;
}
.cmd {
  position: relative;
  display: flex;
}
.cmd__chip {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  display: flex;
  width: max-content;
  /* up to the next button: two chips never overlap; a longer error ends in "…" */
  max-width: calc(100% + 10px);
}
/* tablet and phone: a line under the More menu's trigger */
.cluster-col {
  display: flex;
  flex: none;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.cmd-line {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.cmd-line--end {
  align-items: flex-end;
  margin-top: 8px;
}
.updated {
  color: var(--rl-faint);
  font-size: 12px;
}
.updated--block {
  display: block;
  margin-top: 4px;
}
.updated--phone {
  margin: 6px 0 0;
}
.tnum {
  font-variant-numeric: tabular-nums;
}
.primary,
.secondary {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  border-radius: 10px;
  font-family: inherit;
  font-size: 13px;
  cursor: pointer;
}
/* Import is a link on the canvas: its text line is the page's 20 px (the buttons keep their own, normal) */
.primary {
  line-height: 20px;
}
.primary {
  padding: 0 16px;
  border: 0;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font-weight: 600;
}
.primary:hover {
  background: var(--rl-primary-hover);
}
.primary--phone {
  height: 44px;
  font-size: 14px;
}
.secondary {
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
}
.secondary:hover {
  background: var(--rl-raised);
}
.secondary[aria-disabled="true"] {
  background: var(--rl-raised);
  color: var(--rl-faint);
  cursor: default;
}
</style>
