<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import HintTip from "@/components/shared/HintTip.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { IconAlertCircle } from "@/components/shared/icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { providerClass } from "@/lib/providers";
import {
  appliedLine,
  DEFAULT_TARGET,
  followerCount,
  kindTitle,
  markerChecks,
  markerParts,
  PROMPT_MARKER,
  pickOut,
  SAMPLE_TRACKING,
  TRACKING_MARKER,
  versionTag,
} from "./frames-model";
import type { FramesController } from "./use-frames";

// The editor of one frame, inline (Frames.dc.html): the card of the open kind and target. Head: the provider chip and
// the kind, "Frame for" (which model's frame, or the default), and "unsaved" while the draft differs from the stored
// text. Body: the rules, the Insert buttons, the frame text with its two markers picked out, the live check of those
// markers, the sample prompt and tracking code, and the preview (what the provider receives, read from the server as the
// draft is typed). Foot: Save, Follow the default, Apply to all models of this kind (the default's), Preview, and for
// a default that differs from the shipped one, Reset. A phone moves Follow and Save into a bar fixed above the tab bar.
// While a load or a write is in flight the draft is read-only and every control is off (the controller decides).
const props = defineProps<{ frames: FramesController }>();
const { isPhone, isTablet } = useBreakpoint();
const stacked = computed(() => isPhone.value || isTablet.value);

const kindRow = computed(() => props.frames.kindOf(props.frames.shown?.kind ?? ""));
const target = computed(() => props.frames.shown?.target ?? "");
const isDefault = computed(() => target.value === DEFAULT_TARGET);
const own = computed(() => props.frames.shownModel?.source === "own");
const kindKey = computed(() => props.frames.shown?.kind ?? "");
const kindName = computed(() => kindTitle(props.frames.shown?.kind ?? ""));

// "Default v6 is followed by 2 of 3 models"
const helpLine = computed(() => {
  const row = kindRow.value;
  if (!row) return "";
  if (isPhone.value) return `default ${versionTag(row.default)}`;
  const text = `Default ${versionTag(row.default)} is followed by ${followerCount(row)} of ${row.models.length} models`;
  return isTablet.value ? text.charAt(0).toLowerCase() + text.slice(1) : text;
});

// The "Frame for" list: the default, then each model with where its text comes from.
const targets = computed(() => {
  const row = kindRow.value;
  if (!row) return [];
  return [
    { value: DEFAULT_TARGET, label: `Default · all ${row.kind} models` },
    ...row.models.map((m) => ({
      value: m.model,
      label: m.source === "own" ? `${m.model} · own frame ${versionTag(m.own)}` : `${m.model} · follows default`,
    })),
  ];
});
const pickTarget = (value: string | number): void => {
  const kind = props.frames.shown?.kind;
  if (kind) void props.frames.edit(kind, String(value));
};

const checks = computed(() => markerChecks(props.frames.draft));
const problem = computed(() => {
  const c = checks.value;
  const parts: string[] = [];
  if (c.prompt !== 1) parts.push(`${PROMPT_MARKER} is in ${c.prompt} times`);
  if (c.tracking !== 1) parts.push(`${TRACKING_MARKER} is in ${c.tracking} times`);
  return parts.join(", ");
});
const frameParts = computed(() => markerParts(props.frames.draft));
const verbatim = computed(() => props.frames.data?.verbatim === true);
const previewParts = computed(() =>
  pickOut(props.frames.preview?.text ?? "", [props.frames.previewPrompt.trim(), verbatim.value ? "" : SAMPLE_TRACKING]),
);
// the line under the box: a phone and a tablet say it shorter, as drawn
const countText = computed(() => {
  const n = `${props.frames.draft.length.toLocaleString("en-US")} / 1,000`;
  return isPhone.value ? n : `${n} characters`;
});
const okText = computed(() => (isPhone.value ? "both markers once each" : "both markers present, once each"));
const rulesText = computed(() =>
  isPhone.value ? "One line, at most 1,000 characters, with" : "One line, at most 1,000 characters. It must contain",
);

