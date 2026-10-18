<script setup lang="ts">
import { onBeforeUnmount } from "vue";
import type { LogStatus } from "@/api/logs";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import { IconSearch } from "@/components/shared/icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import "./lane-controls.css";
import { useLogsFilters } from "./logs-filters";
import { STATUS_OPTIONS } from "./log-window";

// The filters of the Request logs, drawn as the canvas draws them: the Status select (FilterSelect's field variant, our
// own control drawn as the canvas's select) and native date fields. A choice in Status or a date field asks for a new
// search at once; the search text goes with the Search button or Enter (on a phone there is no button, so the text
// goes after a short pause in typing).
const emit = defineEmits<{ search: [] }>();
const filters = useLogsFilters();
const { isPhone, isTablet } = useBreakpoint();

const TYPING_PAUSE_MS = 350;
let timer: ReturnType<typeof setTimeout> | undefined;
onBeforeUnmount(() => clearTimeout(timer));

// the field's options (a list of its own: the shared words are read-only)
const STATUS_CHOICES = STATUS_OPTIONS.map((o) => ({ ...o }));

// a status choice searches at once
function pickStatus(value: string | number): void {
  filters.status = value as LogStatus;
  emit("search");
}

function typed(): void {
  if (!isPhone.value) return;
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
    <div class="lf__search kla-field">
      <label class="kla-field__label" :class="{ 'sr-only': isPhone }" for="logs-q">Search</label>
      <div class="lf__wrap">
        <IconSearch class="lf__icon" :size="isPhone ? 16 : 15" />
        <input
          id="logs-q"
          v-model="filters.q"
          type="search"
          class="kla-control lf__q"
          placeholder="Job id, reference or title"
          aria-label="Search"
          @input="typed"
        />
      </div>
    </div>

    <div class="lf__row">
      <FilterSelect
        class="lf__status"
        variant="field"
        label="Status"
        title="The request’s own outcome. The Jobs tab filters by job status."
        :model-value="filters.status"
        :options="STATUS_CHOICES"
        @update:model-value="pickStatus"
      />
      <component :is="isPhone ? 'label' : 'div'" class="lf__date" :class="isPhone ? 'kla-chip-field' : 'kla-field'">
        <component :is="isPhone ? 'span' : 'label'" :class="isPhone ? '' : 'kla-field__label'" for="logs-from"
          >From</component
        >
        <input
          id="logs-from"
          v-model="filters.from"
          type="date"
          class="kla-control"
          aria-label="From"
          @change="emit('search')"
        />
      </component>
      <component :is="isPhone ? 'label' : 'div'" class="lf__date" :class="isPhone ? 'kla-chip-field' : 'kla-field'">
        <component :is="isPhone ? 'span' : 'label'" :class="isPhone ? '' : 'kla-field__label'" for="logs-to"
          >To</component
        >
        <input
          id="logs-to"
          v-model="filters.to"
          type="date"
          class="kla-control"
          aria-label="To"
          @change="emit('search')"
        />
      </component>
      <button v-if="!isPhone" type="submit" class="kla-btn kla-btn--primary lf__submit">Search</button>
    </div>
  </form>
</template>

<style scoped>
/* desktop: one wrapping row */
.lf {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  padding: 14px 16px;
}
.lf__row {
  display: contents;
}
/* the canvas's labels keep the page's 20 px line; its buttons carry no line height of their own */
.lf .kla-field__label {
  line-height: 20px;
}
.lf__search {
  flex: 1 1 260px;
  min-width: 200px;
}
.lf__wrap {
  position: relative;
}
.lf__icon {
  position: absolute;
  top: 50%;
  left: 12px;
  transform: translateY(-50%);
  color: var(--rl-faint);
  pointer-events: none;
}
.lf__q {
  width: 100%;
  padding: 0 12px 0 34px;
}
.lf__submit {
  height: 34px;
  padding: 0 16px;
  line-height: normal;
}

/* tablet: the search on its own row, then Status, From, To and the button in one row. One column that does not wrap:
   a wrapping column would size itself to its widest row (the phone's chips) and push the card sideways */
.lf--tablet {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
}
.lf--tablet .lf__search,
.lf--phone .lf__search {
  flex: none;
}
.lf--tablet .lf__q {
  height: 36px;
}
.lf--tablet .lf__row {
  display: flex;
  align-items: flex-end;
  gap: 12px;
}
.lf--tablet .lf__status {
  flex: 1.4;
  min-width: 0;
}
/* the drawn select fills its share of the row */
.lf--tablet .lf__status :deep(.filter-select.is-field) {
  width: 100%;
}
.lf--tablet .lf__date {
  flex: 1;
}
.lf--tablet .lf__date .kla-control {
  width: 100%;
  height: 36px;
}
.lf--tablet .lf__submit {
  flex: none;
  height: 36px;
}

/* phone: the search, then a row of three chips that scrolls sideways */
.lf--phone {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
  gap: 10px;
  padding: 12px 0;
  border-bottom: 1px solid var(--rl-line);
}
.lf--phone .lf__search {
  padding: 0 12px;
}
.lf--phone .lf__icon {
  left: 14px;
}
.lf--phone .lf__q {
  height: 44px;
  padding: 0 12px 0 40px;
  font-size: 14px;
}
.lf--phone .lf__row {
  display: flex;
  gap: 8px;
  padding: 0 12px;
  overflow-x: auto;
}
/* the chip's own date field: the browser's padding and corners, as drawn */
.lf--phone .kla-chip-field .kla-control {
  padding: revert;
  border-radius: revert;
}
</style>
