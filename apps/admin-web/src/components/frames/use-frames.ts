import { computed, reactive, ref, shallowRef } from "vue";
import { ApiError } from "@/api/client";
import {
  type ApplyAllResult,
  applyAllFrames,
  type FrameInfo,
  type FrameKind,
  type FrameModel,
  type FramesList,
  type FrameTarget,
  getFrame,
  listFrames,
  lockFrame,
  previewFrame,
  type PreviewResult,
  putFrame,
  resetFrame,
  rollbackFrame,
} from "@/api/frames";
import { askDiscard } from "@/components/shared/use-leave-guard";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import {
  DEFAULT_TARGET,
  defaultTargetOf,
  FRAME_MAX,
  kindsInOrder,
  loadedText,
  markerChecks,
  NO_PREVIEW_MODEL_TEXT,
  refusalText,
  SAMPLE_PROMPT,
  targetKey,
} from "./frames-model";

// What the operator asked for last survives leaving the tab (the old tab did the same); sign-out forgets it.
const memory = { provider: "", kind: "", target: "" };
export function forgetFramesMemory(): void {
  memory.provider = memory.kind = memory.target = "";
}

interface Asked {
  provider: string;
  kind: string;
  target: string;
}
// What one read of the table and the open editor answered. `kind` and `target` are what the read settled on: the one
// asked for when the provider still has it, else the first kind's own frame or default.
interface View extends Asked {
  data: FramesList | null;
  info: FrameInfo | null;
}