// ---- the text box: a textarea laid over a mirror of its own text, which picks the markers out ------------------------
const text = ref<HTMLTextAreaElement | null>(null);
function typed(event: Event): void {
  const el = event.target as HTMLTextAreaElement;
  // one line: a pasted line break becomes a space
  const clean = el.value.replace(/[\r\n]+/g, " ");
  if (clean !== el.value) el.value = clean;
  props.frames.setDraft(clean);
}
async function insert(marker: string): Promise<void> {
  const el = text.value;
  const at = el?.selectionStart ?? props.frames.draft.length;
  const end = el?.selectionEnd ?? at;
  props.frames.setDraft(props.frames.draft.slice(0, at) + marker + props.frames.draft.slice(end));
  await nextTick();
  el?.focus();
  el?.setSelectionRange(at + marker.length, at + marker.length);
}

// ---- the preview follows the draft ------------------------------------------------------------------------------------
// A frame that arrives (or a write that ends) is previewed at once; typing waits for a pause.
let timer: ReturnType<typeof setTimeout> | undefined;
const previewIn = (ms: number): void => {
  clearTimeout(timer);
  if (props.frames.shown && !props.frames.controlsOff) timer = setTimeout(() => void props.frames.previewQuiet(), ms);
};
// (a frame that arrives changes the draft too: `fresh` keeps that change from waiting)
let fresh = false;
watch(
  () => [props.frames.shown, props.frames.controlsOff],
  () => {
    fresh = true;
    queueMicrotask(() => (fresh = false));
    previewIn(0);
  },
  { immediate: true },
);
watch(
  () => [props.frames.draft, props.frames.previewPrompt],
  () => previewIn(fresh ? 0 : 350),
);
onBeforeUnmount(() => clearTimeout(timer));

const sampleOff = computed(() => props.frames.controlsOff);
const canReset = computed(() => isDefault.value && kindRow.value?.shipped_differs === true);
const followWhy = computed(() => (own.value ? "" : "Works on a model that has its own frame"));
</script>

