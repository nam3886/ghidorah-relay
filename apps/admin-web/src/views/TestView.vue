<script setup lang="ts">
// The Test tab calls the PUBLIC routes with the API key typed here. The tab is kept alive (App.vue), so a running job,
// the loaded models and the form survive tab switches; the script block also gives KeepAlive the name TestView.
// Where things are kept: the form in localStorage, the images in IndexedDB, the key in sessionStorage only after a
// successful model load (see lib/test-form.ts, lib/test-files.ts and components/test/test-key.ts).
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from "vue";
import { getOptions, type PublicModel, type Template } from "@/api/test";
import TestImages from "@/components/test/TestImages.vue";
import TestResult from "@/components/test/TestResult.vue";
import { useGenerate } from "@/components/test/use-generate";
import { useTestSession } from "@/components/test/use-test-session";
import { useLatest } from "@/composables/use-latest";
import { notify } from "@/lib/notify";
import {
  addFiles,
  clearFiles,
  DROPPED_NOTE,
  FILE_STORE,
  idbStore,
  LIMITS_TEXT,
  NOT_SAVED_NOTE,
  restoreFiles,
  saveFiles,
  type StoredFile,
} from "@/lib/test-files";
import { blobToDataUri, buildBody, clearSavedForm, emptyForm, optionLabel, readForm, saveForm } from "@/lib/test-form";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const store = inject(FILE_STORE, idbStore);
const session = useTestSession();
const generate = useGenerate(session);
const { typedKey, models, points, status } = session;
const { submitting, job, jobKind } = generate;

const initial = readForm();
const form = reactive(initial.form);
// True while localStorage refuses the form (read or write): the tab then says nothing is kept in this browser.
const formFailed = ref(!initial.ok);
const files = shallowRef<StoredFile[]>([]);
const note = ref("");
const chosen = shallowRef<PublicModel | null>(null);
const ratios = ref<string[]>([]);
const styles = ref<string[]>([]);
const templates = shallowRef<Template[]>([]);
const pickLatest = useLatest();

// The form is written on every input (the fields call this), never anything but the form: not the API key.
const persist = () => void (formFailed.value = !saveForm(form));
const shownNote = computed(() => note.value || (formFailed.value ? NOT_SAVED_NOTE : ""));

const groups = computed(() => {
  const byKind = new Map<string, PublicModel[]>();
  for (const m of models.value) byKind.set(m.kind, [...(byKind.get(m.kind) ?? []), m]);
  return [...byKind.keys()].sort().map((kind) => ({ kind, models: byKind.get(kind) ?? [] }));
});
const modelInfo = computed(() => {
  const m = chosen.value;
  return m ? `${m.kind} · ${m.owned_by} · price ${JSON.stringify(m.prices)} · limits ${JSON.stringify(m.limits)}` : "";
});

async function pick(model: PublicModel): Promise<void> {
  const ticket = pickLatest.next();
  chosen.value = model;
  form.model = model.id;
  persist();
  ratios.value = [];
  styles.value = [];
  templates.value = [];
  try {
    const extras = await session.call((key) => getOptions(key, model.owned_by));
    if (!pickLatest.isCurrent(ticket)) return;
    ratios.value = (extras.ratios ?? []).map(optionLabel).filter(Boolean);
    styles.value = (extras.styles ?? []).map(optionLabel).filter(Boolean);
    templates.value = extras.templates ?? [];
  } catch {
    /* the options are a convenience: the model can be used without them */
  }
}

// After a load, the model saved in the form is picked again if the key still has it.
async function afterLoad(loaded: boolean): Promise<void> {
  if (!loaded) return;
  const saved = models.value.find((m) => m.id === form.model);
  if (saved) await pick(saved);
}

// A second click (or Enter) while a load is on its way sends nothing: the button shows the busy state.
const loadingModels = ref(false);
async function loadModels(): Promise<void> {
  if (loadingModels.value) return;
  loadingModels.value = true;
  try {
    await afterLoad(await session.loadModels());
  } finally {
    loadingModels.value = false;
  }
}

// Template: a convenience only, the prompt box takes its text and the request has no template field.
function applyTemplate(t: Template): void {
  form.prompt = t.prompt;
  persist();
}

// Writes are queued, so the store always ends with the newest list.
let writing: Promise<unknown> = Promise.resolve();
function persistFiles(): Promise<unknown> {
  writing = writing.then(async () => {
    const saved = await saveFiles(files.value, store);
    files.value = saved.files;
    note.value = saved.saved ? "" : NOT_SAVED_NOTE;
  });
  return writing;
}

