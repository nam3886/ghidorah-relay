<script setup lang="ts">
import {
  appliedLine,
  DEFAULT_TARGET,
  lockHistoryLine,
  PROMPT_MARKER,
  TRACKING_MARKER,
  versionLine,
} from "./frames-model";
import type { FramesController } from "./use-frames";

// The editor of one frame: the text, Save / Follow the default / Apply to all, a preview of the draft, and the history
// with Roll back. Every frame text is shown as text. While a load or a write is in flight the draft is read-only and
// every control is off (the controller decides, see use-frames.ts).
defineProps<{ frames: FramesController }>();
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
      :readonly="frames.controlsOff"
      class="frame-editor__text"
      aria-label="Frame text"
      @update:model-value="frames.setDraft"
    />
    <p v-if="frames.refusal" class="frame-editor__refusal" role="alert">{{ frames.refusal }}</p>
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
    <div v-if="frames.warnings.length" class="frame-editor__warnings" role="status">
      {{ frames.warnings.join(" ") }}
    </div>
    <div v-if="frames.applied" class="frame-editor__applied" role="status">{{ appliedLine(frames.applied) }}</div>

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
      <p v-if="!frames.info.history.length" class="frame-editor__help">No versions yet</p>
      <ol v-else class="frame-editor__history">
        <li v-for="v in frames.info.history" :key="v.id" :data-version="v.id">
          <div class="frame-editor__version">
            <span>{{ versionLine(v) }}</span>
            <el-button
              size="small"
              :disabled="frames.controlsOff"
              :aria-label="`Roll back to version ${v.id}`"
              @click="frames.rollback(v.id)"
              >Roll back to this</el-button
            >
          </div>
          <pre>{{ v.text === null ? "(follows the default)" : v.text }}</pre>
        </li>
      </ol>
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
.frame-editor__refusal {
  margin: 0;
  color: var(--rl-bad);
  font-size: 13px;
}
.frame-editor__buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.frame-editor__warnings {
  padding: 8px 12px;
  color: var(--rl-warn);
  font-size: 12px;
  background: var(--rl-warn-bg);
  border-radius: var(--el-border-radius-base, 6px);
}
.frame-editor__applied {
  padding: 8px 12px;
  font-size: 12px;
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
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
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.frame-editor__history li {
  padding: 8px;
  font-size: 12px;
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.frame-editor__version {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 4px;
  font-weight: 500;
}
.frame-editor__empty {
  margin: 0;
  padding: 16px;
  color: var(--rl-muted);
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
</style>