// The state and the actions of the Prompt frames view, the most stateful one.
//
// The editor is inline: there is always one frame in it (a kind and a target) once a provider's table is read.
// `gen` moves whenever the operator picks another frame or provider. Every answer, and every error, of a call made
// before that is dropped (`current`): the operator has moved on. While a load or a write is in flight the draft is
// read-only and every control is off, except (during a load only) the pickers, which are how the view being loaded is
// replaced. Picking another frame or provider over an unsaved draft asks first. A write that is refused with a 4xx
// shows beside the editor; anything else is a toast. A lock toggle redraws the table but keeps the typed draft.
export function useFrames() {
  const provider = ref(memory.provider);
  let kind = memory.kind;
  let target = memory.target;
  let gen = 0;

  const providers = ref<string[]>([]);
  const listed = ref("");
  const data = shallowRef<FramesList | null>(null);
  // The frame in the editor (kind and target), once its read landed.
  const shown = shallowRef<FrameTarget | null>(null);
  const info = shallowRef<FrameInfo | null>(null);
  const loaded = ref("");
  const draft = ref("");
  const loading = ref(0);
  const busy = ref(false);
  const refusal = ref("");
  const warnings = ref<string[]>([]);
  const applied = ref<ApplyAllResult | null>(null);
  const previewPrompt = ref(SAMPLE_PROMPT);
  const preview = shallowRef<PreviewResult | null>(null);

  const noFrames = computed(() => listed.value !== "" && data.value === null);
  // A read that failed (a toast says so too): the page draws the failure with Retry. Cleared by the next read.
  const failed = ref("");
  const kinds = computed(() => kindsInOrder(data.value?.kinds ?? []));
  const dirty = computed(() => shown.value !== null && draft.value !== loaded.value);
  const kindOf = (name: string): FrameKind | undefined => data.value?.kinds.find((k) => k.kind === name);
  const shownModel = computed<FrameModel | undefined>(() => {
    const t = shown.value;
    return t && t.target !== DEFAULT_TARGET ? kindOf(t.kind)?.models.find((m) => m.model === t.target) : undefined;
  });
  // Controls: all off during a load or a write; the pickers off only during a write.
  const controlsOff = computed(() => busy.value || loading.value > 0);
  const opensOff = computed(() => busy.value);
  // Save is for a change: an untouched draft would only store the text already in force, and give a model that follows
  // the default a frame of its own.
  const canSave = computed(() => !controlsOff.value && draft.value !== loaded.value);

  // What the operator is on, and what a return to the page remembers.
  function settle(p: string, k: string, t: string): void {
    provider.value = p;
    kind = k;
    target = t;
    Object.assign(memory, { provider: p, kind: k, target: t });
  }
  // The operator picked another frame or provider: answers still on their way are dropped.
  function openTarget(p: string, k: string, t: string): void {
    gen++;
    settle(p, k, t);
  }

  // Asks, then draws only if no other editor or provider was opened meanwhile. A stale failure is dropped as quietly as
  // a stale success. Resolves to the answer, or null when it was dropped.
  // `since`: the generation the operation started under. A write passes its own, so the reload that follows it is
  // dropped too if the operator moved on while the write was in flight.
  async function current<A>(ask: () => Promise<A>, draw: (answer: A) => void, since = gen): Promise<A | null> {
    const mine = since;
    loading.value++;
    try {
      let answer: A;
      try {
        answer = await ask();
      } catch (err) {
        if (mine !== gen) return null;
        throw err;
      }
      if (mine !== gen) return null;
      draw(answer);
      return answer;
    } finally {
      loading.value--;
    }
  }

  // The table and the editor's frame, read together so they are drawn together. A provider with no frames answers 404.
  // A kind the provider no longer has (or none asked for) settles on the first kind; a target it no longer has settles
  // on the kind's own frame, else its default.
  async function readView(at: Asked): Promise<View> {
    let list: FramesList;
    try {
      list = await listFrames(at.provider);
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 404) throw err;
      return { ...at, data: null, info: null };
    }
    const row = list.kinds.find((k) => k.kind === at.kind) ?? kindsInOrder(list.kinds)[0];
    if (!row) return { ...at, data: list, info: null };
    const known = at.target === DEFAULT_TARGET || row.models.some((m) => m.model === at.target);
    const settled = {
      provider: at.provider,
      kind: row.kind,
      target: row.kind === at.kind && known ? at.target : defaultTargetOf(row),
    };
    return { ...settled, data: list, info: await getFrame(settled) };
  }

  // Draws an editor (history and all). The draft is the stored text of the current version, or the typed one when the
  // same target is redrawn after a lock toggle. Everything the last editor showed is cleared.
  function drawEditor(view: Asked & { info: FrameInfo | null }, keep: { key: string; draft: string } | null): void {
    info.value = view.info;
    shown.value = view.info ? { provider: view.provider, kind: view.kind, target: view.target } : null;
    refusal.value = "";
    warnings.value = [];
    applied.value = null;
    preview.value = null;
    previewPrompt.value = SAMPLE_PROMPT;
    if (view.info) {
      loaded.value = loadedText(view.info);
      draft.value = keep && keep.key === targetKey(view) ? keep.draft : loaded.value;
    }
  }

  function drawView(view: View, keep: { key: string; draft: string } | null): void {
    data.value = view.data;
    listed.value = view.provider;
    failed.value = "";
    if (view.data && view.info) settle(view.provider, view.kind, view.target);
    drawEditor(view, view.data ? keep : null);
  }

  // Reloads the table and the editor of `at` (what was asked for last, unless a write names its own target). Answers
  // whether it drew. `keepDraft`: the open editor keeps what was typed.
  async function loadFrames(keepDraft = false, at?: Asked, since = gen): Promise<boolean> {
    const keep = keepDraft && shown.value ? { key: targetKey(shown.value), draft: draft.value } : null;
    const asked = at ?? { provider: provider.value, kind, target };
    return (
      (await current(
        () => readView(asked),
        (view) => drawView(view, keep),
        since,
      )) !== null
    );
  }

  // The editor open for a provider's table, if it is that provider's.
  const openFor = (p: string): Asked => {
    const t = shown.value;
    return t && t.provider === p ? t : { provider: p, kind: "", target: "" };
  };

  async function changing<T>(fn: () => Promise<T>): Promise<T> {
    busy.value = true;
    try {
      return await fn();
    } finally {
      busy.value = false;
    }
  }

  // A 4xx refusal (not a 401) is shown beside the editor, where the fault is, if that editor is still the one that
  // asked; anything else is rethrown for the toast.
  async function guarded<T>(fn: () => Promise<T>, stillAsked?: () => boolean): Promise<T | null> {
    const mine = gen;
    refusal.value = "";
    try {
      return await fn();
    } catch (err) {
      if (err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 401) {
        if (mine === gen && (!stillAsked || stillAsked())) refusal.value = refusalText(err);
        return null;
      }
      throw err;
    }
  }

  async function act(fn: () => Promise<void>): Promise<void> {
    try {
      await fn();
    } catch (err) {
      notify.error(errorText(err));
    }
  }

  // Follow, Roll back and picking another frame reload the editor from the server, which drops what was typed: with an
  // unsaved draft they ask first. Keep editing leaves everything as it is.
  const keepsDraft = async (): Promise<boolean> => dirty.value && !(await askDiscard());

  // A read of the table that fails is a toast and a card with Retry.
  async function reading(fn: () => Promise<void>): Promise<void> {
    try {
      await fn();
    } catch (err) {
      failed.value = errorText(err);
      notify.error(failed.value);
    }
  }

  // ---- the view ---------------------------------------------------------------------------------------------------
  async function init(names: string[], preferred: string): Promise<void> {
    providers.value = names;
    if (!provider.value || !names.includes(provider.value)) {
      provider.value = names.includes(preferred) ? preferred : (names[0] ?? "");
      kind = target = memory.kind = memory.target = "";
    }
    gen++;
    if (provider.value) await reading(async () => void (await loadFrames()));
  }

  // Reads the provider's table again (Retry).
  const reload = (): Promise<void> => reading(async () => void (await loadFrames(true)));

  // Leaving the view: nothing that is still in flight may draw.
  const dispose = (): void => void gen++;

  // Another provider, another kind or another model: over an unsaved draft the operator is asked first, and Keep
  // editing leaves everything as it is. Picking the frame that is already in the editor does nothing.
  const openProvider = (p: string): Promise<void> =>
    reading(async () => {
      if (p === provider.value && !failed.value) return;
      if (dirty.value && !(await askDiscard())) return;
      openTarget(p, "", "");
      await loadFrames();
    });

  const edit = (editKind: string, editTarget: string): Promise<void> =>
    act(async () => {
      if (shown.value?.kind === editKind && shown.value.target === editTarget) return;
      if (dirty.value && !(await askDiscard())) return;
      const at = { provider: listed.value, kind: editKind, target: editTarget };
      const before = { kind, target };
      openTarget(at.provider, at.kind, at.target);
      const mine = gen;
      try {
        await current(
          async () => ({ ...at, info: await getFrame(at) }),
          (view) => drawEditor(view, null),
        );
      } catch (err) {
        // the read failed (a toast says so): the editor still holds the frame it had
        if (mine === gen) settle(at.provider, before.kind, before.target);
        throw err;
      }
    });

  // A kind opens on its first own frame, else its default.
  const openKind = (name: string): Promise<void> => {
    const row = kindOf(name);
    return row ? edit(name, defaultTargetOf(row)) : Promise.resolve();
  };

  // ---- writes -----------------------------------------------------------------------------------------------------
  const save = (): Promise<void> =>
    act(async () => {
      const t = shown.value;
      if (!t) return;
      const text = draft.value;
      const started = gen;
      const done = await changing(() =>
        guarded(async () => {
          const saved = await putFrame(t, text);
          if (await loadFrames(false, t, started)) warnings.value = saved.warnings;
          return saved;
        }),
      );
      if (done) notify.success(done.unchanged ? "Nothing changed" : "Saved: applies to jobs created from now on");
    });

  const follow = (): Promise<void> =>
    act(async () => {
      const t = shown.value;
      if (!t) return;
      if (await keepsDraft()) return;
      const started = gen;
      const done = await changing(() =>
        guarded(async () => {
          await putFrame(t, null);
          await loadFrames(false, t, started);
          return true;
        }),
      );
      if (done) notify.success("This model follows the default");
    });

  const rollback = (versionId: number): Promise<void> =>
    act(async () => {
      const t = shown.value;
      if (!t) return;
      if (await keepsDraft()) return;
      const started = gen;
      const done = await changing(() =>
        guarded(async () => {
          const answer = await rollbackFrame(t, versionId);
          if (await loadFrames(false, t, started)) warnings.value = answer.warnings;
          return true;
        }),
      );
      if (done) notify.success("Rolled back: a new version was saved");
    });

  const resetShipped = (resetKind: string): Promise<void> =>
    act(async () => {
      const p = listed.value;
      await changing(async () => {
        const result = await resetFrame(p, resetKind);
        if (await loadFrames(false, openFor(p))) warnings.value = result.warnings;
      });
      notify.success("The default is the shipped text again");
    });

  const setLock = (lockKind: string, model: string, locked: boolean): Promise<void> =>
    act(async () => {
      const t = { provider: listed.value, kind: lockKind, target: model };
      await changing(async () => {
        try {
          await lockFrame(t, locked);
        } catch (err) {
          // The checkbox already flipped on screen: draw the table again so it shows what is stored.
          await loadFrames(true, openFor(t.provider)).catch(() => false);
          throw err;
        }
        await loadFrames(true, openFor(t.provider));
      });
      notify.success(locked ? "Locked: apply to all keeps this model" : "Unlocked");
    });

  const applyAll = (): Promise<void> =>
    act(async () => {
      const t = shown.value;
      if (!t) return;
      const text = draft.value;
      const started = gen;
      let drew = false;
      const answer = await changing(() =>
        guarded(async () => {
          const result = await applyAllFrames(t.provider, t.kind, text);
          drew = await loadFrames(false, t, started);
          if (drew) warnings.value = result.warnings;
          return result;
        }),
      );
      if (!answer) return;
      notify.success(`Applied to ${answer.applied.length} model(s)`);
      // The operator moved on: the editor that asked is gone, so there is no summary to show.
      if (drew) applied.value = answer;
    });

  // The model a preview is made for: the open model, or for a default the first model of the kind.
  const previewModel = (t: FrameTarget): string | undefined =>
    t.target === DEFAULT_TARGET ? kindOf(t.kind)?.models[0]?.model : t.target;

  // The answer belongs to the editor and the draft that asked: another editor, or another draft, drops it.
  const previewNow = (): Promise<void> =>
    act(async () => {
      const t = shown.value;
      if (!t) return;
      const model = previewModel(t);
      if (!model) {
        notify.error(NO_PREVIEW_MODEL_TEXT);
        return;
      }
      const text = draft.value;
      const mine = gen;
      preview.value = null;
      const stillAsked = () => mine === gen && draft.value === text;
      const answer = await guarded(() => previewFrame(model, previewPrompt.value, text), stillAsked);
      if (!answer || !stillAsked()) return;
      preview.value = answer;
      warnings.value = answer.warnings;
    });

  // The preview kept up with the draft as it is typed: the same call, with no refusal and no toast. A draft that
  // visibly breaks a rule (a marker not in once, over the length, a blank sample prompt) has nothing to show and is not
  // sent: the editor says which rule. The answer belongs to the frame, the draft and the sample prompt that asked.
  const previewQuiet = async (): Promise<void> => {
    const t = shown.value;
    if (!t || controlsOff.value) return;
    const model = previewModel(t);
    if (!model) return;
    const text = draft.value;
    const prompt = previewPrompt.value;
    if (!markerChecks(text).ok || text.length > FRAME_MAX || !prompt.trim()) {
      preview.value = null;
      return;
    }
    const mine = gen;
    const stillAsked = () => draft.value === text && previewPrompt.value === prompt && mine === gen;
    try {
      const answer = await previewFrame(model, prompt, text);
      if (!stillAsked()) return;
      preview.value = answer;
      warnings.value = answer.warnings;
    } catch {
      if (stillAsked()) preview.value = null;
    }
  };

  // The inputs of the editor are bound through these, so the components never assign into the controller they were given.
  const setDraft = (text: string): void => void (draft.value = text);
  const setPreviewPrompt = (text: string): void => void (previewPrompt.value = text);

  return reactive({
    setDraft,
    setPreviewPrompt,
    provider,
    providers,
    failed,
    kinds,
    dirty,
    listed,
    data,
    shown,
    info,
    draft,
    loaded,
    loading,
    busy,
    refusal,
    warnings,
    applied,
    previewPrompt,
    preview,
    noFrames,
    shownModel,
    controlsOff,
    opensOff,
    canSave,
    kindOf,
    init,
    dispose,
    openProvider,
    openKind,
    reload,
    edit,
    save,
    follow,
    rollback,
    resetShipped,
    setLock,
    applyAll,
    previewNow,
    previewQuiet,
  });
}

export type FramesController = ReturnType<typeof useFrames>;