<template>
  <section
    class="fed"
    :class="[providerClass(frames.listed), { 'is-stacked': stacked, 'is-phone': isPhone }]"
    aria-labelledby="ed-title"
    :data-frame-kind="frames.shown?.kind"
    data-testid="frame-editor"
  >
    <template v-if="frames.shown && frames.info">
      <div class="fed__head">
        <div class="fed__title">
          <h2 id="ed-title" class="fed__h2">
            <span class="fed__chip">{{ frames.listed }}</span>
            {{ kindName }}
          </h2>
          <span class="fed__help">{{ helpLine }}</span>
        </div>
        <FilterSelect
          v-if="!stacked"
          :model-value="target"
          :options="targets"
          label="Frame for"
          size="header"
          @update:model-value="pickTarget"
        />
        <span v-if="frames.dirty" class="fed__pill is-warn" data-testid="frame-unsaved">unsaved</span>
        <span v-if="canReset && !stacked" class="fed__pill is-warn">differs from the shipped default</span>
      </div>

      <div v-if="stacked" class="fed__stack-field">
        <FilterSelect
          :model-value="target"
          :options="targets"
          label="Frame for"
          variant="field"
          block
          @update:model-value="pickTarget"
        />
      </div>

      <div class="fed__body">
        <p class="fed__rules">
          {{ rulesText }} <code class="fed__mk">{{ PROMPT_MARKER }}</code> and
          <code class="fed__mk">{{ TRACKING_MARKER }}</code>
          {{ isPhone ? "once each." : "exactly once each." }}
        </p>
        <p v-if="verbatim" class="fed__rules" data-testid="frame-verbatim">
          {{ frames.listed }} sends the text as written: <code class="fed__mk">{{ TRACKING_MARKER }}</code> adds nothing
          and the prompt keeps its line breaks.
        </p>
        <p v-if="frames.shownModel && !own" class="fed__rules">
          This model follows the default. Saving gives it its own frame; until then a change to the default reaches it.
        </p>
        <div class="fed__insert">
          <span class="fed__help">Insert</span>
          <button
            v-for="m in [PROMPT_MARKER, TRACKING_MARKER]"
            :key="m"
            type="button"
            class="fed__ins"
            :aria-label="`Insert ${m}`"
            :disabled="frames.controlsOff"
            @click="insert(m)"
          >
            {{ m }}
          </button>
        </div>

        <div class="fed__box" :class="{ 'is-invalid': frames.refusal, 'is-off': frames.controlsOff }">
          <div class="fed__mirror" aria-hidden="true">
            <template v-for="(part, i) in frameParts" :key="i"
              ><span v-if="part.marker" class="fed__ph">{{ part.text }}</span
              ><template v-else>{{ part.text }}</template></template
            >&#8203;
          </div>
          <textarea
            id="frame-text"
            ref="text"
            class="fed__ta"
            aria-label="Frame text"
            spellcheck="false"
            :value="frames.draft"
            :placeholder="`${PROMPT_MARKER} ... ${TRACKING_MARKER}`"
            :readonly="frames.controlsOff"
            :aria-invalid="frames.refusal ? 'true' : undefined"
            :aria-describedby="frames.refusal ? 'frame-refusal' : undefined"
            @keydown.enter.prevent
            @input="typed"
          />
        </div>
        <span v-if="frames.refusal" id="frame-refusal" class="fed__alert" role="alert"
          ><IconAlertCircle :size="14" />{{ frames.refusal }}</span
        >
        <div class="fed__checks">
          <span class="fed__help num">{{ countText }}</span>
          <span v-if="checks.ok" class="fed__ok" data-testid="frame-check"
            ><svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.4"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M20 6 9 17l-5-5" /></svg
            ><template v-if="stacked">{{ okText }}</template
            ><template v-else
              ><span class="mono">{{ PROMPT_MARKER }}</span> and <span class="mono">{{ TRACKING_MARKER }}</span> once
              each</template
            ></span
          >
          <span v-else class="fed__bad" data-testid="frame-check">{{ problem }}</span>
          <span v-if="!stacked" class="fed__help">One line · line breaks are not allowed</span>
        </div>
        <p v-if="frames.warnings.length" class="fed__note" role="status" data-testid="frame-warnings">
          {{ frames.warnings.join(" ") }}
        </p>
        <p v-if="frames.applied" class="fed__note" role="status" data-testid="frame-applied">
          {{ appliedLine(frames.applied) }}
        </p>
      </div>

      <div class="fed__sample">
        <label class="fed__field fed__field--grow">
          <span class="fed__label">Sample prompt</span>
          <input
            id="frame-sample"
            type="text"
            class="fed__input"
            :value="frames.previewPrompt"
            :disabled="sampleOff"
            @input="frames.setPreviewPrompt(($event.target as HTMLInputElement).value)"
          />
        </label>
        <label v-if="!stacked" class="fed__field fed__field--track">
          <span class="fed__label">Sample tracking code</span>
          <input class="fed__input mono" readonly :value="verbatim ? '' : SAMPLE_TRACKING" />
        </label>
      </div>
      <div class="fed__prev">
        <div class="fed__prev-head">
          <span class="fed__label">Preview</span>
          <span class="fed__help num"
            ><HintTip
              v-if="frames.preview"
              :text="`Shown with the sample tracking code (${frames.preview.source} frame).`"
              >{{ frames.preview.length }} characters</HintTip
            ><template v-if="frames.preview"> · </template>what {{ frames.listed }} receives</span
          >
        </div>
        <output v-if="frames.preview" class="fed__out" data-testid="frame-preview"
          ><span v-for="(part, i) in previewParts" :key="i" :class="{ fed__fill: part.hit }">{{
            part.text
          }}</span></output
        >
        <output v-else class="fed__out is-empty" data-testid="frame-preview-empty"
          >The preview appears when the frame follows its rules.</output
        >
      </div>

      <div v-if="isPhone" class="fed__phone-actions">
        <button
          type="button"
          class="fed__btn fed__btn--wide"
          :disabled="frames.controlsOff || !isDefault"
          :title="isDefault ? undefined : 'Works on the default frame'"
          @click="frames.applyAll"
        >
          Apply to all models of this kind
        </button>
        <IconButton icon="preview" label="Preview" :disabled="frames.controlsOff" @click="frames.previewNow" />
        <IconButton
          v-if="canReset"
          icon="reset"
          label="Reset to the shipped default"
          :disabled="frames.controlsOff"
          @click="frames.resetShipped(kindKey)"
        />
      </div>
      <div v-if="isPhone" class="fed__bar">
        <button
          type="button"
          class="fed__btn fed__btn--half"
          :disabled="frames.controlsOff || !own"
          @click="frames.follow"
        >
          Follow the default
        </button>
        <button type="button" class="fed__primary fed__btn--half" :disabled="!frames.canSave" @click="frames.save">
          Save
        </button>
      </div>
      <div v-else class="fed__foot">
        <button type="button" class="fed__primary" :disabled="!frames.canSave" @click="frames.save">Save</button>
        <button
          type="button"
          class="fed__btn"
          :disabled="frames.controlsOff || !own"
          :title="followWhy || undefined"
          @click="frames.follow"
        >
          Follow the default
        </button>
        <button
          type="button"
          class="fed__btn"
          :disabled="frames.controlsOff || !isDefault"
          :title="isDefault ? undefined : 'Works on the default frame'"
          @click="frames.applyAll"
        >
          Apply to all models of this kind
        </button>
        <IconButton icon="preview" label="Preview" :disabled="frames.controlsOff" @click="frames.previewNow" />
        <IconButton
          v-if="canReset"
          icon="reset"
          label="Reset to the shipped default"
          :disabled="frames.controlsOff"
          @click="frames.resetShipped(kindKey)"
        />
        <span v-if="!stacked" class="fed__foot-note">Save applies to jobs created from now on.</span>
      </div>
    </template>
    <p v-else class="fed__missing" role="status" data-testid="frame-missing">
      {{ frames.loading ? "Loading the frame..." : "This frame is not listed any more. Pick another." }}
    </p>
  </section>
