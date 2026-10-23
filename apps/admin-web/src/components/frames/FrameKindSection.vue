<script setup lang="ts">
import { computed } from "vue";
import type { FrameKind, FrameModel } from "@/api/frames";
import ProviderTile from "@/components/shared/ProviderTile.vue";
import StatusChip from "@/components/shared/StatusChip.vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { providerClass } from "@/lib/providers";
import { useScopeStore } from "@/stores/scope";
import { DEFAULT_TARGET, kindTitle, lockLine, markerParts, versionTag } from "./frames-model";
import type { FramesController } from "./use-frames";

// One kind (image, video) of the provider: its default frame, written out with its two markers picked out, and a table
// of its models, each with its name, whether it has its own frame (then that text too) or follows the default, and
// paused when the admin switched it off. The default can be edited, reset to the shipped text when it differs from it,
// and applied to every model that is not kept on its own frame. From 768 px the Source column folds under the model
// id; below that the table becomes a list. A click anywhere on a model's row (or its phone block) opens its editor,
// except on Keep own; the model id is the button that does it from the keyboard.
const props = defineProps<{ frames: FramesController; kind: FrameKind }>();
const { isPhone, isTablet } = useBreakpoint();
const scope = useScopeStore();

// the default in force: its stored text, else the shipped one
const defaultText = computed(() => props.kind.default?.text ?? props.kind.shipped ?? "");
const ownText = (row: FrameModel): string => (row.source === "own" ? (row.own?.text ?? "") : "");
const nameOf = (row: FrameModel): string => (row.name && row.name !== row.model ? row.name : "");
const paused = (row: FrameModel): boolean =>
  (scope.models ?? []).some((m) => m.id === row.model && m.owned_by === props.frames.listed && m.disabled === true);

const selected = (frames: FramesController, kind: FrameKind, model: FrameModel): boolean =>
  frames.shown?.kind === kind.kind && frames.shown.target === model.model;
const open = (model: string): void => {
  if (!props.frames.opensOff) props.frames.edit(props.kind.kind, model);
};
// a click on a control inside the row is that control's, not the row's
const openRow = (model: string, event: MouseEvent): void => {
  if (!(event.target as HTMLElement | null)?.closest("button, a, input, label")) open(model);
};
</script>

