<script setup lang="ts">
import { RefreshLeft } from "@element-plus/icons-vue";
import IconAction from "@/components/shared/IconAction.vue";
import { fmtTime } from "@/lib/format";
import {
  appliedLine,
  DEFAULT_TARGET,
  lockHistoryLine,
  newestFirst,
  PROMPT_MARKER,
  TRACKING_MARKER,
  versionLine,
  versionType,
} from "./frames-model";
import type { FramesController } from "./use-frames";

// The editor of one frame: the text, Save / Follow the default / Apply to all, a preview of the draft, and the history
// with Roll back. Every frame text is shown as text. While a load or a write is in flight the draft is read-only and
// every control is off (the controller decides, see use-frames.ts).
defineProps<{ frames: FramesController }>();

// The empty box shows the shape of a frame. Built from the markers, so no template holds a double brace.
const FRAME_SHAPE = `${PROMPT_MARKER} ... ${TRACKING_MARKER}`;
</script>

<template>
  <section v-if="frames.shown && frames.info" class="frame-editor" aria-label="Frame editor">
    <h3>{{ frames.shown.kind }} / {{ frames.shown.target }}</h3>
    <p class="frame-editor__help">
      Write one line, at most 1,000 characters, with <code>{{ PROMPT_MARKER }}</code> (the customer's words) and
      <code>{{ TRACKING_MARKER }}</code> (the job's marker) once each. A save applies only to jobs created after it; the
      frame is not checked for ownership or consent wording.
    </p>
    <p v-if="frames.shownModel && frames.shownModel.source === 'default'" class="frame-editor__help">
      This model follows the default. Saving gives it its own frame; until then a change to the default reaches it.
    </p>
    <el-input
      :model-value="frames.draft"
      type="textarea"
      :rows="4"
      :placeholder="FRAME_SHAPE"
      :readonly="frames.controlsOff"
      class="frame-editor__text"
      aria-label="Frame text"
      @update:model-value="frames.setDraft"
    />
    <el-alert
      v-if="frames.refusal"
      type="error"
      show-icon
      :closable="false"
      :title="frames.refusal"
      class="frame-editor__refusal"
    />
    <div class="frame-editor__buttons">
      <el-button type="primary" :disabled="!frames.canSave" @click="frames.save">Save</el-button>
      <el-button
        v-if="frames.shownModel && frames.shownModel.source === 'own'"
        :disabled="frames.controlsOff"
        @click="frames.follow"
        >Follow the default</el-button
      >
      <el-button v-if="frames.shown.target === DEFAULT_TARGET" :disabled="frames.controlsOff" @click="frames.applyAll"
        >Apply to all models of this kind</el-button
      >
    </div>
    <el-alert
      v-if="frames.warnings.length"
      type="warning"
      show-icon
      :closable="false"
      :title="frames.warnings.join(' ')"
      class="frame-editor__warnings"
      role="status"
    />
    <el-alert
      v-if="frames.applied"
      type="info"
      show-icon
      :closable="false"
      :title="appliedLine(frames.applied)"
      class="frame-editor__applied"
      role="status"
    />

    <div class="frame-editor__section">
      <h4>Preview</h4>
      <p class="frame-editor__help">
        Renders the draft above with a sample prompt, without sending or charging. The tracking code differs for every
        job.
      </p>
      <div class="frame-editor__preview-row">
        <el-input
          :model-value="frames.previewPrompt"
          aria-label="Sample prompt"
          :disabled="frames.controlsOff"
          @update:model-value="frames.setPreviewPrompt"
        />
        <el-button :disabled="frames.controlsOff" @click="frames.previewNow">Preview</el-button>
      </div>
      <pre v-if="frames.preview" class="frame-editor__preview">{{ frames.preview.text }}</pre>
      <p v-if="frames.preview" class="frame-editor__help">
        {{ frames.preview.length }} characters, shown with the sample tracking code ({{ frames.preview.source }} frame)
      </p>
    </div>

    <div class="frame-editor__section">
      <h4>History</h4>
      <p v-if="frames.shownModel && frames.info.lock_history?.length" class="frame-editor__help">
        {{ lockHistoryLine(frames.info.lock_history) }}
      </p>
      <el-empty v-if="!frames.info.history.length" description="No versions yet" :image-size="48" />
      <el-timeline v-else class="frame-editor__history">
        <el-timeline-item
          v-for="v in newestFirst(frames.info.history)"
          :key="v.id"
          :type="versionType(v)"
          :timestamp="fmtTime(v.created_ms)"
          placement="top"
          :data-version="v.id"
        >
          <div class="frame-editor__version">
            <span>{{ versionLine(v) }}</span>
            <IconAction
              :icon="RefreshLeft"
              :label="`Roll back to version ${v.id}`"
              :disabled="frames.controlsOff"
              @click="frames.rollback(v.id)"
            />
          </div>
          <pre>{{ v.text === null ? "(follows the default)" : v.text }}</pre>
        </el-timeline-item>
      </el-timeline>
    </div>
  </section>
  <p v-else-if="frames.data" class="frame-editor__empty">Pick a model or a default to edit its frame.</p>
</template>

<style scoped>
.frame-editor {
  display: grid;
  gap: 12px;
  padding: 16px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.frame-editor h3,
.frame-editor h4 {
  margin: 0;
  font-size: 16px;
}
.frame-editor h4 {
  font-size: 14px;
}
.frame-editor__help {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
}
.frame-editor__text :deep(textarea) {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 13px;
}
.frame-editor__buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.frame-editor__section {
  display: grid;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--rl-line);
}
.frame-editor__preview-row {
  display: flex;
  gap: 8px;
}
.frame-editor__preview,
.frame-editor__history pre {
  max-height: 10rem;
  margin: 0;
  padding: 8px;
  overflow: auto;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: var(--rl-surface-2);
  border-radius: var(--el-border-radius-base, 6px);
}
.frame-editor__history {
  margin: 0;
  padding-left: 4px;
  font-size: 12px;
}
.frame-editor__version {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  font-weight: 500;
}
/* a hint line, not a third card: nothing to edit yet */
.frame-editor__empty {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
}
</style>