</template>

<style scoped>
.fed {
  --rl-marker-ring: #d9ccff;
  --rl-marker-dash: #c9b8ff;
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
html.dark .fed {
  --rl-marker-ring: #4a3a80;
  --rl-marker-dash: #4a3a80;
}
.fed__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--rl-rule);
}
.fed__title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-right: auto;
  min-width: 0;
}
.fed__h2 {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 16px;
  font-weight: 700;
}
.fed__chip {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  background: var(--ps);
  color: var(--pi);
}
.fed__help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.fed__pill {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.fed__pill.is-warn {
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
}
.fed__body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 20px 0;
}
.fed__rules {
  margin: 0;
  font-size: 13px;
  line-height: 22px;
  color: var(--rl-body);
}
.fed__mk {
  padding: 1px 4px;
  border-radius: 5px;
  font-family: var(--rl-mono);
  font-size: 12px;
  font-weight: 500;
  background: var(--rl-info-bg);
  color: var(--rl-info);
  box-shadow: inset 0 0 0 1px var(--rl-marker-ring);
}
.fed__insert {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.fed__ins {
  height: 28px;
  padding: 0 10px;
  border: 1px dashed var(--rl-marker-dash);
  border-radius: 8px;
  background: transparent;
  color: var(--rl-brand-ink);
  font-family: var(--rl-mono);
  font-size: 12px;
  cursor: pointer;
}
.fed__ins:hover:not(:disabled) {
  background: var(--rl-primary-soft);
}
.fed__ins:disabled {
  opacity: 0.5;
  cursor: default;
}

/* the box: the mirror sets its height, the textarea lies over it with the same box and font */
.fed__box {
  position: relative;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-code);
}
.fed__box:focus-within {
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.fed__box.is-invalid {
  border-color: var(--rl-bad);
}
.fed__mirror,
.fed__ta {
  box-sizing: border-box;
  margin: 0;
  padding: 12px 14px;
  font-family: var(--rl-mono);
  font-size: 13px;
  line-height: 24px;
  letter-spacing: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.fed__mirror {
  min-height: 50px;
  color: var(--rl-fg);
  pointer-events: none;
}
.fed__box.is-off .fed__mirror {
  color: var(--rl-muted);
}
.fed__ph {
  border-radius: 4px;
  background: var(--rl-info-bg);
  color: var(--rl-info);
  box-shadow: 0 0 0 1px var(--rl-marker-ring);
}
.fed__ta {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
  outline: 0;
  resize: none;
  overflow: hidden;
  background: transparent;
  color: transparent;
  caret-color: var(--rl-brand);
}
.fed__ta::placeholder {
  color: var(--rl-faint);
}
.fed__alert {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--rl-bad);
  font-size: 12px;
  line-height: 16px;
}
.fed__checks {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 20px;
}
.fed__ok {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 700;
  color: var(--rl-ok);
}
.fed__bad {
  font-size: 12px;
  font-weight: 700;
  color: var(--rl-bad);
  font-family: var(--rl-mono);
}
.fed__note {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-muted);
}

