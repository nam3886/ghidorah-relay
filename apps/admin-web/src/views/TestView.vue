<script setup lang="ts">
// The Test tab calls the PUBLIC routes with the API key typed here. The tab is kept alive (App.vue), so a running job,
// the loaded models and the form survive tab switches; the script block also gives KeepAlive the name TestView.
// Where things are kept: the form in localStorage, the images in IndexedDB, the key in sessionStorage only after a
// successful model load (see lib/test-form.ts, lib/test-files.ts and components/test/test-key.ts).
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from "vue";
import { getOptions, type PublicModel, type Template } from "@/api/test";
import TestImages from "@/components/test/TestImages.vue";
import TestResult from "@/components/test/TestResult.vue";
import {
  capital,
  defaultRatioOf,
  durationsOf,
  durationsPhrase,
  ratiosOf,
  titleOf,
  walletRows,
} from "@/components/test/test-models";
import { useGenerate } from "@/components/test/use-generate";
import { useTestSession } from "@/components/test/use-test-session";
import { useBreakpoint } from "@/composables/use-breakpoint";
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
import {
  blobToDataUri,
  buildBody,
  clearSavedForm,
  emptyForm,
  type FormField,
  optionLabel,
  optionValue,
  readForm,
  saveForm,
} from "@/lib/test-form";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const store = inject(FILE_STORE, idbStore);
const session = useTestSession();
const generate = useGenerate(session);
const { typedKey, models, points, status } = session;
const { submitting, job, jobKind } = generate;
const { isPhone, isTablet } = useBreakpoint();

const initial = readForm();
const form = reactive(initial.form);
// True while localStorage refuses the form (read or write): the tab then says nothing is kept in this browser.
const formFailed = ref(!initial.ok);
const files = shallowRef<StoredFile[]>([]);
const note = ref("");
const chosen = shallowRef<PublicModel | null>(null);
const ratios = ref<string[]>([]);
// A style is sent by the value the provider takes ("oil_painting") and shown by its label.
const styles = ref<Array<{ value: string; label: string }>>([]);
const templates = shallowRef<Template[]>([]);
const pickLatest = useLatest();

// The form is written on every input (the fields call this), never anything but the form: not the API key.
const persist = () => void (formFailed.value = !saveForm(form));
const shownNote = computed(() => note.value || (formFailed.value ? NOT_SAVED_NOTE : ""));
// A select or a number box does not go through v-model (which would turn a number box's text into a number).
function setField(name: FormField, value: string): void {
  form[name] = value;
  persist();
}

const groups = computed(() => {
  const byKind = new Map<string, PublicModel[]>();
  for (const m of models.value) byKind.set(m.kind, [...(byKind.get(m.kind) ?? []), m]);
  // video first, as drawn
  return [...byKind.keys()]
    .sort((a, b) => Number(b === "video") - Number(a === "video") || a.localeCompare(b))
    .map((kind) => ({ kind, models: byKind.get(kind) ?? [] }));
});
const durations = computed(() => durationsOf(chosen.value));
// The heading's second line: `<kind> · ` then the lengths the model takes ("5, 10, 20, 25 or 30 s"), or its owner.
const infoTail = computed(() => durationsPhrase(durations.value) || chosen.value?.owned_by || "");
const wallet = computed(() => walletRows(points.value));
// A saved value the list no longer has stays selectable, so choosing a model never silently changes the form.
const withSaved = (list: string[], saved: string): string[] =>
  saved && !list.includes(saved) ? [...list, saved] : list;
const durationChoices = computed(() => withSaved(durations.value.map(String), form.duration));
// The model's own ratios when it names them (dola's options hold its image ratios, offered for every model), else the
// provider's options. The form never keeps a ratio the model does not take (see pick).
const ratioChoices = computed(() => {
  const own = ratiosOf(chosen.value);
  return own.length ? own : withSaved(ratios.value, form.ratio);
});
const styleChoices = computed(() =>
  form.style && !styles.value.some((s) => s.value === form.style)
    ? [...styles.value, { value: form.style, label: form.style }]
    : styles.value,
);