async function onAdd(picked: File[]): Promise<void> {
  const out = addFiles(files.value, picked);
  files.value = out.files;
  if (out.skipped.length) notify.error(`Not attached (${LIMITS_TEXT}): ${out.skipped.join(", ")}`);
  await persistFiles();
}

async function onRemove(index: number): Promise<void> {
  files.value = files.value.filter((_, i) => i !== index);
  await persistFiles();
}

async function clearForm(): Promise<void> {
  formFailed.value = !clearSavedForm();
  Object.assign(form, emptyForm());
  files.value = [];
  note.value = "";
  chosen.value = null;
  pickLatest.invalidate();
  ratios.value = [];
  styles.value = [];
  templates.value = [];
  writing = writing.then(() => clearFiles(store));
  await writing;
}

async function generateNow(): Promise<void> {
  const model = chosen.value;
  if (!model) return;
  await generate.start(model, async () => {
    const uris = await Promise.all(files.value.map((f) => blobToDataUri(f.blob)));
    return buildBody(model, form, uris);
  });
}

// Sign-out and a 401 end the running job and drop the key and the models.
const offSignOut = auth.onSignOut(() => {
  generate.stop();
  session.reset();
});
onBeforeUnmount(() => {
  generate.stop();
  pickLatest.invalidate();
  offSignOut();
});

onMounted(async () => {
  const restored = await restoreFiles(store);
  files.value = restored.files;
  if (restored.dropped) note.value = DROPPED_NOTE;
  if (restored.failed) note.value = NOT_SAVED_NOTE;
  await afterLoad(await session.restore());
});
</script>

<template>
  <section class="test-tab">
    <div class="test-tab__key">
      <el-form inline label-position="left" @submit.prevent>
        <el-form-item label="API key">
          <el-input
            v-model="typedKey"
            type="password"
            autocomplete="off"
            placeholder="rk-..."
            spellcheck="false"
            class="test-tab__key-input"
            @keyup.enter="loadModels"
          />
        </el-form-item>
        <el-button type="primary" :loading="loadingModels" @click="loadModels">Load models</el-button>
        <el-button @click="session.forget">Forget key</el-button>
        <el-button class="test-tab__clear" title="Reset the form and the attached images" @click="clearForm"
          >Clear form</el-button
        >
      </el-form>
      <p class="test-tab__hint">
        The key is kept for this browser tab only (session storage): it is forgotten when the tab closes and never saved
        anywhere else. The model, the options, the prompt and the attached images are saved in this browser until you
        press Clear form.
      </p>
    </div>

    <div class="test-tab__grid">
      <aside class="test-tab__models">
        <el-empty
          v-if="!groups.length"
          class="test-tab__empty"
          :image-size="64"
          description="No models loaded. Enter an API key above and press Load models."
          data-testid="test-models-empty"
        />
        <div v-for="g in groups" :key="g.kind" class="test-tab__group">
          <div class="test-tab__group-title">{{ g.kind }}</div>
          <button
            v-for="m in g.models"
            :key="m.id"
            type="button"
            class="test-tab__model"
            :class="{ 'is-active': chosen?.id === m.id }"
            :aria-pressed="chosen?.id === m.id"
            @click="pick(m)"
          >
            <span>{{ m.id }}</span>
            <span class="test-tab__owner">{{ m.owned_by }}</span>
          </button>
        </div>
        <details v-if="points" class="test-tab__points-box">
          <summary>Wallet limits</summary>
          <pre class="test-tab__points" aria-label="Points">{{ points }}</pre>
        </details>
      </aside>

      <section class="test-tab__form">
        <h3>{{ chosen?.id ?? "Pick a model" }}</h3>
        <p class="test-tab__hint">{{ modelInfo }}</p>
        <div v-if="templates.length" class="test-tab__templates">
          <div class="test-tab__hint">Templates (click to use the prompt)</div>
          <button v-for="(t, i) in templates" :key="i" type="button" @click="applyTemplate(t)">
            {{ t.show_value || t.prompt }}
          </button>
        </div>
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="Prompt">
            <el-input v-model="form.prompt" type="textarea" :rows="8" @input="persist" />
          </el-form-item>
          <div class="test-tab__row">
            <el-form-item label="Duration (s, video)">
              <el-input v-model="form.duration" inputmode="numeric" placeholder="optional" @input="persist" />
            </el-form-item>
            <el-form-item label="Ratio">
              <el-input v-model="form.ratio" list="test-ratio-list" placeholder="16:9" @input="persist" />
              <datalist id="test-ratio-list">
                <option v-for="r in ratios" :key="r" :value="r" />
              </datalist>
            </el-form-item>
            <el-form-item label="Count (n)">
              <el-input v-model="form.count" inputmode="numeric" placeholder="optional" @input="persist" />
            </el-form-item>
            <el-form-item label="Style (image)">
              <el-input v-model="form.style" list="test-style-list" placeholder="optional" @input="persist" />
              <datalist id="test-style-list">
                <option v-for="s in styles" :key="s" :value="s" />
              </datalist>
            </el-form-item>
          </div>
          <el-form-item label="Reference image URLs (https, one per line)">
            <el-input v-model="form.images" type="textarea" :rows="2" @input="persist" />
          </el-form-item>
          <TestImages :files="files" :note="shownNote" @add="onAdd" @remove="onRemove" />
          <el-form-item label="Title" class="test-tab__title">
            <el-input v-model="form.title" @input="persist" />
          </el-form-item>
          <el-form-item label="Extra options (JSON object, optional)">
            <el-input v-model="form.options" type="textarea" :rows="2" @input="persist" />
          </el-form-item>
        </el-form>
        <el-button type="primary" class="test-tab__generate" :disabled="!chosen || submitting" @click="generateNow"
          >Generate</el-button
        >
      </section>

      <TestResult :status="status" :job="job" :kind="jobKind" />
    </div>
  </section>
