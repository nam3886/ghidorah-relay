<script setup lang="ts">
import { computed } from "vue";
import CommandChip from "@/components/shared/CommandChip.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { type ChipState, chipProps } from "./account-model";

// The head of the Accounts page, per width. Desktop (Accounts): the title and one sentence, "16 of 24 usable." then
// what needs a look; at the right Re-check all accounts and Refresh catalog as icon buttons and Import accounts.
// Tablet (AccountsTablet): the title is the top bar's; Refresh catalog, Check all and Import sit in a row at the right,
// then a banner says how many need a look. Phone (AccountsPhone): Filter by provider, Check all accounts, Refresh
// catalog and Import accounts as icon buttons at the right, then the summary card with its bars. Each page command
// locks only its own control while it runs, and has its command chip under the controls.
export interface Summary {
  usable: number;
  total: number;
  // the accounts that need a look, and the sentence that names them ("2 dead, 1 proxy failing")
  look: number;
  line: string;
  // the phone card's bars: usable, dead, proxy failing, asleep, low
  bars: number[];
}
const props = defineProps<{
  size: "desktop" | "tablet" | "phone";
  summary: Summary | null;
  checkingAll: boolean;
  readingCatalogs: boolean;
  checkAllChip?: ChipState | null;
  refreshChip?: ChipState | null;
}>();
const emit = defineEmits<{ import: []; checkAll: []; refreshCatalog: []; filter: [] }>();
const pageChips = computed(() =>
  [
    { key: "checkAll", chip: props.checkAllChip },
    { key: "refreshCatalog", chip: props.refreshChip },
  ].filter((c): c is { key: string; chip: ChipState } => !!c.chip),
);
// the bars of the phone card, 4 to 36 px high against the usable count (green, red, red, amber, yellow)
const BAR_TONES = ["ok", "bad", "bad2", "warn", "warn2"];
const bars = computed(() => {
  const b = props.summary?.bars ?? [];
  const top = Math.max(1, b[0] ?? 0, ...b);
  return b.map((n, i) => ({ tone: BAR_TONES[i], height: { height: `${Math.max(4, Math.round((n / top) * 36))}px` } }));
});
</script>

<template>
  <div class="head" :class="`head--${props.size}`">
    <div v-if="props.size === 'desktop'" class="head__text">
      <h1 class="head__title">Accounts</h1>
      <span v-if="props.summary" class="head__sub"
        ><b>{{ props.summary.usable }} of {{ props.summary.total }} usable.</b
        >{{ props.summary.line ? ` ${props.summary.line}.` : "" }}</span
      >
    </div>
    <h1 v-else class="sr-only">Accounts</h1>

    <div class="head__side">
      <span class="head__actions">
        <IconButton
          v-if="props.size === 'phone'"
          icon="filter"
          label="Filter by provider"
          placement="bottom"
          @click="emit('filter')"
        />
        <IconButton
          v-if="props.size !== 'tablet'"
          icon="recheck"
          :label="props.size === 'phone' ? 'Check all accounts' : 'Re-check all accounts'"
          :disabled="props.checkingAll"
          placement="bottom"
          @click="emit('checkAll')"
        />
        <IconButton
          icon="rotate"
          label="Refresh catalog"
          :disabled="props.readingCatalogs"
          placement="bottom"
          @click="emit('refreshCatalog')"
        />
        <button
          v-if="props.size === 'tablet'"
          type="button"
          class="head__btn"
          :aria-disabled="props.checkingAll ? 'true' : undefined"
          @click="!props.checkingAll && emit('checkAll')"
        >
          Check all
        </button>
        <IconButton
          v-if="props.size === 'phone'"
          icon="plus"
          label="Import accounts"
          placement="bottom-end"
          class="head__plus"
          @click="emit('import')"
        />
        <button v-else type="button" class="head__primary" @click="emit('import')">
          {{ props.size === "tablet" ? "Import" : "Import accounts" }}
        </button>
      </span>
      <span v-if="pageChips.length" class="head__cmds">
        <CommandChip v-for="c in pageChips" :key="c.key" v-bind="chipProps(c.chip)" />
      </span>
    </div>

    <!-- the tablet's banner: how many need a look -->
    <div v-if="props.size === 'tablet' && props.summary?.look" class="head__banner" role="status">
      <span
        ><b>{{ props.summary.look }} of {{ props.summary.total }} need a look.</b> {{ props.summary.line }}.</span
      >
    </div>

    <!-- the phone's summary card -->
    <section v-if="props.size === 'phone' && props.summary" class="head__card" aria-label="Summary">
      <div class="head__card-text">
        <span class="num head__big"
          >{{ props.summary.usable }} <span class="head__of">of {{ props.summary.total }} usable</span></span
        >
        <span v-if="props.summary.line" class="head__line">{{ props.summary.line }}</span>
      </div>
      <span class="head__bars" aria-hidden="true">
        <span v-for="(b, i) in bars" :key="i" class="head__bar" :class="`is-${b.tone}`" :style="b.height" />
      </span>
    </section>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
}
.head__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.head__title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.head__sub {
  color: var(--rl-muted);
}
.head__sub b {
  color: var(--rl-fg);
  font-weight: 700;
}
.head__side {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  margin-left: auto;
}
.head__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.head__cmds {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px;
}
.head__primary,
.head__btn {
  height: 36px;
  padding: 0 16px;
  border-radius: 10px;
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.head__primary {
  border: 0;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
}
.head__primary:hover {
  background: var(--rl-primary-hover);
}
.head__btn {
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
}
.head__btn:hover {
  background: var(--rl-track);
}
.head__btn[aria-disabled="true"] {
  background: var(--rl-track);
  color: var(--rl-faint);
  cursor: default;
}
/* the tablet: the buttons at the touch-free 40 px, 700 (JobsTablet's action row) */
.head--tablet .head__primary,
.head--tablet .head__btn {
  height: 40px;
  font-weight: 700;
}
.head--tablet .head__side,
.head--phone .head__side {
  width: 100%;
}
.head__banner {
  flex-basis: 100%;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-size: 13px;
  line-height: 19px;
}
/* the phone: the touch icons, the import one in brand ink */
.head--phone {
  gap: 14px;
}
.head--phone .head__actions {
  gap: 2px;
}
.head--phone .head__actions :deep(.ib) {
  width: 44px;
  height: 44px;
  border-radius: 12px;
}
.head__plus :deep(.ib) {
  color: var(--rl-brand-ink);
}
.head__card {
  display: flex;
  flex-basis: 100%;
  align-items: center;
  gap: 14px;
  box-sizing: border-box;
  padding: 14px 16px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.head__card-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.head__big {
  font-size: 22px;
  line-height: 28px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.head__of {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--rl-muted);
}
.head__line {
  font-size: 12px;
  line-height: 17px;
  color: var(--rl-muted);
}
.head__bars {
  display: flex;
  flex: none;
  align-items: flex-end;
  gap: 2px;
  height: 36px;
}
.head__bar {
  display: block;
  width: 8px;
  border-radius: 2px;
}
.head__bar.is-ok {
  background: var(--rl-live);
}
.head__bar.is-bad {
  background: var(--rl-bad);
}
.head__bar.is-bad2 {
  background: var(--rl-bad-solid);
}
.head__bar.is-warn {
  background: var(--rl-amber);
}
.head__bar.is-warn2 {
  background: var(--rl-amber);
  opacity: 0.7;
}
</style>