async function pick(model: PublicModel): Promise<void> {
  const ticket = pickLatest.next();
  chosen.value = model;
  form.model = model.id;
  // a ratio the model does not take is never sent: its own default (or none) takes its place
  const own = ratiosOf(model);
  if (own.length && form.ratio && !own.includes(form.ratio)) form.ratio = defaultRatioOf(model);
  persist();
  ratios.value = [];
  styles.value = [];
  templates.value = [];
  try {
    const extras = await session.call((key) => getOptions(key, model.owned_by));
    if (!pickLatest.isCurrent(ticket)) return;
    ratios.value = (extras.ratios ?? []).map(optionValue).filter(Boolean);
    styles.value = (extras.styles ?? [])
      .map((o) => ({ value: optionValue(o), label: optionLabel(o) }))
      .filter((s) => s.value);
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
  <section class="test-tab" :class="{ 'is-tablet': isTablet, 'is-phone': isPhone }">
    <div>
      <h1 class="test-tab__title">Test</h1>
      <p class="test-tab__subtitle">Send a real generation through the API with a key you paste here.</p>
    </div>

    <section class="key" aria-label="API key">
      <div class="key__field">
        <label for="test-api-key">API key</label>
        <input
          id="test-api-key"
          v-model="typedKey"
          type="password"
          autocomplete="off"
          placeholder="rk-..."
          spellcheck="false"
          @keyup.enter="loadModels"
        />
      </div>
      <button
        type="button"
        class="btn btn--primary key__load"
        :aria-busy="loadingModels"
        :class="{ 'is-busy': loadingModels }"
        @click="loadModels"
      >
        Load models
      </button>
      <div class="key__more">
        <button type="button" class="btn btn--secondary" @click="session.reset">Forget key</button>
        <button
          type="button"
          class="btn btn--text test-tab__clear"
          title="Reset the form and the attached images"
          @click="clearForm"
        >
          Clear form
        </button>
      </div>
      <span class="key__note">The key lives only in this tab’s session storage.</span>
    </section>

    <div class="grid">
      <section class="models" aria-label="Models" data-testid="test-models">
        <p v-if="!groups.length" class="models__empty" data-testid="test-models-empty">No models loaded.</p>
        <!-- the phone draws one row of every model, the scroller itself is the group -->
        <div
          class="models__scroll"
          :role="isPhone && groups.length ? 'group' : undefined"
          :aria-label="isPhone && groups.length ? 'Models' : undefined"
        >
          <div
            v-for="g in groups"
            :key="g.kind"
            :role="isPhone ? undefined : 'group'"
            :aria-label="isPhone ? undefined : `${capital(g.kind)} models`"
            class="models__group"
          >
            <h2>{{ capital(g.kind) }}</h2>
            <button
              v-for="m in g.models"
              :key="m.id"
              type="button"
              class="model"
              :class="{ 'is-active': chosen?.id === m.id }"
              :aria-pressed="chosen?.id === m.id"
              data-testid="test-model"
              @click="pick(m)"
            >
              <span class="model__main">
                <span class="model__title">{{ titleOf(m) }}</span>
                <!-- the tablet writes the owner on the id line, as one text -->
                <span v-if="titleOf(m) !== m.id" class="model__id">{{
                  isTablet ? `${m.id} · ${m.owned_by}` : m.id
                }}</span>
                <span class="model__kind">{{ capital(m.kind) }} · {{ m.owned_by }}</span>
              </span>
              <span class="model__owner">{{ m.owned_by }}</span>
            </button>
          </div>
        </div>
        <details v-if="points" class="test-tab__points-box">
          <summary>
            Wallet limits
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </summary>
          <dl v-if="wallet" class="wallet">
            <div v-for="w in wallet" :key="w.label" class="wallet__row">
              <dt>{{ w.label }}</dt>
              <span class="wallet__leader" aria-hidden="true" />
              <dd>{{ w.value }}</dd>
            </div>
          </dl>
          <pre v-else class="test-tab__points" aria-label="Points">{{ points }}</pre>
        </details>
      </section>

      <section class="form" aria-labelledby="test-form-title">
        <div>
          <h2 id="test-form-title">{{ chosen ? titleOf(chosen) : "Pick a model" }}</h2>
          <p v-if="chosen" class="form__info">
            {{ chosen.kind }} · <span class="num">{{ infoTail }}</span>
          </p>
        </div>
        <div v-if="templates.length" class="chips" role="group" aria-label="Prompt templates">
          <!-- the whole template in the shared hint look (HintTip's), on the chip itself: no second tab stop -->
          <el-tooltip
            v-for="(t, i) in templates"
            :key="i"
            :content="t.show_value || t.prompt"
            placement="top"
            effect="light"
            :trigger="['hover', 'focus']"
            popper-class="rl-tip test-chip-tip"
            :show-after="150"
            :show-arrow="false"
          >
            <button type="button" class="chip" @click="applyTemplate(t)">
              {{ t.show_value || t.prompt }}
            </button>
          </el-tooltip>
        </div>

        <div class="field">
          <label for="test-prompt">Prompt</label>
          <textarea
            id="test-prompt"
            v-model="form.prompt"
            :rows="isPhone ? 9 : isTablet ? 7 : 8"
            class="control control--text"
            @input="persist"
          />
        </div>

        <div class="trio">
          <div class="field">
            <label for="test-duration">Duration</label>
            <select
              v-if="durations.length"
              id="test-duration"
              class="control control--select"
              :value="form.duration"
              @change="setField('duration', ($event.target as HTMLSelectElement).value)"
            >
              <option value="">Default</option>
              <option v-for="d in durationChoices" :key="d" :value="d">{{ d }} s</option>
            </select>
            <input
              v-else
              id="test-duration"
              v-model="form.duration"
              class="control"
              inputmode="numeric"
              placeholder="optional"
              @input="persist"
            />
          </div>
          <div class="field">
            <label for="test-ratio">Ratio</label>
            <select
              v-if="ratios.length || ratiosOf(chosen).length"
              id="test-ratio"
              class="control control--select"
              :value="form.ratio"
              @change="setField('ratio', ($event.target as HTMLSelectElement).value)"
            >
              <option value="">Default</option>
              <option v-for="r in ratioChoices" :key="r" :value="r">{{ r }}</option>
            </select>
            <input v-else id="test-ratio" v-model="form.ratio" class="control" placeholder="16:9" @input="persist" />
          </div>
          <div class="field">
            <label for="test-count">Count</label>
            <input
              id="test-count"
              class="control control--num"
              type="number"
              min="1"
              placeholder="1"
              :value="form.count"
              @input="setField('count', ($event.target as HTMLInputElement).value)"
            />
          </div>
          <div v-if="chosen?.kind === 'image'" class="field">
            <label for="test-style">Style</label>
            <select
              v-if="styles.length"
              id="test-style"
              class="control control--select"
              :value="form.style"
              @change="setField('style', ($event.target as HTMLSelectElement).value)"
            >
              <option value="">Default</option>
              <option v-for="s in styleChoices" :key="s.value" :value="s.value">{{ s.label }}</option>
            </select>
            <input
              v-else
              id="test-style"
              v-model="form.style"
              class="control"
              placeholder="optional"
              @input="persist"
            />
          </div>
        </div>

        <div class="field">
          <label for="test-refs">Reference image URLs <span class="field__hint">· one per line</span></label>
          <textarea
            id="test-refs"
            v-model="form.images"
            rows="2"
            placeholder="https://..."
            class="control control--text control--mono control--refs"
            @input="persist"
          />
        </div>

        <TestImages :files="files" :note="shownNote" @add="onAdd" @remove="onRemove" />

        <div class="field">
          <label for="test-title">Title</label>
          <input id="test-title" v-model="form.title" type="text" class="control" @input="persist" />
        </div>

        <div class="field">
          <label for="test-extra">Extra options (JSON)</label>
          <textarea
            id="test-extra"
            v-model="form.options"
            rows="3"
            class="control control--text control--mono"
            @input="persist"
          />
        </div>

        <button
          type="button"
          class="btn btn--primary form__generate"
          :disabled="!chosen || submitting"
          @click="generateNow"
        >
          Generate
        </button>
      </section>

      <TestResult :status="status" :job="job" :kind="jobKind" :loaded="models.length > 0" />
    </div>
  </section>
</template>

<style scoped>
.test-tab {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.test-tab__title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 600;
  letter-spacing: -0.015em;
}
.test-tab__subtitle {
  margin: 4px 0 0;
  color: var(--rl-muted);
}

/* buttons */
.btn {
  box-sizing: border-box;
  height: 36px;
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
  opacity: 0.45;
  cursor: not-allowed;
}
.btn.is-busy {
  opacity: 0.7;
  cursor: progress;
}
.btn--secondary {
  padding: 0 14px;
}
.btn--text {
  padding: 0 12px;
  border: 0;
  background: transparent;
  color: var(--rl-bad);
}

/* key card */
.key {
  box-sizing: border-box;
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  padding: 16px 20px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.key__field {
  flex: 1 1 320px;
  max-width: 520px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.key__field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--rl-muted);
}
.key__field input {
  box-sizing: border-box;
  width: 100%;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 14px;
}
.key__more {
  display: contents;
}
.key__note {
  flex: 1 1 240px;
  align-self: center;
  font-size: 12px;
  color: var(--rl-faint);
}

.grid {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 20px;
}

/* models */
.models {
  flex: 1 1 260px;
  max-width: 320px;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.models__empty {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.models__scroll {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.models__group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.models__group h2 {
  margin: 0 0 2px 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-muted);
}
.model {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: 10px;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  line-height: normal;
  text-align: left;
  cursor: pointer;
}
.model:hover {
  background: var(--rl-raised);
}
.model.is-active {
  border-color: var(--rl-primary);
  background: var(--rl-primary-soft);
}
.model__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.model__title {
  font-size: 14px;
  font-weight: 500;
}
.model__id {
  font-family: var(--rl-mono);
  font-size: 12px;
  color: var(--rl-faint);
}
.model__kind {
  display: none;
  font-size: 12px;
  color: var(--rl-muted);
}
.model__owner {
  font-size: 12px;
  color: var(--rl-muted);
}
.test-tab__points-box {
  padding-top: 12px;
  border-top: 1px solid var(--rl-line);
}
.test-tab__points-box summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 500;
  list-style: none;
  cursor: pointer;
}
.test-tab__points-box summary::-webkit-details-marker {
  display: none;
}
.test-tab__points-box summary svg {
  color: var(--rl-muted);
}
.wallet {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 10px 0 0;
  font-size: 13px;
}
.wallet__row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.wallet dt {
  color: var(--rl-muted);
  white-space: nowrap;
}
.wallet__leader {
  flex: 1;
  border-bottom: 1px dotted var(--rl-line-strong);
  transform: translateY(-4px);
}
.wallet dd {
  margin: 0;
  font-family: var(--rl-mono);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.test-tab__points {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--rl-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* form */
.form {
  flex: 2 1 460px;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.form h2 {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
.form__info {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--rl-muted);
}
/* A provider's template is often its whole prompt (hundreds of characters, dozens of them): one line each, ending in
   "…" (a click puts the whole text in the prompt box), and at most three rows of chips before the block scrolls. */
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  max-height: calc(3 * var(--chip-h) + 16px);
  overflow-y: auto;
  --chip-h: 28px;
}
.chip {
  max-width: 240px;
  height: var(--chip-h);
  padding: 0 12px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 14px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 12px;
  line-height: normal;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.chip:hover {
  background: var(--rl-raised);
}
/* a template can run to thousands of characters: its hint scrolls past a few lines */
:global(.el-popper.test-chip-tip) {
  max-height: 240px;
  overflow-y: auto;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--rl-muted);
}
.field__hint {
  font-weight: 400;
  color: var(--rl-faint);
}
.control {
  box-sizing: border-box;
  width: 100%;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 14px;
}
.control--select {
  padding: 0 10px;
}
.control--num {
  font-variant-numeric: tabular-nums;
}
.control--text {
  height: auto;
  padding: 10px 12px;
  line-height: 20px;
  resize: vertical;
}
.control--mono {
  padding: 8px 12px;
  font-family: var(--rl-mono);
  font-size: 13px;
}
/* the reference box takes the font's own line height (the prompt and the JSON box are 20) */
.control--refs {
  line-height: normal;
}
.trio {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.trio .field {
  flex: 1 1 120px;
}
/* a button the design gives no padding keeps the browser's own, 1 px 6 px */
.form__generate {
  width: 100%;
  height: 40px;
  padding: 1px 6px;
  font-size: 14px;
}

@media (min-width: 768px) and (max-width: 1279px) {
  .test-tab__title {
    font-size: 24px;
    line-height: 32px;
    letter-spacing: -0.012em;
  }
  .test-tab__subtitle {
    margin-top: 2px;
  }
  .key__field {
    flex: 1 1 300px;
  }
  .key__note {
    flex: 1 1 100%;
  }
  .grid {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
  }
  .models {
    flex: none;
    max-width: none;
    gap: 12px;
    padding: 12px 16px;
  }
  .models__scroll {
    flex-direction: row;
    align-items: center;
    gap: 20px;
    overflow-x: auto;
    padding: 4px 0;
  }
  .models__group {
    flex: none;
    flex-direction: row;
    align-items: center;
    gap: 8px;
  }
  .models__group h2 {
    margin: 0 4px 0 0;
  }
  .model {
    flex: none;
    flex-direction: column;
    align-items: flex-start;
    gap: 0;
    width: auto;
    min-height: 52px;
    padding: 6px 12px;
    border-color: var(--rl-line-strong);
    white-space: nowrap;
  }
  .model__owner {
    display: none;
  }
  /* the tablet and the phone draw the texts straight in the button */
  .model__main {
    display: contents;
  }
  .test-tab__points-box summary {
    min-height: 28px;
  }
  .wallet {
    max-width: 420px;
  }
  .form {
    flex: none;
    width: 100%;
  }
  .chips {
    --chip-h: 32px;
  }
  .chip {
    border-radius: 16px;
  }
  .form__generate {
    height: 44px;
  }
}

@media (max-width: 767px) {
  .test-tab {
    gap: 16px;
  }
  .test-tab__title {
    font-size: 22px;
    line-height: 30px;
    letter-spacing: -0.01em;
  }
  .test-tab__subtitle {
    margin-top: 2px;
  }
  .key {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    padding: 16px;
  }
  .key__field {
    flex: none;
    max-width: none;
  }
  .key__field input {
    height: 44px;
  }
  .key__load {
    width: 100%;
    height: 44px;
    padding: 1px 6px;
    font-size: 14px;
  }
  .key__more {
    display: flex;
    gap: 8px;
  }
  .key__more .btn {
    flex: 1;
    height: 44px;
    padding: 1px 6px;
    border: 1px solid var(--rl-line-strong);
    font-size: 14px;
  }
  .key__note {
    flex: none;
    align-self: auto;
  }
  .grid {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    gap: 16px;
  }
  .models {
    flex: none;
    max-width: none;
    gap: 4px;
    padding: 12px 0 4px;
    overflow: hidden;
  }
  .models__empty {
    padding: 0 16px 8px;
  }
  .models__scroll {
    flex-direction: row;
    gap: 8px;
    padding: 0 16px 8px;
    overflow-x: auto;
  }
  .models__group {
    display: contents;
  }
  .models__group h2 {
    display: none;
  }
  .model {
    flex: none;
    flex-direction: column;
    align-items: stretch;
    justify-content: center;
    gap: 1px;
    width: 172px;
    min-height: 64px;
    padding: 8px 12px;
    border-color: var(--rl-line-strong);
    border-radius: 12px;
    background: var(--rl-surface);
  }
  .model__main {
    display: contents;
  }
  .model__title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .model__id {
    white-space: nowrap;
  }
  .model__owner {
    display: none;
  }
  .model__kind {
    display: block;
  }
  .test-tab__points-box {
    padding: 0 16px;
  }
  .test-tab__points-box summary {
    min-height: 44px;
  }
  .wallet {
    margin: 0 0 12px;
  }
  .form {
    flex: none;
    width: 100%;
    padding: 16px;
  }
  .chips {
    flex-wrap: nowrap;
    max-height: none;
    margin: 0 -16px;
    padding: 0 16px;
    overflow-x: auto;
    overflow-y: hidden;
    --chip-h: 44px;
  }
  .chip {
    flex: none;
    padding: 0 16px;
    border-radius: 22px;
    font-size: 13px;
  }
  /* three fields of the form, each 16 px from the next */
  .trio {
    flex-direction: column;
    gap: 16px;
  }
  .trio .field {
    flex: none;
  }
  .control {
    height: 44px;
  }
  .control--text {
    height: auto;
  }
  .control--mono {
    padding: 10px 12px;
  }
  .form__generate {
    height: 48px;
    font-size: 15px;
  }
}
</style>