</template>

<style scoped>
.test-tab {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 16px;
}
.test-tab__key,
.test-tab__form {
  padding: 16px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.test-tab__key-input {
  width: 320px;
  max-width: 100%;
}
/* on a phone the key field takes the full row and the buttons wrap below it, so the box never outgrows the page */
.test-tab__key :deep(.el-form-item) {
  max-width: 100%;
  margin-right: 0;
}
.test-tab__key :deep(.el-form-item__content) {
  min-width: 0;
}
.test-tab__hint {
  margin: 0;
  color: var(--rl-muted);
  font-size: 12px;
}
.test-tab__grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
@media (max-width: 1100px) {
  .test-tab__grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
.test-tab__models {
  min-width: 0;
}
.test-tab__group {
  margin-bottom: 12px;
}
.test-tab__group-title {
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 600;
  background: var(--rl-surface-2);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px) var(--el-border-radius-base, 6px) 0 0;
}
.test-tab__model {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  color: var(--rl-fg);
  font: inherit;
  text-align: left;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-top: 0;
  cursor: pointer;
}
.test-tab__model:hover {
  background: var(--rl-surface-2);
}
.test-tab__model.is-active {
  font-weight: 600;
  background: var(--rl-surface-2);
}
.test-tab__owner {
  color: var(--rl-muted);
}
/* the model column keeps its place while nothing is loaded, so the three columns do not jump when models arrive */
.test-tab__empty {
  border: 1px dashed var(--rl-line-strong);
  border-radius: var(--el-border-radius-base, 6px);
}
/* a reset is not the main action: neutral until the pointer is on it */
.test-tab__clear {
  --el-button-hover-text-color: var(--rl-bad);
  --el-button-hover-border-color: var(--rl-bad);
  --el-button-hover-bg-color: var(--rl-bad-bg);
}
.test-tab__points-box {
  color: var(--rl-muted);
  font-size: 12px;
}
.test-tab__points-box summary {
  cursor: pointer;
}
.test-tab__points {
  margin: 4px 0 0;
  color: var(--rl-muted);
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.test-tab__form {
  min-width: 0;
}
.test-tab__form h3 {
  margin: 0 0 4px;
  font-size: 16px;
}
.test-tab__templates {
  display: grid;
  max-height: 10rem;
  margin: 8px 0;
  overflow-y: auto;
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.test-tab__templates button {
  padding: 6px 12px;
  color: var(--rl-fg);
  font: inherit;
  font-size: 12px;
  text-align: left;
  background: transparent;
  border: 0;
  border-bottom: 1px solid var(--rl-line);
  cursor: pointer;
}
.test-tab__row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0 12px;
}
@media (max-width: 700px) {
  .test-tab__row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.test-tab__title {
  margin-top: 12px;
}
.test-tab__generate {
  width: 100%;
}
</style>
