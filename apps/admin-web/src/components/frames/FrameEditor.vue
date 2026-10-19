<script setup lang="ts">
import { computed } from "vue";
import DetailDrawer from "@/components/shared/DetailDrawer.vue";
import HintTip from "@/components/shared/HintTip.vue";
import { IconAlertCircle } from "@/components/shared/icons";
import { useBreakpoint } from "@/composables/use-breakpoint";
import {
  appliedLine,
  DEFAULT_TARGET,
  lockHistoryLine,
  newestFirst,
  PROMPT_MARKER,
  shortStamp,
  TRACKING_MARKER,
  versionTag,
} from "./frames-model";
import type { FramesController } from "./use-frames";

// The editor of one frame, in a right drawer: the text, a preview of the draft and the history with Roll back in the
// body; Save / Follow the default / Apply to all / Preview in the footer. The drawer is open while `frames.open` is
// (also while the read runs, and when the read found no such frame: a reload never drops the draft). A close with an
// unsaved change asks first. Every frame text is shown as text. While a load or a write is in flight the draft is read-only and
// every control is off (the controller decides, see use-frames.ts).
const props = defineProps<{ frames: FramesController }>();
const emit = defineEmits<{ closed: [] }>();
const { isPhone } = useBreakpoint();

// The empty box shows the shape of a frame. Built from the markers, so no template holds a double brace.
const FRAME_SHAPE = `${PROMPT_MARKER} ... ${TRACKING_MARKER}`;

// The pill beside the title: where the text in force comes from, and its version.
const source = computed(() => {
  const f = props.frames;
  const own = f.shownModel?.source === "own";
  const v = own ? (f.info?.current ?? f.shownModel?.effective) : (f.info?.effective ?? f.info?.current);
  if (!v) return null;
  return { tone: own ? "info" : "neutral", text: `${own ? "own" : "default"} · ${versionTag(v)}` };
});
const heading = computed(() =>
  props.frames.shown ? `Editing ${props.frames.shown.kind} / ${props.frames.shown.target}` : "Edit frame",
);
const dirty = computed(() => props.frames.draft !== props.frames.loaded);
const currentId = computed(() => props.frames.info?.current?.id ?? null);
</script>

<template>
  <DetailDrawer
    :model-value="frames.open"
    title="Edit frame"
    :heading="heading"
    testid="frame-drawer"
    back-label="Back to Prompt frames"
    :dirty="dirty"
    :busy="frames.busy"
    @update:model-value="(o: boolean) => !o && frames.close()"
    @closed="emit('closed')"
  >
    <template v-if="source && frames.shown && frames.info" #chips>
      <span class="pill" :class="`pill--${source.tone}`"><i class="dot" aria-hidden="true" />{{ source.text }}</span>
    </template>
    <div v-if="frames.shown && frames.info" class="frame-editor">
      <p class="editor__hint">
        One line, at most 1,000 characters. It must contain <code>{{ PROMPT_MARKER }}</code> and
        <code>{{ TRACKING_MARKER }}</code> exactly once each.
      </p>
      <p v-if="frames.shownModel && frames.shownModel.source === 'default'" class="editor__hint">
        This model follows the default. Saving gives it its own frame; until then a change to the default reaches it.
      </p>

      <div class="editor__field">
        <div class="editor__label-row">
          <label for="frame-text">Frame text</label>
          <span class="editor__count">{{ frames.draft.length.toLocaleString("en-US") }} / 1,000</span>
        </div>
        <textarea
          id="frame-text"
          :value="frames.draft"
          :rows="isPhone ? 7 : 5"
          :placeholder="FRAME_SHAPE"
          :readonly="frames.controlsOff"
          class="editor__text"
          :class="{ 'is-invalid': frames.refusal }"
          :aria-invalid="frames.refusal ? 'true' : undefined"
          :aria-describedby="frames.refusal ? 'frame-refusal' : undefined"
          @input="frames.setDraft(($event.target as HTMLTextAreaElement).value)"
        />
        <!-- a refused write, drawn as the field error of B-States (12.8): under the box it is about -->
        <span v-if="frames.refusal" id="frame-refusal" class="editor__alert" role="alert"
          ><IconAlertCircle :size="14" />{{ frames.refusal }}</span
        >
      </div>
      <p v-if="frames.warnings.length" class="editor__note" role="status" data-testid="frame-warnings">
        {{ frames.warnings.join(" ") }}
      </p>
      <p v-if="frames.applied" class="editor__note" role="status" data-testid="frame-applied">
        {{ appliedLine(frames.applied) }}
      </p>

      <div class="editor__section">
        <div class="editor__field">
          <label for="frame-sample">Sample prompt</label>
          <input
            id="frame-sample"
            type="text"
            class="editor__input"
            :value="frames.previewPrompt"
            :disabled="frames.controlsOff"
            @input="frames.setPreviewPrompt(($event.target as HTMLInputElement).value)"
          />
        </div>
        <template v-if="frames.preview">
          <div class="editor__label-row">
            <span class="editor__strong">Preview</span>
            <!-- the canvas draws the count alone: what the preview was made with is its tooltip -->
            <span class="editor__count"
              ><HintTip :text="`Shown with the sample tracking code (${frames.preview.source} frame).`"
                >{{ frames.preview.length }} characters</HintTip
              ></span
            >
          </div>
          <div class="editor__preview" data-testid="frame-preview">{{ frames.preview.text }}</div>
        </template>
      </div>

      <div class="editor__section editor__section--history">
        <!-- the canvas draws no lock history line: the whole history of the lock is the heading's tooltip -->
        <h3>
          <HintTip
            v-if="frames.shownModel && frames.info.lock_history?.length"
            :text="lockHistoryLine(frames.info.lock_history)"
            >History</HintTip
          >
          <template v-else>History</template>
        </h3>
        <p v-if="!frames.info.history.length" class="editor__note">No versions yet</p>
        <ol v-else class="editor__history" data-testid="frame-history">
          <li v-for="v in newestFirst(frames.info.history)" :key="v.id" :data-version="v.id">
            <span class="editor__version">{{ versionTag(v) }}</span>
            <span v-if="v.id === currentId" class="pill pill--ok"><i class="dot" aria-hidden="true" />current</span>
            <span class="editor__when">{{ shortStamp(v.created_ms) }}</span>
            <button
              v-if="v.id !== currentId"
              type="button"
              class="btn btn--roll"
              :aria-label="`Roll back to version ${v.id}`"
              :disabled="frames.controlsOff"
              @click="frames.rollback(v.id)"
            >
              Roll back to this
            </button>
          </li>
        </ol>
      </div>
    </div>
    <p v-else class="editor__note" role="status" data-testid="frame-missing">
      {{ frames.loading ? "Loading the frame..." : "This frame is not listed any more. Close this and pick another." }}
    </p>
    <template v-if="frames.shown && frames.info" #footer>
      <button type="button" class="btn btn--primary" :disabled="!frames.canSave" @click="frames.save">Save</button>
      <button
        v-if="frames.shownModel && frames.shownModel.source === 'own'"
        type="button"
        class="btn"
        :disabled="frames.controlsOff"
        @click="frames.follow"
      >
        Follow the default
      </button>
      <button
        v-if="frames.shown.target === DEFAULT_TARGET"
        type="button"
        class="btn"
        :disabled="frames.controlsOff"
        @click="frames.applyAll"
      >
        Apply to all models of this kind
      </button>
      <button type="button" class="btn" :disabled="frames.controlsOff" @click="frames.previewNow">Preview</button>
    </template>
  </DetailDrawer>
