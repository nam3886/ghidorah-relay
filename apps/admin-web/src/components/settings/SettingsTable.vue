<script setup lang="ts">
import { computed, reactive, watch } from "vue";
import type { SettingItem } from "@/api/settings";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import SectionState from "./SectionState.vue";
import { valueText } from "./settings-value";

// The live settings. Each value is an input with its own draft: it is "edited" (and Save is enabled) only while the
// draft differs from the stored value. The view does the calls and reloads; a reload gives every draft its stored value.
// Desktop is a five-column table, tablet folds the Default column under the value, a phone gets a list.
const props = defineProps<{ items: SettingItem[]; loading: boolean; error: string | null; busy: boolean }>();
const emit = defineEmits<{ save: [key: string, text: string]; reset: [key: string]; retry: [] }>();
const { isPhone, isTablet } = useBreakpoint();

const drafts = reactive<Record<string, string>>({});
watch(
  () => props.items,
  (items) => {
    for (const key of Object.keys(drafts)) delete drafts[key];
    for (const item of items) drafts[item.key] = valueText(item);
  },
  { immediate: true },
);

const edited = (item: SettingItem): boolean => drafts[item.key] !== valueText(item);
const canReset = (item: SettingItem): boolean => !props.busy && (item.source === "db" || edited(item));
const send = (item: SettingItem): void => {
  if (edited(item)) emit("save", item.key, drafts[item.key] ?? "");
};
// What the sweep and the tests read: the load state of the card
const state = computed(() =>
  props.error ? "error" : props.loading && !props.items.length ? "loading" : !props.items.length ? "empty" : "ready",
);
const shownDefault = (item: SettingItem): string => JSON.stringify(item.default);
</script>

