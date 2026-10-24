<script setup lang="ts">
import { computed } from "vue";
import type { FrameModel } from "@/api/frames";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useScopeStore } from "@/stores/scope";
import { lockDay, lockSentence, versionTag } from "./frames-model";
import type { FramesController } from "./use-frames";

// "Models of this kind" (Frames.dc.html): a row per model with where its frame comes from (own or the default), the
// version in force and Keep own, the lock that keeps a model's own text when a default is applied to all. The model
// id is the button that puts that model's frame in the editor (the open one is on the head tint); the id is plain text,
// no colour. A phone draws the rows as a list. While the first read runs the list is flat bars in the real columns.
const props = defineProps<{ frames: FramesController }>();
const { isPhone, isTablet } = useBreakpoint();
const scope = useScopeStore();

const models = computed(() => props.frames.kindOf(props.frames.shown?.kind ?? "")?.models ?? []);
const reading = computed(() => props.frames.loading > 0 && !props.frames.data);
const nameOf = (row: FrameModel): string => (row.name && row.name !== row.model ? row.name : "");
const paused = (row: FrameModel): boolean =>
  (scope.models ?? []).some((m) => m.id === row.model && m.owned_by === props.frames.listed && m.disabled === true);
const selected = (row: FrameModel): boolean => props.frames.shown?.target === row.model;
const open = (model: string): void => {
  const kind = props.frames.shown?.kind;
  if (kind && !props.frames.opensOff) void props.frames.edit(kind, model);
};
const toggle = (row: FrameModel, event: Event): void =>
  void props.frames.setLock(props.frames.shown?.kind ?? "", row.model, (event.target as HTMLInputElement).checked);
const BARS = [0, 1, 2, 3, 4];
</script>

<template>
  <section class="fm" aria-labelledby="mod-title" data-testid="frame-models">
    <div class="fm__head"><h3 id="mod-title" class="fm__h3">Models of this kind</h3></div>

    <ul v-if="reading" class="fm__skel" aria-busy="true" aria-label="Loading the models">
      <li v-for="i in BARS" :key="i"><span class="fm__bar fm__bar--a" /><span class="fm__bar fm__bar--b" /></li>
    </ul>
    <p v-else-if="!models.length" class="fm__empty">No models</p>

    <ul v-else-if="isPhone" class="fm__list">
      <li v-for="row in models" :key="row.model" :class="{ 'is-selected': selected(row) }">
        <div class="fm__line">
          <span class="fm__pill" :class="{ 'is-own': row.source === 'own' }">{{ row.source }}</span>
          <span class="fm__ver num">{{ versionTag(row.effective) }}</span>
        </div>
        <div class="fm__line">
          <button
            type="button"
            class="fm__id"
            :aria-label="`Edit frame for ${row.model}`"
            :disabled="frames.opensOff"
            @click="open(row.model)"
          >
            {{ row.model }}
          </button>
          <span v-if="paused(row)" class="fm__pill fm__paused">paused</span>
        </div>
        <span v-if="nameOf(row)" class="fm__name">{{ nameOf(row) }}</span>
        <label class="fm__keep">
          <input
            type="checkbox"
            :checked="row.locked"
            :disabled="frames.controlsOff"
            :aria-label="`Keep own frame of ${row.model}`"
            @change="toggle(row, $event)"
          />Keep own
        </label>
        <span v-if="lockSentence(row.lock)" class="fm__lock">{{ lockSentence(row.lock) }}</span>
      </li>
    </ul>

    <ArrowScroller v-else label="Models" table>
      <table class="fm__table">
        <thead>
          <tr>
            <th scope="col" class="fm__first">Model</th>
            <th scope="col">Source</th>
            <th scope="col">Version</th>
            <th scope="col" class="fm__last">Keep own</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in models" :key="row.model" :class="{ 'is-selected': selected(row) }">
            <td class="fm__first">
              <button
                type="button"
                class="fm__id"
                :aria-label="`Edit frame for ${row.model}`"
                :disabled="frames.opensOff"
                @click="open(row.model)"
              >
                {{ row.model }}
              </button>
              <span v-if="paused(row)" class="fm__pill fm__paused">paused</span>
              <span v-if="nameOf(row)" class="fm__name">{{ nameOf(row) }}</span>
            </td>
            <td>
              <span class="fm__pill" :class="{ 'is-own': row.source === 'own' }">{{ row.source }}</span>
            </td>
            <td class="fm__mono num">{{ versionTag(row.effective) }}</td>
            <td class="fm__last">
              <label class="fm__keep" :title="lockSentence(row.lock) || undefined">
                <span v-if="isTablet && lockDay(row.lock)" class="fm__lockday num">{{ lockDay(row.lock) }}</span>
                <input
                  type="checkbox"
                  :checked="row.locked"
                  :disabled="frames.controlsOff"
                  :aria-label="`Keep own frame of ${row.model}`"
                  @change="toggle(row, $event)"
                />
              </label>
            </td>
          </tr>
        </tbody>
      </table>
    </ArrowScroller>
  </section>
