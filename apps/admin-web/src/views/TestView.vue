<script setup lang="ts">
// The Test tab calls the PUBLIC routes with the API key typed here. The tab is kept alive (App.vue), so a running job,
// the loaded models and the form survive tab switches; the script block also gives KeepAlive the name TestView.
// Where things are kept: the form in localStorage, the images in IndexedDB, the key in sessionStorage only after a
// successful model load (see lib/test-form.ts, lib/test-files.ts and components/test/test-key.ts).
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { getOptions, type PublicModel, type Template } from "@/api/test";
import TestImages from "@/components/test/TestImages.vue";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import TestResult from "@/components/test/TestResult.vue";
import {
  capital,
  defaultRatioOf,
  durationsOf,
  durationsPhrase,
  modelMeta,
  ratiosOf,
  resolutionsOf,
  stylesOf,
  takesImages,
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
import { byProviderOrder, providerClass } from "@/lib/providers";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

const auth = useAuthStore();
const scope = useScopeStore();
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

// The provider strip (one provider at a time, shared with Prompt frames) picks the provider; the picker shows its models
// the key may use, then its models the admin paused (from the admin catalog: the key's list leaves them out), which
// can never be chosen. Until a provider is picked, the one of the saved model, else the first that has a model.
const provider = computed(() => scope.only);
interface Choice {
  model: PublicModel;
  paused: boolean;
}
const choices = computed<Choice[]>(() => {
  const mine = models.value.filter((m) => m.owned_by === provider.value);
  const paused = (scope.models ?? []).filter(
    (m) => m.owned_by === provider.value && m.disabled === true && !mine.some((x) => x.id === m.id),
  );
  return [
    ...mine.map((model) => ({ model, paused: false })),
    ...paused.map((m) => ({ model: { ...m, kind: String(m.kind ?? "") } as PublicModel, paused: true })),
  ];
});
const groups = computed(() => {
  const byKind = new Map<string, Choice[]>();
  for (const c of choices.value) byKind.set(c.model.kind, [...(byKind.get(c.model.kind) ?? []), c]);
  // video first, as drawn
  return [...byKind.keys()]
    .sort((a, b) => Number(b === "video") - Number(a === "video") || a.localeCompare(b))
    .map((kind) => ({ kind, models: byKind.get(kind) ?? [] }));
});
watch(
  models,
  (list) => {
    if (!list.length || list.some((m) => m.owned_by === scope.only)) return;
    const saved = list.find((m) => m.id === form.model);
    scope.only = saved?.owned_by ?? byProviderOrder([...new Set(list.map((m) => m.owned_by))])[0] ?? "";
  },
  { immediate: true },
);
// Another provider: the model chosen for the last one goes, the new one's first model is picked (the form never sends
// a model the picker does not show).
watch(provider, (p) => {
  if (!chosen.value || chosen.value.owned_by === p) return;
  const first = choices.value.find((c) => !c.paused);
  if (first) void pick(first.model);
  else {
    chosen.value = null;
    pickLatest.invalidate();
    ratios.value = [];
    styles.value = [];
    templates.value = [];
  }
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
// A model takes a ratio or a style only from a list (its own, or its provider's options): with no list the field is not
// drawn and not sent (meta takes neither, vibes names its ratios), so the form never offers what the provider refuses.
const hasRatio = computed(() => ratiosOf(chosen.value).length > 0 || ratios.value.length > 0);
const styleList = computed(() =>
  styles.value.length ? styles.value : stylesOf(chosen.value).map((value) => ({ value, label: value })),
);
const hasStyle = computed(() => chosen.value?.kind === "image" && styleList.value.length > 0);
const hasImages = computed(() => takesImages(chosen.value));
const styleChoices = computed(() =>
  form.style && !styleList.value.some((s) => s.value === form.style)
    ? [...styleList.value, { value: form.style, label: form.style }]
    : styleList.value,
);
// The selects' lists, "Default" (nothing sent) first.
const DEFAULT_OPTION = { value: "", label: "Default" };
const durationOptions = computed(() => [
  DEFAULT_OPTION,
  ...durationChoices.value.map((d) => ({ value: d, label: `${d} s` })),
]);
const ratioOptions = computed(() => [DEFAULT_OPTION, ...ratioChoices.value.map((r) => ({ value: r, label: r }))]);
const styleOptions = computed(() => [DEFAULT_OPTION, ...styleChoices.value]);
// "Default" sends none: vibes then renders 480p.
const resolutionOptions = computed(() => [
  DEFAULT_OPTION,
  ...withSaved(resolutionsOf(chosen.value), form.resolution).map((r) => ({ value: r, label: r })),
]);

async function pick(model: PublicModel): Promise<void> {
  const ticket = pickLatest.next();
  chosen.value = model;
  form.model = model.id;
  // the strip shows the provider of the model the form sends
  scope.only = model.owned_by;
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
  // a field the model does not take is not drawn, and a value saved for another model is not sent either
  const images = hasImages.value;
  const sent = {
    ...form,
    ratio: hasRatio.value ? form.ratio : "",
    style: hasStyle.value ? form.style : "",
    images: images ? form.images : "",
  };
  const picked = images ? files.value : [];
  await generate.start(model, async () => {
    const uris = await Promise.all(picked.map((f) => blobToDataUri(f.blob)));
    return buildBody(model, sent, uris);
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
        <p v-if="!models.length" class="models__empty" data-testid="test-models-empty">No models loaded.</p>
        <p v-else-if="!groups.length" class="models__empty" data-testid="test-models-none">
          This key may use no {{ provider }} model.
        </p>
        <!-- picker C: the models as chips, a row per kind under its small label; the chosen one tinted with a check -->
        <div v-for="g in groups" :key="g.kind" class="models__group">
          <h2>{{ capital(g.kind) }}</h2>
          <div class="chips2" role="group" :aria-label="`${capital(g.kind)} models`">
            <button
              v-for="c in g.models"
              :key="c.model.id"
              type="button"
              class="mchip"
              :class="[providerClass(c.model.owned_by), { 'is-active': chosen?.id === c.model.id }]"
              :aria-pressed="chosen?.id === c.model.id"
              :disabled="c.paused"
              :title="c.model.id"
              data-testid="test-model"
              @click="pick(c.model)"
            >
              <span v-if="chosen?.id === c.model.id" class="mchip__check" aria-hidden="true">✓</span
              >{{ titleOf(c.model) }}<small v-if="c.paused" class="mchip__paused">Paused</small>
            </button>
          </div>
        </div>
        <p v-if="chosen" class="models__chosen" data-testid="test-chosen">
          Sends <b>{{ titleOf(chosen) }}</b
          ><template v-if="modelMeta(chosen)"> · {{ modelMeta(chosen) }}</template> ·
          <span class="models__id">{{ chosen.id }}</span>
        </p>
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
          <!-- the design's selects, drawn by us (FilterSelect's field); a model that names no list of lengths has no
               Duration at all, and none is sent -->
          <FilterSelect
            v-if="durations.length"
            class="field"
            variant="field"
            block
            label="Duration"
            :model-value="form.duration"
            :options="durationOptions"
            @update:model-value="setField('duration', String($event))"
          />
          <FilterSelect
            v-if="hasRatio"
            class="field"
            variant="field"
            block
            label="Ratio"
            :model-value="form.ratio"
            :options="ratioOptions"
            @update:model-value="setField('ratio', String($event))"
          />
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
          <FilterSelect
            v-if="resolutionsOf(chosen).length"
            class="field"
            variant="field"
            block
            label="Resolution"
            :model-value="form.resolution"
            :options="resolutionOptions"
            @update:model-value="setField('resolution', String($event))"
          />
          <FilterSelect
            v-if="hasStyle"
            class="field"
            variant="field"
            block
            label="Style"
            :model-value="form.style"
            :options="styleOptions"
            @update:model-value="setField('style', String($event))"
          />
        </div>

        <div v-if="hasImages" class="field">
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

        <TestImages v-if="hasImages" :files="files" :note="shownNote" @add="onAdd" @remove="onRemove" />

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
/* beside the buttons the note is as high as they are and centred in that height, so its line sits on their centre
   line (the canvas centres it in the whole row, label included, which puts it about 6 px higher) */
.key__note {
  flex: 1 1 240px;
  display: flex;
  align-items: center;
  align-self: flex-end;
  height: 36px;
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
  flex: 1 1 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 20px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
}
.models__empty {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.models__group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
/* the kind: a small label over its chips */
.models__group h2 {
  margin: 0;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--rl-muted);
}
.chips2 {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.mchip {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 16px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
  line-height: normal;
  white-space: nowrap;
  cursor: pointer;
}
.mchip:hover:not(:disabled) {
  border-color: var(--rl-control);
}
/* the chosen model: the provider's tint, a border in its tone, a check */
.mchip.is-active {
  border-color: color-mix(in oklab, var(--pc) 40%, transparent);
  background: var(--ps);
  font-weight: 500;
}
.mchip__check {
  color: var(--pi);
  font-size: 12px;
  font-weight: 700;
}
.mchip:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.mchip__paused {
  font-size: 11px;
  color: var(--rl-muted);
}
/* what the chosen model sends: quiet text under the chips */
.models__chosen {
  margin: 0;
  padding-top: 10px;
  border-top: 1px dashed var(--rl-line-strong);
  font-size: 12px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.models__chosen b {
  font-weight: 500;
  color: var(--rl-fg);
}
.models__id {
  font-family: var(--rl-mono);
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
    height: auto;
  }
  .grid {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
  }
  .models {
    flex: none;
    padding: 12px 16px;
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
    height: auto;
  }
  .grid {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    gap: 16px;
  }
  .models {
    flex: none;
    padding: 16px;
  }
  /* a touch target: 40 px chips, still in a wrap */
  .mchip {
    height: 40px;
    border-radius: 20px;
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