.fed__sample {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 16px 20px;
}
.fed__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.fed__field--grow {
  flex: 1 1 320px;
  min-width: 0;
}
.fed__field--track {
  flex: 0 1 200px;
}
.fed__label {
  font-size: 13px;
  font-weight: 700;
}
.fed__input {
  box-sizing: border-box;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
}
.fed__input:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 1px;
}
.fed__prev {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0 20px 18px;
}
.fed__prev-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8px;
}
.fed__out {
  display: block;
  padding: 12px 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-code);
  color: var(--rl-body);
  font-family: var(--rl-mono);
  font-size: 13px;
  line-height: 22px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.fed__out.is-empty {
  color: var(--rl-faint);
  font-family: inherit;
  font-size: 13px;
}
.fed__fill {
  padding: 1px 2px;
  border-radius: 3px;
  background: var(--ps);
  color: var(--pi);
  font-weight: 500;
}

.fed__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid var(--rl-rule);
  background: var(--rl-head);
}
.fed__foot-note {
  margin-left: auto;
  font-size: 12px;
  color: var(--rl-faint);
}
.fed__btn,
.fed__primary {
  box-sizing: border-box;
  height: 36px;
  border-radius: 10px;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.fed__btn {
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  background: var(--rl-surface);
  color: var(--rl-fg);
}
.fed__btn:hover:not(:disabled) {
  background: var(--rl-raised);
}
.fed__btn:disabled {
  border-color: var(--rl-line-strong);
  background: var(--rl-head);
  color: var(--rl-faint);
  cursor: not-allowed;
}
.fed__primary {
  padding: 0 18px;
  border: 0;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font-weight: 700;
}
.fed__primary:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.fed__primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.fed__btn:focus-visible,
.fed__primary:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.fed__missing {
  margin: 0;
  padding: 24px 20px;
  font-size: 13px;
  color: var(--rl-muted);
}

/* tablet and phone: the card keeps its head on the page's side padding and stacks the blocks at 12 px */
.fed.is-stacked {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 0;
}
.fed.is-stacked .fed__head {
  align-items: baseline;
  gap: 4px 10px;
  padding: 0 24px;
  border: 0;
}
.fed.is-stacked .fed__title {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 10px;
  margin-right: auto;
}
.fed.is-stacked .fed__h2 {
  font-size: 15px;
}
.fed.is-stacked .fed__stack-field,
.fed.is-stacked .fed__body,
.fed.is-stacked .fed__sample,
.fed.is-stacked .fed__prev,
.fed.is-stacked .fed__foot {
  padding-right: 24px;
  padding-left: 24px;
}
.fed.is-stacked .fed__body {
  padding-top: 0;
  gap: 12px;
}
.fed.is-stacked .fed__sample {
  padding-top: 0;
  padding-bottom: 0;
}
.fed.is-stacked .fed__prev {
  padding-bottom: 0;
}
.fed.is-stacked .fed__foot {
  padding-top: 0;
  padding-bottom: 0;
  border: 0;
  background: none;
}
.fed.is-phone .fed__head,
.fed.is-phone .fed__stack-field,
.fed.is-phone .fed__body,
.fed.is-phone .fed__sample,
.fed.is-phone .fed__prev,
.fed.is-phone .fed__phone-actions {
  padding-right: 16px;
  padding-left: 16px;
}
.fed.is-phone .fed__input {
  height: 44px;
}
.fed__phone-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.fed__btn--wide {
  flex: 1 1 0;
  min-width: 0;
  height: 44px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* the phone's bar: Follow and Save, fixed above the tab bar */
.fed__bar {
  position: fixed;
  right: 0;
  bottom: var(--rl-bottom-bar, 0px);
  left: 0;
  z-index: 5;
  display: flex;
  gap: 8px;
  box-sizing: border-box;
  padding: 12px 16px;
  border-top: 1px solid var(--rl-rule);
  background: var(--rl-surface);
}
.fed__btn--half {
  flex: 1 1 0;
  min-width: 0;
  height: 44px;
  padding: 0 12px;
}
</style>