<template>
  <div
    class="settings-table"
    :class="{ 'is-tablet': isTablet, 'is-phone': isPhone }"
    :aria-busy="loading"
    :data-state="state"
  >
    <SectionState
      :loading="loading"
      :error="error"
      :empty="!items.length"
      empty-text="No settings"
      @retry="emit('retry')"
    />

    <ul v-if="!error && items.length && isPhone" class="list">
      <li v-for="row in items" :key="row.key" :class="{ 'is-edited': edited(row) }">
        <div>
          <div class="key">{{ row.key }}</div>
          <div class="description">{{ row.description }}</div>
        </div>
        <div class="list__value">
          <input
            v-model="drafts[row.key]"
            class="input"
            :class="{ 'is-edited': edited(row) }"
            :aria-label="`Value of ${row.key}`"
            :disabled="busy"
            autocomplete="off"
            spellcheck="false"
            @keyup.enter="send(row)"
          />
          <span v-if="edited(row)" class="tag tag--phone">edited</span>
        </div>
        <span class="list__meta tnum"
          >{{ row.source }} · default <span class="mono">{{ shownDefault(row) }}</span></span
        >
        <div class="list__actions">
          <button
            type="button"
            class="btn btn--save"
            :disabled="busy || !edited(row)"
            :aria-label="`Save ${row.key}`"
            @click="send(row)"
          >
            Save
          </button>
          <button
            type="button"
            class="btn btn--reset"
            :disabled="!canReset(row)"
            :aria-label="`Reset ${row.key}`"
            @click="emit('reset', row.key)"
          >
            Reset
          </button>
        </div>
      </li>
    </ul>

    <div v-else-if="!error && items.length" class="scroll">
      <table class="table">
        <thead>
          <tr>
            <th scope="col" class="first">Setting</th>
            <th scope="col">Value</th>
            <th scope="col">Source</th>
            <th v-if="!isTablet" scope="col">Default</th>
            <th scope="col" class="last">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in items" :key="row.key" :class="{ 'is-edited': edited(row) }">
            <td class="first">
              <span class="key">{{ row.key }}</span>
              <span class="description">{{ row.description }}</span>
            </td>
            <td class="value">
              <input
                v-model="drafts[row.key]"
                class="input"
                :class="{ 'is-edited': edited(row) }"
                :aria-label="`Value of ${row.key}`"
                :disabled="busy"
                autocomplete="off"
                spellcheck="false"
                @keyup.enter="send(row)"
              />
              <!-- the drawn cell has a space between the box and its tag, and between Save and Reset -->
              {{ " " }}<span v-if="edited(row)" class="tag">edited</span>
              <span v-if="isTablet" class="folded mono tnum">default {{ shownDefault(row) }}</span>
            </td>
            <td><StatusChip :status="row.source" :label="row.source" tone="neutral" /></td>
            <td v-if="!isTablet" class="mono muted tnum">
              <span class="default">{{ shownDefault(row) }}</span>
            </td>
            <td class="last">
              <button
                type="button"
                class="btn btn--save"
                :disabled="busy || !edited(row)"
                :aria-label="`Save ${row.key}`"
                @click="send(row)"
              >
                Save
              </button>
              {{ " "
              }}<button
                type="button"
                class="btn btn--reset"
                :disabled="!canReset(row)"
                :aria-label="`Reset ${row.key}`"
                @click="emit('reset', row.key)"
              >
                Reset
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.scroll {
  overflow-x: auto;
}
.table {
  width: 100%;
  min-width: 900px;
  border-collapse: separate;
  border-spacing: 0;
}
.is-tablet .table {
  min-width: 0;
}
.table th {
  height: 38px;
  padding: 0 12px;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  color: var(--rl-muted);
  background: var(--rl-raised);
}
.table th.first {
  padding-left: 16px;
}
.table th.last,
.table td.last {
  padding: 0 16px 0 12px;
  text-align: right;
  white-space: nowrap;
}
.table td {
  padding: 0 12px;
  border-bottom: 1px solid var(--rl-line);
}
.table td.first {
  height: 56px;
  box-sizing: border-box;
  padding: 8px 12px 8px 16px;
}
/* the drawn tablet cell sets a min-height, which a table cell ignores: its rows are as tall as their text */
.is-tablet .table td.first {
  height: auto;
  padding: 10px 12px 10px 16px;
}
.table tbody tr:hover td {
  background: var(--rl-raised);
}
.table tbody tr.is-edited td {
  background: var(--rl-primary-soft);
}
.table tbody tr.is-edited:hover td {
  background: var(--rl-raised);
}
.key {
  display: block;
  font-family: var(--rl-mono);
  font-size: 13px;
  font-weight: 500;
}
.is-tablet .key,
.is-phone .key {
  overflow-wrap: anywhere;
}
.description {
  display: block;
  font-size: 12px;
  color: var(--rl-muted);
}
.is-phone .description {
  line-height: 18px;
}
.table td.value {
  padding: 8px 12px;
  white-space: nowrap;
}
.input {
  box-sizing: border-box;
  width: 120px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font-family: var(--rl-mono);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.input.is-edited {
  border-color: var(--rl-primary);
}
.tag {
  display: inline-flex;
  align-items: center;
  height: 22px;
  margin-left: 8px;
  padding: 0 8px;
  border-radius: 12px;
  background: var(--rl-primary-soft);
  color: var(--rl-fg);
  font-size: 11px;
  font-weight: 500;
  vertical-align: middle;
}
.mono {
  font-family: var(--rl-mono);
  font-size: 13px;
}
/* the canvas's `num`: tabular figures only (the app's global .num also aligns right) */
.tnum {
  font-variant-numeric: tabular-nums;
}
/* A default is one word when it is a table (dola.prices: some 300 characters of JSON): it wraps inside a column of
   at most 240 px, as the canvas wraps a long key, instead of widening the table past its card. */
.default {
  display: block;
  max-width: 240px;
  overflow-wrap: anywhere;
}
.muted {
  color: var(--rl-muted);
}
.btn {
  box-sizing: border-box;
  height: 32px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  cursor: pointer;
}
.btn--save {
  padding: 0 14px;
  font-weight: 600;
  border: 0;
  background: var(--rl-primary);
  color: #ffffff;
}
.btn--save:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.btn--save:disabled {
  border: 1px solid var(--rl-line-strong);
  background: transparent;
  color: var(--rl-faint);
  cursor: default;
}
.btn--reset {
  margin-left: 6px;
  padding: 0 12px;
}
.btn--reset:hover:not(:disabled) {
  background: var(--rl-raised);
}
.btn--reset:disabled {
  color: var(--rl-faint);
  cursor: default;
}

/* tablet */
.is-tablet .input {
  width: 96px;
  height: 36px;
}
.is-tablet .tag {
  margin-left: 6px;
}
.folded {
  display: block;
  max-width: 200px;
  margin-top: 2px;
  font-size: 12px;
  color: var(--rl-muted);
  white-space: normal;
  overflow-wrap: anywhere;
}
.is-tablet .btn {
  height: 36px;
}
.is-tablet .btn--reset {
  margin-left: 4px;
}

/* phone */
.list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.list li {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-line);
}
.list li.is-edited {
  background: var(--rl-primary-soft);
}
.list__value {
  display: flex;
  align-items: center;
  gap: 8px;
}
.is-phone .input {
  flex: 1;
  min-width: 0;
  width: 100%;
  height: 44px;
  padding: 0 12px;
  font-size: 14px;
}
.tag--phone {
  flex: none;
  height: 24px;
  margin: 0;
  padding: 0 10px;
  border: 1px solid var(--rl-primary);
}
.list__meta {
  font-size: 12px;
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}
.list__meta .mono {
  font-size: 12px;
}
.list__actions {
  display: flex;
  gap: 8px;
}
/* the drawn phone buttons set no padding: the browser's 1px 6px */
.is-phone .btn,
.is-phone .btn--save,
.is-phone .btn--reset {
  flex: 1;
  height: 44px;
  margin-left: 0;
  padding: 1px 6px;
  font-size: 14px;
}
</style>