<template>
  <section
    class="kind"
    :class="[providerClass(frames.listed), { 'is-tablet': isTablet, 'is-phone': isPhone }]"
    :data-frame-kind="kind.kind"
    :aria-labelledby="`kind-${kind.kind}`"
  >
    <div class="kind__head">
      <div class="kind__title">
        <ProviderTile :provider="frames.listed" class="kind__tile" />
        <h2 :id="`kind-${kind.kind}`">{{ kindTitle(kind.kind) }}</h2>
        <span class="kind__default"
          >Default: <span class="kind__mono">{{ versionTag(kind.default) }}</span></span
        >
        <template v-if="kind.shipped_differs && !isPhone">
          <span class="kind__warn"><i class="dot" aria-hidden="true" />differs from the shipped default</span>
          <button
            type="button"
            class="kind__link"
            :disabled="frames.controlsOff"
            @click="frames.resetShipped(kind.kind)"
          >
            Reset to the shipped default
          </button>
        </template>
      </div>
      <div v-if="kind.shipped_differs && isPhone" class="kind__title kind__title--row2">
        <span class="kind__warn"><i class="dot" aria-hidden="true" />differs from the shipped default</span>
        <button type="button" class="kind__link" :disabled="frames.controlsOff" @click="frames.resetShipped(kind.kind)">
          Reset to the shipped default
        </button>
      </div>
      <button
        type="button"
        class="kind__edit-default"
        :disabled="frames.opensOff"
        @click="frames.edit(kind.kind, DEFAULT_TARGET)"
      >
        Edit the default
      </button>
    </div>
    <!-- the default frame as it reads, its two markers picked out -->
    <div class="kind__frame-wrap">
      <pre class="kind__frame" data-testid="frame-default-text"><template
          v-for="(part, i) in markerParts(defaultText)"
          :key="i"
          ><em v-if="part.marker">{{ part.text }}</em><template v-else>{{ part.text }}</template></template
        ></pre>
    </div>

    <p v-if="!kind.models.length" class="kind__empty">No models</p>

    <ul v-else-if="isPhone" class="kind__list">
      <li
        v-for="row in kind.models"
        :key="row.model"
        :class="{ 'is-selected': selected(frames, kind, row), 'is-open-off': frames.opensOff }"
        @click="openRow(row.model, $event)"
      >
        <span v-if="nameOf(row)" class="kind__name">{{ nameOf(row) }}</span>
        <div class="kind__line">
          <button
            type="button"
            class="kind__model kind__open"
            :aria-label="`Edit frame for ${row.model}`"
            :disabled="frames.opensOff"
            @click="open(row.model)"
          >
            {{ row.model }}
          </button>
          <span v-if="paused(row)" class="kind__paused">paused</span>
          <StatusChip
            :status="row.source"
            :label="row.source"
            :tone="row.source === 'own' ? 'info' : 'neutral'"
            class="pill--phone"
          />
        </div>
        <div class="kind__line">
          <span class="kind__effective"
            >effective <span class="kind__mono">{{ versionTag(row.effective) }}</span></span
          >
          <label class="kind__keep kind__keep--phone">
            <input
              type="checkbox"
              :checked="row.locked"
              :disabled="frames.controlsOff"
              :aria-label="`Keep own frame of ${row.model}`"
              @change="frames.setLock(kind.kind, row.model, ($event.target as HTMLInputElement).checked)"
            />Keep own
          </label>
        </div>
        <span v-if="lockLine(row.lock)" class="kind__lock kind__lock--phone">{{ lockLine(row.lock) }}</span>
        <span v-if="ownText(row)" class="kind__own"
          ><template v-for="(part, i) in markerParts(ownText(row))" :key="i"
            ><em v-if="part.marker">{{ part.text }}</em
            ><template v-else>{{ part.text }}</template></template
          ></span
        >
      </li>
    </ul>

    <div v-else class="kind__scroll">
      <table class="kind__table">
        <thead>
          <tr>
            <th scope="col" class="kind__first">Model</th>
            <th v-if="!isTablet" scope="col">Source</th>
            <th scope="col" class="kind__right">Effective version</th>
            <th scope="col" class="kind__last">Keep own</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in kind.models"
            :key="row.model"
            :class="{ 'is-selected': selected(frames, kind, row), 'is-open-off': frames.opensOff }"
            @click="openRow(row.model, $event)"
          >
            <td class="kind__first kind__model">
              <span v-if="nameOf(row)" class="kind__name">{{ nameOf(row) }}</span>
              <button
                type="button"
                class="kind__open"
                :aria-label="`Edit frame for ${row.model}`"
                :disabled="frames.opensOff"
                @click="open(row.model)"
              >
                {{ row.model }}
              </button>
              <span v-if="paused(row)" class="kind__paused">paused</span>
              <span
                v-if="isTablet"
                class="pill pill--tablet"
                :class="row.source === 'own' ? 'pill--info' : 'pill--neutral'"
                ><i class="dot" aria-hidden="true" />{{ row.source }}</span
              >
              <span v-if="ownText(row)" class="kind__own"
                ><template v-for="(part, i) in markerParts(ownText(row))" :key="i"
                  ><em v-if="part.marker">{{ part.text }}</em
                  ><template v-else>{{ part.text }}</template></template
                ></span
              >
            </td>
            <td v-if="!isTablet">
              <StatusChip :status="row.source" :label="row.source" :tone="row.source === 'own' ? 'info' : 'neutral'" />
            </td>
            <td class="num kind__right kind__mono kind__effective-cell">{{ versionTag(row.effective) }}</td>
            <td class="kind__keep-cell kind__last">
              <label class="kind__keep">
                <input
                  type="checkbox"
                  :checked="row.locked"
                  :disabled="frames.controlsOff"
                  :aria-label="`Keep own frame of ${row.model}`"
                  @change="frames.setLock(kind.kind, row.model, ($event.target as HTMLInputElement).checked)"
                />Keep own
              </label>
              <span v-if="lockLine(row.lock)" class="kind__lock">{{ lockLine(row.lock) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.kind {
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  overflow: hidden;
}
.kind__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 16px;
  padding: 16px 16px 14px;
}
.kind__title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.kind h2 {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
.kind__default {
  font-size: 13px;
  color: var(--rl-muted);
}
/* the provider's tile, large, and its tint fading across the head and the default frame */
.kind__head,
.kind__frame-wrap {
  background: linear-gradient(90deg, var(--ps), var(--rl-surface) 70%);
}
.kind__title .kind__tile {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  font-size: 14px;
}
.kind__frame-wrap {
  padding: 0 16px 16px;
}
.kind__frame {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid var(--rl-line);
  border-radius: 10px;
  background: var(--rl-surface);
  font: 12px/1.6 var(--rl-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.kind__frame em,
.kind__own em {
  padding: 0 3px;
  border-radius: 4px;
  background: var(--ps);
  color: var(--pi);
  font-style: normal;
  font-weight: 600;
}
.kind__name {
  display: block;
  font-family: var(--rl-font);
  font-size: 13px;
  font-weight: 600;
  color: var(--rl-fg);
}
.kind__own {
  display: block;
  margin-top: 4px;
  font-family: var(--rl-mono);
  font-size: 12px;
  line-height: 1.5;
  color: var(--rl-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.kind__paused {
  margin-left: 8px;
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-family: var(--rl-font);
  font-size: 11px;
  font-weight: 600;
}
.kind__mono {
  font-family: var(--rl-mono);
  font-variant-numeric: tabular-nums;
}
.kind__default .kind__mono {
  color: var(--rl-fg);
}
.kind__warn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border-radius: 12px;
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-size: 11px;
  font-weight: 500;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: currentcolor;
}
.kind__link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--rl-link, var(--rl-primary));
  font: inherit;
  font-size: 13px;
  text-decoration: underline;
  text-underline-offset: 3px;
  cursor: pointer;
}
.kind__link:hover:not(:disabled) {
  color: var(--rl-primary-hover);
}
.kind__link:disabled {
  opacity: 0.6;
  cursor: default;
}
.kind__edit-default {
  box-sizing: border-box;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  cursor: pointer;
}
.kind__edit-default {
  height: 34px;
  padding: 0 12px;
  background: var(--rl-surface);
}
.kind__edit-default:hover:not(:disabled) {
  background: var(--rl-raised);
}
.kind__edit-default:disabled {
  cursor: default;
  color: var(--rl-faint);
}
.kind__empty {
  margin: 0;
  padding: 14px 16px;
  border-top: 1px solid var(--rl-line);
  font-size: 13px;
  color: var(--rl-muted);
}

.kind__scroll {
  overflow-x: auto;
}
.kind__table {
  width: 100%;
  min-width: 560px;
  border-collapse: separate;
  border-spacing: 0;
}
.kind__table th {
  padding: 0 12px;
  height: 38px;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  color: var(--rl-muted);
  background: var(--rl-raised);
}
.kind__table .kind__right {
  text-align: right;
}
.kind__table td {
  padding: 0 12px;
  border-bottom: 1px solid var(--rl-line);
  background: transparent;
}
.kind__table tbody tr:hover td {
  background: var(--rl-raised);
}
.kind__table tbody tr.is-selected td {
  background: var(--rl-primary-soft);
}
.kind__table tbody tr.is-selected:hover td {
  background: var(--rl-raised);
}
.kind__table .kind__first {
  padding: 8px 12px 8px 16px;
}
.kind__table th.kind__first {
  padding: 0 12px 0 16px;
}
.kind__table td.kind__first {
  height: 56px;
  box-sizing: border-box;
}
.kind__table .kind__last {
  padding-right: 16px;
}
/* a model's row opens its editor: it reads as clickable while it can */
.kind__table tbody tr,
.kind__list li {
  cursor: pointer;
}
.kind__table tbody tr.is-open-off,
.kind__list li.is-open-off {
  cursor: default;
}
/* the model id is the row's opener for the keyboard: it looks like the id, not like a button */
.kind__open {
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.kind__open:disabled {
  cursor: default;
}
.kind__open:focus-visible {
  outline: 2px solid var(--rl-primary);
  outline-offset: 2px;
  border-radius: 4px;
}
.kind__table td.kind__model {
  font-family: var(--rl-mono);
  font-size: 13px;
}
.kind__effective-cell {
  font-size: 13px;
}
.kind__table td.kind__keep-cell {
  padding: 8px 16px 8px 12px;
}
.kind__keep {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}
.kind__keep input {
  width: 15px;
  height: 15px;
  margin: 0;
  font-family: inherit;
  accent-color: var(--rl-primary);
}
.kind__lock {
  display: block;
  margin: 2px 0 0 23px;
  font-size: 12px;
  color: var(--rl-faint);
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.pill--info {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.pill--neutral {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}

/* tablet: Source folds under the model id; bigger touch targets */
.is-tablet .kind__table {
  min-width: 0;
}
.is-tablet .kind__table td.kind__first {
  padding: 10px 12px 10px 16px;
}
.is-tablet .kind__table td.kind__first {
  height: auto;
}
.is-tablet .kind__table td.kind__model {
  line-height: 20px;
}
.pill--tablet {
  display: flex;
  border-radius: 12px;
  font-weight: 500;
  width: fit-content;
  height: 22px;
  margin-top: 4px;
  padding: 0 8px;
  font-family: var(--rl-font);
  font-size: 11px;
}
.is-tablet .kind__keep input {
  width: 16px;
  height: 16px;
}
.is-tablet .kind__keep {
  min-height: 28px;
}
.is-tablet .kind__lock {
  margin-left: 24px;
}

/* phone: a list */
.kind__list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.kind__list li {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 16px;
  border-top: 1px solid var(--rl-line);
}
.kind__list li.is-selected {
  background: var(--rl-primary-soft);
}
.kind__line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.kind__model {
  font-family: var(--rl-mono);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.kind__list .kind__model {
  font-weight: 500;
}
.pill--phone {
  flex: none;
}
.kind__effective {
  font-size: 13px;
  color: var(--rl-muted);
}
.kind__effective .kind__mono {
  color: var(--rl-fg);
}
.kind__keep--phone {
  min-height: 44px;
  padding-left: 8px;
  gap: 10px;
  justify-content: flex-end;
}
.kind__keep--phone input {
  width: 20px;
  height: 20px;
}
.kind__lock--phone {
  margin: 0;
}

.kind__title--row2 {
  gap: 0 12px;
  width: 100%;
}
.kind__title--row2 .kind__link {
  display: flex;
  align-items: center;
  min-height: 44px;
}
.is-phone .kind__head {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
  gap: 8px;
}
.is-phone .kind__title:not(.kind__title--row2) {
  align-items: baseline;
  gap: 4px 12px;
}
.is-phone .kind__edit-default {
  width: 100%;
  height: 44px;
  padding: 0 14px;
}
</style>
