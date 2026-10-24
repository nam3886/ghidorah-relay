<script setup lang="ts">
// The Test tab calls the PUBLIC routes with the API key typed here. The tab is kept alive (App.vue), so a running job,
// the loaded models and the form survive tab switches; the script block also gives KeepAlive the name TestView.
// Where things are kept: the form in localStorage, the images in IndexedDB, the key in sessionStorage only after a
// successful model load (see lib/test-form.ts, lib/test-files.ts and components/test/test-key.ts).
import { computed, inject, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { getJob as getJobDetail, type JobDetail } from "@/api/jobs-detail";
import { getOptions, type PublicModel, type Template } from "@/api/test";
import TestImages from "@/components/test/TestImages.vue";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import FilterSelect from "@/components/shared/FilterSelect.vue";
import IconButton from "@/components/shared/IconButton.vue";
import TestResult from "@/components/test/TestResult.vue";
import {
  defaultRatioOf,
  durationsOf,
  durationsPhrase,
  maxOutputsOf,
  modelMeta,
  optionsCheck,
  outputsHint,
  pointsFor,
  ratiosOf,
  resolutionsOf,
  stylesOf,
  takesImages,
  titleOf,
  upstreamFor,
  walletRows,
  walletStrip,
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

// The Model block draws one row per provider the key may use (Test.dc.html), in the drawn provider order: its models
// (video first), then its models the admin paused (from the admin catalog: the key's list leaves them out), which can
// never be chosen. `scope.only` is the provider of the chosen model, shared with Prompt frames: until a model is
// picked, the one of the saved model, else the first that has a model; a provider chosen on Prompt frames picks its
// first model here.
const provider = computed(() => scope.only);
interface Choice {
  model: PublicModel;
  paused: boolean;
}
function choicesOf(p: string): Choice[] {
  const mine = models.value.filter((m) => m.owned_by === p);
  const paused = (scope.models ?? []).filter(
    (m) => m.owned_by === p && m.disabled === true && !mine.some((x) => x.id === m.id),
  );
  // video first, as drawn; a paused model sits with its kind
  return [
    ...mine.map((model) => ({ model, paused: false })),
    ...paused.map((m) => ({ model: { ...m, kind: String(m.kind ?? "") } as PublicModel, paused: true })),
  ].sort((a, b) => Number(b.model.kind === "video") - Number(a.model.kind === "video"));
}
const choices = computed<Choice[]>(() => choicesOf(provider.value));
const rows = computed(() =>
  byProviderOrder([...new Set(models.value.map((m) => m.owned_by))]).map((p) => ({
    provider: p,
    choices: choicesOf(p),
  })),
);
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
// the wallet beside the key: its name, what is left and today's spend
const strip = computed(() => walletStrip(points.value));
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

// The form's foot: how many outputs, what the job costs at the catalog's prices and what the upstream takes for it.
// A model returns at most `maxOut` (the stepper is drawn only above 1).
const maxOut = computed(() => maxOutputsOf(chosen.value));
const outputs = computed(() => Math.min(maxOut.value, Math.max(1, Math.floor(Number(form.count) || 1))));
const cost = computed(() => pointsFor(chosen.value, form.duration, String(outputs.value)));
const costText = computed(() => {
  const n = `${outputs.value} ${outputs.value === 1 ? "output" : "outputs"}`;
  return cost.value === null ? n : `${n} · `;
});
// the admin catalog's entry of the chosen model (it alone carries the upstream cost)
const adminEntry = computed(() => (scope.models ?? []).find((m) => m.id === chosen.value?.id) ?? null);
const upstream = computed(() => upstreamFor(adminEntry.value, form.duration, outputs.value));
const unit = computed(() => {
  const u = adminEntry.value?.cost_unit;
  return typeof u === "string" ? u : null;
});
// the Outputs stepper: one fewer, one more (never under 1 or over the model's most)
function step(by: number): void {
  setField("count", String(Math.min(maxOut.value, Math.max(1, outputs.value + by))));
}
const extraCheck = computed(() => optionsCheck(form.options));
// the prompt's counter: what is typed against the model's limit, when the catalog names one
const promptMax = computed(() => {
  const n = (chosen.value?.limits as { max_prompt_chars?: unknown } | undefined)?.max_prompt_chars;
  return typeof n === "number" ? n : null;
});
const thousands = (n: number): string => n.toLocaleString("en-US");

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
    // a model that returns one output is never sent a count
    count: maxOut.value > 1 ? String(outputs.value) : "",
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

// The timeline of the shown job comes from the ADMIN job detail (it stores the send time and the account; the public
// status does not): read again whenever the job's status or stage changes, the newest read winning. A failed read
// leaves the timeline out; the Result card still draws the public answer.
const detail = shallowRef<JobDetail | null>(null);
const detailLatest = useLatest();
watch(
  () => (job.value ? `${job.value.id}/${job.value.status}/${job.value.stage ?? ""}` : ""),
  async (at) => {
    const ticket = detailLatest.next();
    if (!at) {
      detail.value = null;
      return;
    }
    const id = job.value?.id ?? "";
    if (detail.value?.id !== id) detail.value = null;
    try {
      const read = await getJobDetail(id);
      if (detailLatest.isCurrent(ticket)) detail.value = read;
    } catch {
      /* the timeline is a convenience: the public answer is drawn without it */
    }
  },
);

// Sign-out and a 401 end the running job and drop the key and the models.
const offSignOut = auth.onSignOut(() => {
  generate.stop();
  session.reset();
});
onBeforeUnmount(() => {
  generate.stop();
  pickLatest.invalidate();
  detailLatest.invalidate();
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
  <section class="test-tab" :class="{ 'is-tablet': isTablet, 'is-phone': isPhone, 'is-compact': isTablet || isPhone }">
    <div v-if="!isTablet && !isPhone" class="tt-head">
      <h1 class="test-tab__title">Test</h1>
      <p class="test-tab__subtitle">Send a real job with a key. It is charged and delivered like any other job.</p>
    </div>
    <template v-else>
      <h1 class="sr-only">Test</h1>
      <p v-if="isTablet" class="test-tab__subtitle">
        Send a real job with a key. It is charged and delivered like any other job. The form and the result stack at
        this width.
      </p>
    </template>

    <div class="tt-grid">
      <section class="form" aria-labelledby="test-form-title">
        <!-- the key: the box, Load models, Forget key and Clear form; the wallet limits once a load answered -->
        <div class="blk key" aria-label="API key" role="group">
          <label for="test-api-key" class="lbl">API key</label>
          <div class="key__row">
            <input
              id="test-api-key"
              v-model="typedKey"
              class="ctl ctl--mono key__input"
              type="password"
              autocomplete="off"
              placeholder="rk-..."
              spellcheck="false"
              @keyup.enter="loadModels"
            />
            <div v-if="strip" class="key__wallet" data-testid="test-wallet">
              <span class="key__wallet-name">{{ strip.name }}</span>
              <span class="num key__wallet-left"
                >{{ strip.left }}<span v-if="/\d/.test(strip.left)" class="key__wallet-unit"> pts</span></span
              >
              <span class="num key__wallet-spent">{{ strip.spent }}</span>
            </div>
            <button
              type="button"
              class="btn key__load"
              :aria-busy="loadingModels"
              :class="{ 'is-busy': loadingModels }"
              @click="loadModels"
            >
              Load models
            </button>
            <span class="key__icons">
              <IconButton icon="unlink" label="Forget key" @click="session.reset" />
              <IconButton icon="reset" label="Clear form" placement="top-end" @click="clearForm" />
            </span>
          </div>
          <span class="help">The key lives only in this tab’s session storage.</span>
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
                <dd>{{ w.value }}</dd>
              </div>
            </dl>
            <pre v-else class="test-tab__points" aria-label="Points">{{ points }}</pre>
          </details>
        </div>

        <!-- the models: a row per provider, the chips scrolling sideways in it -->
        <fieldset class="blk models" data-testid="test-models">
          <legend class="lbl">Model</legend>
          <p v-if="!models.length" class="models__empty" data-testid="test-models-empty">No models loaded.</p>
          <div v-for="r in rows" :key="r.provider" class="mrow" :class="providerClass(r.provider)">
            <span class="mrow__head" data-testid="test-provider"
              ><span class="mrow__dot" aria-hidden="true" />{{ r.provider }}</span
            >
            <ArrowScroller
              class="mrow__chips"
              :label="`${r.provider} models`"
              :count="r.choices.length"
              :active-key="chosen?.id"
            >
              <button
                v-for="c in r.choices"
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
                {{ titleOf(c.model) }}<small v-if="c.paused" class="mchip__paused">Paused</small>
              </button>
            </ArrowScroller>
          </div>
          <div class="models__pick">
            <h2 id="test-form-title">{{ chosen ? titleOf(chosen) : "Pick a model" }}</h2>
            <span v-if="chosen" class="form__info">
              {{ chosen.kind }} · <span class="num">{{ infoTail }}</span>
            </span>
          </div>
          <p v-if="chosen" class="models__chosen" data-testid="test-chosen">
            Sends <b>{{ titleOf(chosen) }}</b
            ><template v-if="modelMeta(chosen)"> · {{ modelMeta(chosen) }}</template> ·
            <span class="models__id">{{ chosen.id }}</span>
          </p>
        </fieldset>

        <!-- the prompt and the options: one card of their own on a phone (TestPhone, "Prompt and options") -->
        <div class="pgroup" data-testid="test-prompt-card">
          <h2 v-if="isPhone" class="pgroup__title">Prompt and options</h2>
          <!-- the prompt, its templates and the title -->
          <div class="blk">
            <div class="blk__head">
              <label for="test-prompt" class="lbl">Prompt</label>
              <span v-if="promptMax" class="help num"
                >{{ thousands(form.prompt.length) }} / {{ thousands(promptMax) }}</span
              >
            </div>
            <textarea
              id="test-prompt"
              v-model="form.prompt"
              :rows="isPhone ? 5 : 3"
              class="ctl ctl--area"
              @input="persist"
            />
            <ArrowScroller v-if="templates.length" label="Prompt templates" :active-key="null">
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
            </ArrowScroller>
            <div class="field">
              <span class="blk__head"
                ><label for="test-title" class="lbl">Title</label><span class="help">optional</span></span
              >
              <input id="test-title" v-model="form.title" type="text" class="ctl" @input="persist" />
            </div>
          </div>

          <!-- the options: Duration and Ratio as segments, the Outputs stepper, Resolution and Style -->
          <div class="blk opts">
            <div v-if="durations.length" class="opt">
              <span class="lbl" aria-hidden="true">Duration</span>
              <ArrowScroller class="seg" label="Duration" :active-key="form.duration">
                <button
                  v-for="o in durationOptions"
                  :key="o.value"
                  type="button"
                  class="seg__btn num"
                  :data-value="o.value"
                  :aria-pressed="form.duration === o.value"
                  @click="setField('duration', String(o.value))"
                >
                  {{ o.label }}
                </button>
              </ArrowScroller>
            </div>
            <div v-if="hasRatio" class="opt">
              <span class="lbl" aria-hidden="true">Ratio</span>
              <ArrowScroller class="seg" label="Ratio" :active-key="form.ratio">
                <button
                  v-for="o in ratioOptions"
                  :key="o.value"
                  type="button"
                  class="seg__btn num"
                  :data-value="o.value"
                  :aria-pressed="form.ratio === o.value"
                  @click="setField('ratio', String(o.value))"
                >
                  {{ o.label }}
                </button>
              </ArrowScroller>
            </div>
            <div class="opt">
              <span id="test-out" class="lbl">Outputs</span>
              <span
                v-if="maxOut <= 1"
                class="one-out num"
                aria-describedby="test-out-hint"
                data-testid="test-one-output"
                >1 output</span
              >
              <div v-else class="stepper" role="group" aria-labelledby="test-out" aria-describedby="test-out-hint">
                <button type="button" aria-label="One output fewer" :disabled="outputs <= 1" @click="step(-1)">
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.4"
                    stroke-linecap="round"
                    aria-hidden="true"
                  >
                    <path d="M5 12h14" />
                  </svg>
                </button>
                <input
                  id="test-count"
                  class="num"
                  type="number"
                  min="1"
                  placeholder="1"
                  aria-label="Outputs"
                  :value="form.count"
                  @input="setField('count', ($event.target as HTMLInputElement).value)"
                />
                <button type="button" aria-label="One output more" :disabled="outputs >= maxOut" @click="step(1)">
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.4"
                    stroke-linecap="round"
                    aria-hidden="true"
                  >
                    <path d="M5 12h14M12 5v14" />
                  </svg>
                </button>
              </div>
              <span id="test-out-hint" class="help">{{ outputsHint(chosen) }}</span>
            </div>
            <FilterSelect
              v-if="resolutionsOf(chosen).length"
              class="opt opt--select"
              variant="field"
              block
              label="Resolution"
              :model-value="form.resolution"
              :options="resolutionOptions"
              @update:model-value="setField('resolution', String($event))"
            />
            <FilterSelect
              v-if="hasStyle"
              class="opt opt--select"
              variant="field"
              block
              label="Style"
              :model-value="form.style"
              :options="styleOptions"
              @update:model-value="setField('style', String($event))"
            />
          </div>

          <!-- the reference images: the attached tiles, and the https links -->
          <div v-if="hasImages" class="blk">
            <TestImages :files="files" :note="shownNote" @add="onAdd" @remove="onRemove" />
            <div class="field">
              <label for="test-refs" class="lbl">Reference image URLs <span class="opt-word">one per line</span></label>
              <textarea
                id="test-refs"
                v-model="form.images"
                rows="2"
                placeholder="https://..."
                class="ctl ctl--area ctl--mono"
                @input="persist"
              />
            </div>
          </div>

          <!-- the extra options, checked as they are typed -->
          <div class="blk">
            <div class="blk__head">
              <label for="test-extra" class="lbl">Extra options (JSON) <span class="opt-word">optional</span></label>
              <span class="help">Sent as the request’s options, as written</span>
            </div>
            <textarea
              id="test-extra"
              v-model="form.options"
              rows="4"
              spellcheck="false"
              class="ctl ctl--area ctl--mono"
              :aria-invalid="extraCheck && !extraCheck.ok ? 'true' : undefined"
              aria-describedby="test-extra-check"
              @input="persist"
            />
            <span
              v-if="extraCheck"
              id="test-extra-check"
              class="json-check"
              :class="extraCheck.ok ? 'is-ok' : 'is-bad'"
              role="status"
              >{{ extraCheck.text }}</span
            >
          </div>
        </div>

        <!-- on a phone the foot is the Send bar at the bottom of the screen, after the result (TestPhone) -->
        <div class="form__foot" data-testid="test-send-bar">
          <span class="cost">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <ellipse cx="12" cy="6" rx="8" ry="3" />
              <path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6" />
              <path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />
            </svg>
            <span class="num"
              >{{ costText }}<b v-if="cost !== null">{{ thousands(cost) }} {{ cost === 1 ? "point" : "points" }}</b
              ><template v-if="upstream"> · {{ upstream }}</template></span
            >
          </span>
          <button type="button" class="send" :disabled="!chosen || submitting" @click="generateNow">
            Send test job
          </button>
        </div>
      </section>

      <TestResult
        :status="status"
        :job="job"
        :kind="jobKind"
        :loaded="models.length > 0"
        :detail="detail?.id === job?.id ? detail : null"
        :unit="unit"
      />
    </div>
  </section>
</template>

<style scoped>
.test-tab {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.tt-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.test-tab__title {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.test-tab__subtitle {
  margin: 0;
  color: var(--rl-muted);
}
.is-compact .test-tab__subtitle {
  font-size: 13px;
  line-height: 1.55;
}
.tt-grid {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 20px;
}

/* the form card: blocks ruled off from each other, the foot on the head fill */
.form {
  flex: 999 1 620px;
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.blk {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  margin: 0;
  padding: 20px 24px;
  border: 0;
  border-bottom: 1px solid var(--rl-rule);
}
.blk__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px 12px;
}
.lbl {
  font-size: 13px;
  font-weight: 700;
}
.opt-word {
  font-weight: 500;
  color: var(--rl-faint);
}
.help {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.ctl {
  box-sizing: border-box;
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
}
.ctl:focus-visible {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.ctl--mono {
  font-family: var(--rl-mono);
  font-size: 13px;
}
.ctl--area {
  height: auto;
  padding: 12px 14px;
  line-height: 22px;
  resize: vertical;
}
.ctl--area.ctl--mono {
  padding: 10px 12px;
  font-size: 12px;
  line-height: 20px;
  background: var(--rl-code);
}
.ctl[aria-invalid="true"] {
  border-color: var(--rl-bad);
}

/* the key */
.key__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.key__input {
  flex: 1 1 280px;
  min-width: 0;
}
/* the wallet beside the key (Test.dc.html): name, points left, today's spend on the head fill */
.key__wallet {
  display: flex;
  align-items: center;
  gap: 10px;
  align-self: flex-start;
  max-width: 100%;
  height: 44px;
  box-sizing: border-box;
  padding: 0 14px;
  border-radius: 10px;
  background: var(--rl-head);
  border: 1px solid var(--rl-rule);
}
.key__wallet-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 700;
  color: var(--rl-brand-ink);
}
.key__wallet-left {
  font-weight: 800;
  white-space: nowrap;
}
.key__wallet-unit {
  font-size: 12px;
  font-weight: 600;
  color: var(--rl-faint);
}
.key__wallet-spent {
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  color: var(--rl-muted);
}
/* a phone (TestPhone): the wallet is one quiet line under the key's row, `Studio Saigon · 12,480 pts · −1,240 today` */
.is-phone .key__wallet {
  order: 3;
  flex: 1 1 100%;
  gap: 0;
  height: auto;
  padding: 0;
  border: 0;
  background: transparent;
}
.is-phone .key__wallet > span {
  font-size: 12px;
  font-weight: 500;
  color: var(--rl-faint);
}
.is-phone .key__wallet > span + span::before {
  content: " · ";
  white-space: pre;
}
.is-phone .key__wallet-unit {
  font-weight: 500;
}
.one-out {
  display: inline-flex;
  align-items: center;
  height: 44px;
  font-weight: 700;
}
.btn {
  box-sizing: border-box;
  height: 44px;
  padding: 0 16px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
}
.btn:hover {
  background: var(--rl-head);
}
.btn.is-busy {
  opacity: 0.7;
  cursor: progress;
}
.key__icons {
  display: flex;
  gap: 4px;
}
.test-tab__points-box {
  align-self: flex-start;
  box-sizing: border-box;
  min-width: 260px;
  max-width: 100%;
  padding: 0 14px;
  border: 1px solid var(--rl-rule);
  border-radius: 10px;
  background: var(--rl-head);
}
.test-tab__points-box summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 40px;
  font-size: 12px;
  font-weight: 700;
  color: var(--rl-brand-ink);
  list-style: none;
  cursor: pointer;
}
.test-tab__points-box summary::-webkit-details-marker {
  display: none;
}
.test-tab__points-box[open] summary svg {
  transform: rotate(180deg);
}
.wallet {
  display: flex;
  flex-direction: column;
  margin: 0 0 10px;
  font-size: 13px;
}
.wallet__row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  border-top: 1px solid var(--rl-rule);
}
.wallet dt {
  color: var(--rl-faint);
}
.wallet dd {
  margin: 0;
  font-family: var(--rl-mono);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  text-align: right;
}
.test-tab__points {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--rl-muted);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* the models: a row per provider, its name in its own colour, the chips neutral (a model name is not coloured) */
.models {
  gap: 10px;
}
.models legend {
  float: left;
  width: 100%;
  margin-bottom: 10px;
  padding: 0;
}
.models__empty {
  margin: 0;
  font-size: 13px;
  color: var(--rl-muted);
}
.mrow {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.mrow__head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: none;
  width: 76px;
  min-height: 34px;
  font-size: 13px;
  font-weight: 700;
  color: var(--pi);
  overflow-wrap: anywhere;
}
.mrow__dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--pc);
}
.mrow__chips {
  flex: 1 1 0;
}
.mchip {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-body);
  font: inherit;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
}
.mchip:hover:not(:disabled) {
  border-color: var(--rl-faint);
}
/* the chosen model, as drawn: filled with its provider's colour */
.mchip.is-active,
.mchip.is-active:hover:not(:disabled) {
  border-color: var(--pi);
  background: var(--pi);
  color: var(--rl-surface);
  font-weight: 700;
}
.mchip:disabled {
  background: var(--rl-head);
  color: var(--rl-faint);
  cursor: not-allowed;
}
.mchip__paused {
  padding: 0 6px;
  border-radius: 999px;
  background: var(--rl-neutral-bg);
  color: var(--rl-neutral);
  font-size: 11px;
  font-weight: 700;
}
.models__pick {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 10px;
  margin-top: 4px;
  padding-top: 12px;
  border-top: 1px dashed var(--rl-line-strong);
}
.models__pick h2 {
  margin: 0;
  font-size: 14px;
  line-height: 20px;
  font-weight: 700;
  overflow-wrap: anywhere;
}
.form__info {
  font-size: 12px;
  color: var(--rl-muted);
}
.models__chosen {
  margin: 0;
  font-size: 12px;
  color: var(--rl-muted);
  overflow-wrap: anywhere;
}
.models__chosen b {
  font-weight: 600;
  color: var(--rl-fg);
}
.models__id {
  font-family: var(--rl-mono);
}

/* the templates: one line each, ending in "…" (a click puts the whole text in the prompt box) */
.chip {
  max-width: 240px;
  height: 32px;
  padding: 0 12px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-body);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.chip:hover {
  background: var(--rl-head);
}
/* a template can run to thousands of characters: its hint scrolls past a few lines */
:global(.el-popper.test-chip-tip) {
  max-width: 420px;
  max-height: 240px;
  overflow-y: auto;
}

/* the options */
.opts {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 16px 28px;
}
.opt {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}
.opt--select {
  flex: 0 1 200px;
}
/* a segmented control: the track and the chosen segment raised on it */
.seg {
  max-width: 100%;
}
.seg :deep(.asc__row) {
  gap: 2px;
  padding: 3px;
  border-radius: 12px;
  background: var(--rl-track);
}
.seg__btn {
  min-width: 56px;
  height: 38px;
  padding: 0 12px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--rl-muted);
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.seg__btn[aria-pressed="true"] {
  background: var(--rl-seg-on);
  color: var(--rl-fg);
  font-weight: 700;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.1);
}
.stepper {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  align-self: flex-start;
  height: 44px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
}
.stepper button {
  display: grid;
  place-items: center;
  width: 44px;
  height: 100%;
  padding: 0;
  border: 0;
  background: var(--rl-head);
  color: var(--rl-body);
  cursor: pointer;
}
.stepper button:disabled {
  color: var(--rl-faint);
  cursor: not-allowed;
}
.stepper input {
  width: 48px;
  height: 100%;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
  font-weight: 700;
  text-align: center;
  -moz-appearance: textfield;
  appearance: textfield;
}
.stepper input::-webkit-outer-spin-button,
.stepper input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
.stepper:focus-within {
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}

.json-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  overflow-wrap: anywhere;
}
.json-check.is-ok {
  color: var(--rl-ok);
}
.json-check.is-bad {
  color: var(--rl-bad);
}