</template>

<style scoped>
.frame-editor {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.frame-editor h3 {
  margin: 0 0 4px;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border-radius: 12px;
  font-size: 11px;
  font-weight: 500;
}
.pill--info {
  background: var(--rl-info-bg);
  color: var(--rl-info);
}
.pill--neutral {
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
}
.pill--ok {
  background: var(--rl-ok-bg);
  color: var(--rl-ok);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 3px;
  background: currentcolor;
}
.editor__hint {
  margin: 0;
  font-size: 13px;
  line-height: 18px;
  color: var(--rl-muted);
}
.editor__hint code {
  font-family: var(--rl-mono);
  color: var(--rl-fg);
}
.editor__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.editor__label-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.editor__field label,
.editor__strong {
  font-size: 13px;
  font-weight: 500;
}
.editor__count {
  font-family: var(--rl-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--rl-faint);
}
.editor__text,
.editor__input {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
}
.editor__text {
  padding: 10px 12px;
  font-family: var(--rl-mono);
  font-size: 13px;
  line-height: 20px;
  resize: vertical;
}
.editor__input {
  height: 36px;
  padding: 0 12px;
  font: inherit;
  font-size: 14px;
  line-height: normal;
}
.editor__text:read-only {
  color: var(--rl-muted);
}
.editor__text.is-invalid {
  border-color: var(--rl-bad);
}
.editor__alert {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--rl-bad);
  font-size: 12px;
  line-height: 16px;
}
.editor__note {
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-muted);
}
.btn {
  box-sizing: border-box;
  height: 36px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  cursor: pointer;
}
.btn:hover:not(:disabled) {
  background: var(--rl-raised);
}
.btn:disabled {
  color: var(--rl-faint);
  cursor: default;
}
.btn--primary {
  padding: 0 16px;
  border: 0;
  background: var(--rl-primary);
  color: #ffffff;
  font-weight: 600;
}
.btn--primary:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.btn--primary:disabled {
  background: var(--rl-primary);
  color: #ffffff;
  opacity: 0.45;
  cursor: not-allowed;
}
.editor__section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 16px;
  border-top: 1px solid var(--rl-line);
}
/* the history is a plain block: its heading's own 4 px margin is the only space above the list */
.editor__section--history {
  display: block;
}
.editor__preview {
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--rl-raised);
  font-family: var(--rl-mono);
  font-size: 13px;
  line-height: 20px;
  overflow-wrap: anywhere;
}
.editor__history {
  margin: 0;
  padding: 0;
  list-style: none;
}
.editor__history li {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 12px;
  min-height: 44px;
  border-bottom: 1px solid var(--rl-line);
}
.editor__version {
  min-width: 24px;
  font-family: var(--rl-mono);
  font-size: 13px;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.editor__when {
  flex: 1;
  font-family: var(--rl-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--rl-faint);
}
.btn--roll {
  height: 30px;
  padding: 0 10px;
  background: transparent;
}
@media (min-width: 768px) and (max-width: 1279px) {
  .editor__history li {
    min-height: 48px;
  }
  .btn--roll {
    height: 34px;
    padding: 0 12px;
  }
}
@media (max-width: 767px) {
  .editor__text {
    min-height: 0;
  }
  .editor__input {
    height: 44px;
  }
  .editor__history li {
    min-height: 56px;
    gap: 4px 12px;
  }
  .btn--roll {
    height: 44px;
    padding: 0 12px;
  }
}
</style>
