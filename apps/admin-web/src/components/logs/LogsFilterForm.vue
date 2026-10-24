<script setup lang="ts">
import { computed, onBeforeUnmount } from "vue";
import type { LogStatus } from "@/api/logs";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import { IconSearch } from "@/components/shared/icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import LogsDatesField from "./LogsDatesField.vue";
import { useLogsFilters } from "./logs-filters";
import { STATUS_OPTIONS } from "./log-window";

// The filters of the Request logs, drawn as Logs.dc.html draws them. A desktop: one row with the Status segments, the
// date range and the search field, and under it the Provider chips. A tablet or phone: the Status chips (a set of 4
// wraps on a phone), the Provider chips, and the search field only while the header's Search button has it open; the
// dates sit in the Filters sheet. A choice in Status or Provider, or a range, asks for a new search at once; the search
// text goes with Enter (a tablet or phone has no Enter to rely on, so the text goes after a short pause in typing).
defineProps<{ searchOpen: boolean }>();
const emit = defineEmits<{ search: [] }>();
const filters = useLogsFilters();
const { isPhone, isTablet } = useBreakpoint();
const compact = computed(() => isPhone.value || isTablet.value);

const TYPING_PAUSE_MS = 350;
let timer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(timer));

// the options (a list of its own: the shared words are read-only)
const STATUS_CHOICES = STATUS_OPTIONS.map((o) => ({ ...o }));

// a status choice searches at once
function pickStatus(value: string | number): void {
  filters.status = value as LogStatus;
  emit("search");
}
function typed(): void {
  if (!compact.value) return;
  clearTimeout(timer);
  timer = setTimeout(() => emit("search"), TYPING_PAUSE_MS);
}
</script>

<template>
  <form
    class="lf"
    :class="{ 'lf--tablet': isTablet, 'lf--phone': isPhone }"
    role="search"
    @submit.prevent="emit('search')"
  >
    <div class="lf__bar">
      <div v-if="!compact" class="lf__seg" role="group" aria-label="Status">
        <button
          v-for="o in STATUS_CHOICES"
          :key="o.value"
          type="button"
          class="lf__seg-btn"
          :class="{ 'is-on': filters.status === o.value }"
          :aria-pressed="filters.status === o.value"
          @click="pickStatus(o.value)"
        >
          {{ o.label }}
        </button>
      </div>
      <ArrowScroller
        v-else
        class="lf__chips"
        label="Status"
        :count="STATUS_CHOICES.length"
        :active-key="filters.status"
      >
        <button
          v-for="o in STATUS_CHOICES"
          :key="o.value"
          type="button"
          class="chip"
          :class="{ 'is-on': filters.status === o.value }"
          :aria-pressed="filters.status === o.value"
          @click="pickStatus(o.value)"
        >
          {{ o.label }}
        </button>
      </ArrowScroller>
      <LogsDatesField v-if="!compact" @search="emit('search')" />
      <label v-if="!compact || searchOpen" class="lf__search" :class="{ 'is-row': compact }">
        <IconSearch class="lf__icon" :size="15" />
        <input
          id="logs-q"
          v-model="filters.q"
          type="search"
          class="lf__q"
          placeholder="Job id, reference or title"
          aria-label="Search"
          @input="typed"
          @keydown.enter.prevent="emit('search')"
        />
      </label>
    </div>
  </form>
</template>

<style scoped>
.lf {
  display: flex;
  flex-direction: column;
}
.lf__bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 10px;
  padding: 14px 20px;
}
/* the Status segments: a track with the chosen one raised */
.lf__seg {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 10px;
  background: var(--rl-track);
}
.lf__seg-btn {
  height: 28px;
  padding: 0 12px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.lf__seg-btn.is-on {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
}
.lf__seg-btn:focus-visible,
.chip:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}

/* the search field, at the right of the bar */
.lf__search {
  display: flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  width: 260px;
  max-width: 100%;
  height: 34px;
  margin-left: auto;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-faint);
}
.lf__search:focus-within {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.lf__icon {
  flex: none;
}
.lf__q {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
}
.lf__q::placeholder {
  color: var(--rl-faint);
}

/* the chips: one pill shape for status and provider */
.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--rl-line);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.chip.is-on {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}

/* a tablet and a phone: the chips fill the bar, the touch height, the search on its own row */
.lf--tablet .lf__bar,
.lf--phone .lf__bar {
  flex-direction: column;
  align-items: stretch;
  flex-wrap: nowrap;
  gap: 4px;
  padding: 12px 16px 8px;
}
.lf--tablet .chip {
  height: 40px;
  padding: 0 14px;
}
.lf--phone .chip {
  height: 44px;
  padding: 0 14px;
}
.lf--tablet .lf__search.is-row,
.lf--phone .lf__search.is-row {
  width: auto;
  height: 44px;
  margin: 4px 0 8px;
}
</style>