/* the foot: what the job costs, and Send */
.form__foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 16px 24px;
  background: var(--rl-head);
}
.cost {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--rl-body);
}
.cost b {
  font-weight: 800;
  color: var(--rl-fg);
}
.send {
  margin-left: auto;
  height: 44px;
  padding: 0 22px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  white-space: nowrap;
  cursor: pointer;
  box-shadow:
    0 1px 2px rgba(16, 24, 40, 0.12),
    inset 0 -1px 0 rgba(0, 0, 0, 0.12);
}
.send:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* the prompt and options group: no box of its own above a phone */
.pgroup {
  display: contents;
}

/* a tablet and a phone: the form and the result stack; the foot stays at the bottom of the screen */
.is-compact .tt-grid {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
}
.is-compact .form {
  flex: none;
  width: 100%;
  overflow: visible;
}
.is-compact .form__foot {
  position: sticky;
  bottom: 0;
  z-index: 2;
  border-top: 1px solid var(--rl-rule);
  border-radius: 0 0 16px 16px;
  background: var(--rl-surface);
}
.is-phone .test-tab,
.is-phone .tt-grid {
  gap: 14px;
}
.is-phone .blk {
  padding: 16px;
}
/* a phone (TestPhone): the key, the models, the prompt and options and the result are cards of their own, and the
   Send bar sits at the bottom of the screen after them all, edge to edge */