</template>

<style scoped>
.fm {
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.fm__head {
  display: flex;
  align-items: center;
  padding: 14px 20px;
  border-bottom: 1px solid var(--rl-rule);
}
.fm__h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 700;
}
.fm__empty {
  margin: 0;
  padding: 18px 20px;
  font-size: 13px;
  color: var(--rl-muted);
}
.fm__table {
  width: 100%;
  min-width: 420px;
  border-collapse: collapse;
}
.fm__table thead tr {
  background: var(--rl-head);
  color: var(--rl-faint);
  font-size: 12px;
  text-align: left;
}
.fm__table th {
  padding: 8px;
  font-weight: 600;
  white-space: nowrap;
}
.fm__table td {
  padding: 10px 8px;
  vertical-align: middle;
}
.fm__table tbody tr + tr,
.fm__table tbody tr:first-child {
  border-top: 1px solid var(--rl-rule);
}
.fm__table tr.is-selected {
  background: var(--rl-head);
}
.fm__table .fm__first {
  padding-left: 20px;
}
.fm__table .fm__last {
  padding-right: 20px;
  text-align: right;
}
.fm__id {
  padding: 0;
  border: 0;
  background: none;
  color: var(--rl-fg);
  font-family: var(--rl-mono);
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
}
.fm__id:hover:not(:disabled) {
  text-decoration: underline;
}
.fm__id:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
  border-radius: 4px;
}
.fm__id:disabled {
  cursor: default;
}
.fm__name {
  display: block;
  font-size: 12px;
  color: var(--rl-faint);
}
.fm__mono {
  font-family: var(--rl-mono);
  font-size: 12px;
}
.fm__pill {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.fm__pill.is-own {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.fm__paused {
  margin-left: 8px;
}
.fm__keep {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  cursor: pointer;
}
.fm__keep input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: var(--rl-primary);
}
.fm__lockday {
  font-size: 13px;
  font-weight: 700;
  color: var(--rl-fg);
}

.fm__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.fm__list li {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 44px;
  padding: 14px 16px;
}
.fm__list li + li {
  border-top: 1px solid var(--rl-rule);
}
.fm__list li.is-selected {
  background: var(--rl-head);
}
.fm__line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.fm__line .fm__ver {
  margin-left: auto;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-muted);
}
.fm__list .fm__id {
  min-height: 24px;
  font-size: 13px;
  font-weight: 700;
}
.fm__list .fm__keep {
  justify-content: flex-start;
  min-height: 44px;
  font-size: 13px;
}
.fm__lock {
  font-size: 12px;
  color: var(--rl-faint);
}

.fm__skel {
  margin: 0;
  padding: 0;
  list-style: none;
}
.fm__skel li {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 18px 20px;
  border-top: 1px solid var(--rl-rule);
}
.fm__bar {
  display: block;
  height: 12px;
  border-radius: 6px;
  background: var(--rl-skel);
}
.fm__bar--a {
  width: 160px;
}
.fm__bar--b {
  width: 60px;
}

@media (min-width: 768px) and (max-width: 1279px) {
  .fm__table {
    min-width: 560px;
  }
  .fm__table td {
    padding: 11px 8px;
  }
  .fm__keep {
    min-height: 40px;
  }
}
</style>