.is-phone .form {
  display: contents;
}
.is-phone .blk.key,
.is-phone .blk.models,
.is-phone .pgroup {
  /* a fieldset and a flex item would grow to their chips' width: they keep the screen's */
  min-width: 0;
  border: 0;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.is-phone .pgroup {
  display: block;
  padding: 16px 0 4px;
}
.is-phone .pgroup .blk {
  padding: 4px 16px 12px;
  border-bottom: 0;
}
.pgroup__title {
  margin: 0 16px 8px;
  font-size: 16px;
  line-height: 22px;
  font-weight: 700;
}
.is-phone .form__foot {
  order: 2;
  margin: 0 -16px;
  padding: 12px 16px;
  border-radius: 0;
  border-top: 1px solid var(--rl-rule);
  box-shadow: 0 -4px 12px rgba(16, 24, 40, 0.06);
}
.is-phone .form__foot {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  padding: 12px 16px;
}
.is-phone .send {
  width: 100%;
  margin-left: 0;
}
.is-phone .key__load {
  flex: 1 1 auto;
}
.is-phone .mrow {
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
}
.is-phone .mrow__head {
  width: auto;
  min-height: 0;
}
.is-phone .mchip,
.is-phone .chip {
  height: 44px;
  padding: 0 14px;
}
.is-phone .opts {
  flex-direction: column;
  gap: 16px;
}
.is-phone .opt--select {
  flex: none;
  width: 100%;
}
.is-tablet .mchip,
.is-tablet .chip {
  height: 40px;
  padding: 0 14px;
}
</style>
