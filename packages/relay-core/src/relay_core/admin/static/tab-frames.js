'use strict';

// Prompt frames tab: the text relay sends is the customer's prompt inside one admin-written frame per model (or per
// kind: the default). The list shows who owns each model's frame, the editor saves a new version, the preview renders
// the draft without sending it, and the history rolls back. Every frame text reaches the page as text.

// `provider`, `kind` and `target` are what the operator asked for last; `listed` is the provider the table shows and
// `shown` the target the editor shows (set only when an answer is drawn). Actions take their target from `shown` and
// `listed`, never from what was asked for last. `gen` moves whenever the operator opens another editor or provider,
// and every answer is drawn only if it has not moved since the question was asked (`current`).
const framesView = { provider: '', listed: '', data: null, kind: '', target: '', shown: null, info: null, loaded: '', keep: null, gen: 0, loading: 0, busy: false };
const SOURCE_TONE = { own: 'blue', default: 'gray' };

function framesPath(t, tail) {
    return '/frames/' + encodeURIComponent(t.provider) + '/' + encodeURIComponent(t.kind) + '/' + encodeURIComponent(t.target) + (tail || '');
}

function versionLine(v) {
    if (!v) return 'no version';
    return 'version ' + v.id + ' by ' + v.created_by + ', ' + fmtTime(v.created_ms) + (v.restored_from ? ' (restored from ' + v.restored_from + ')' : '');
}

function lockLine(model) {
    return model.lock ? (model.lock.locked ? 'locked' : 'unlocked') + ' by ' + model.lock.created_by + ', ' + fmtTime(model.lock.created_ms) : 'never toggled';
}

function kindOf(name) { return framesView.data.kinds.find((k) => k.kind === name); }

const FRAME_COLUMNS = [
    { key: 'model', label: 'Model', align: 'left', cell: (m) => '<span class="font-mono text-xs">' + esc(m.model) + '</span>' },
    { key: 'source', label: 'Source', align: 'left', cell: (m) => badge(m.source, SOURCE_TONE[m.source] || 'gray') },
    { key: 'version', label: 'Effective version', align: 'left', cell: (m) => '<span class="text-xs">' + esc(versionLine(m.effective)) + '</span>' },
    { key: 'lock', label: 'Keep own', align: 'left', cell: (m) => '<label class="inline-flex items-center gap-2 text-xs"><input type="checkbox" data-change="frameLock" data-kind="' + esc(m.kind) + '" data-model="' + esc(m.model) + '"' + (m.locked ? ' checked' : '') + '> keep own</label><div class="text-xs text-muted-foreground">' + esc(lockLine(m)) + '</div>' },
    { key: 'actions', label: '', align: 'right', cell: (m) => button('frameEdit', 'Edit', { kind: m.kind, target: m.model }) },
];

function kindHtml(k) {
    const differs = k.shipped_differs
        ? ' ' + badge('differs from the shipped default', 'amber') + ' ' + button('frameReset', 'Reset to the shipped default', { kind: k.kind })
        : '';
    return '<section class="rounded-lg border border-border bg-background" data-frame-kind="' + esc(k.kind) + '">' +
        '<div class="flex flex-wrap items-center gap-3 p-4 border-b border-border"><h3 class="text-lg font-semibold">' + esc(k.kind) + '</h3>' +
        '<span class="text-xs">Default: ' + esc(versionLine(k.default)) + '</span>' + differs +
        '<span class="flex-1"></span>' + button('frameEdit', 'Edit the default', { kind: k.kind, target: 'default' }, BTN) + '</div>' +
        tableHtml(FRAME_COLUMNS, k.models.map((m) => Object.assign({ kind: k.kind }, m)), 'No models') + '</section>';
}

function historyHtml(info) {
    if (!info.history.length) return '<p class="text-sm text-muted-foreground">No versions yet</p>';
    return '<ol class="space-y-2" id="frameHistory">' + info.history.map((v) =>
        '<li class="rounded border border-border p-2 text-xs" data-version="' + esc(v.id) + '"><div class="flex items-center gap-2"><span class="font-medium">' + esc(versionLine(v)) + '</span><span class="flex-1"></span>' +
        button('frameRollback', 'Roll back to this', { version: v.id }) + '</div><pre class="mt-1 whitespace-pre-wrap font-mono">' + esc(v.text === null ? '(follows the default)' : v.text) + '</pre></li>').join('') + '</ol>';
}

function editorHtml() {
    const d = framesView.info;
    if (!d) return '<p class="text-sm text-muted-foreground p-4">Pick a model or a default to edit its frame.</p>';
    const t = framesView.shown;
    const isModel = t.target !== 'default';
    const k = kindOf(t.kind);
    const model = isModel ? k.models.find((m) => m.model === t.target) : null;
    const lockHistory = isModel && d.lock_history && d.lock_history.length
        ? '<p class="text-xs text-muted-foreground">Lock history: ' + d.lock_history.map((l) => esc((l.locked ? 'locked' : 'unlocked') + ' by ' + l.created_by + ' ' + fmtTime(l.created_ms))).join('; ') + '</p>' : '';
    return '<div class="p-4 space-y-3" id="frameEditor">' +
        '<h3 class="text-lg font-semibold">' + esc(t.kind) + ' / ' + esc(isModel ? t.target : 'default') + '</h3>' +
        '<p class="text-xs text-muted-foreground">Write one line, at most 1,000 characters, with <code>{{client_prompt}}</code> (the customer\'s words) and <code>{{tracking_code}}</code> (the job\'s marker) once each. ' +
        'A save applies only to jobs created after it; the frame is not checked for ownership or consent wording.</p>' +
        (isModel && model && model.source === 'default' ? '<p id="frameFollowing" class="text-xs text-muted-foreground">This model follows the default. Saving gives it its own frame; until then a change to the default reaches it.</p>' : '') +
        '<textarea id="frameText" rows="4" class="flex w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"></textarea>' +
        '<p id="frameRefusal" class="hidden text-sm text-destructive" role="alert"></p>' +
        '<div class="flex flex-wrap gap-2">' + button('frameSave', 'Save', {}, BTN_PRIMARY) +
        (isModel && model && model.source === 'own' ? button('frameFollow', 'Follow the default', {}, BTN) : '') +
        (isModel ? '' : button('frameApplyAll', 'Apply to all models of this kind', {}, BTN)) + '</div>' +
        '<div id="frameWarnings" class="hidden rounded-md border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900"></div>' +
        '<div id="frameApplied" class="hidden rounded-md border border-border p-2 text-xs"></div>' +
        '<div class="space-y-2 border-t border-border p-3"><h4 class="text-sm font-semibold">Preview</h4>' +
        '<p class="text-xs text-muted-foreground">Renders the draft above with a sample prompt, without sending or charging. The tracking code differs for every job.</p>' +
        '<div class="flex gap-2"><input id="framePreviewPrompt" class="' + FIELD + '" value="a cat on a sofa">' + button('framePreview', 'Preview', {}, BTN) + '</div>' +
        '<pre id="framePreviewText" class="hidden max-h-40 overflow-auto rounded border border-border bg-muted p-2 text-xs whitespace-pre-wrap"></pre>' +
        '<p id="framePreviewMeta" class="text-xs text-muted-foreground"></p></div>' +
        '<div class="space-y-2 border-t border-border p-3"><h4 class="text-sm font-semibold">History</h4>' + lockHistory + historyHtml(d) + '</div></div>';
}

registerTab({
    id: 'frames',
    label: 'Prompt frames',
    load: async (panel) => {
        const names = await providerNames();
        if (!framesView.provider || !names.includes(framesView.provider)) framesView.provider = names.includes(state.provider) ? state.provider : (names[0] || '');
        panel.innerHTML = '<div class="space-y-6"><div class="flex items-center gap-3"><h3 class="text-lg font-semibold">Prompt frames</h3>' +
            '<select id="framesProvider" data-change="framesProvider" class="h-8 rounded-md border border-input bg-background px-2 text-xs">' +
            names.map((n) => '<option value="' + esc(n) + '"' + (n === framesView.provider ? ' selected' : '') + '>' + esc(n) + '</option>').join('') + '</select></div>' +
            '<p class="text-xs text-muted-foreground">A save applies only to jobs created after it. A model with its own frame follows no default; a locked model keeps its own when a default is applied to all.</p>' +
            '<div id="framesKinds" class="space-y-4"></div><div class="rounded-lg border border-border bg-background" id="framesEditorBox"></div></div>';
        framesView.gen += 1;
        await loadFrames();
    },
});

// Asks, then draws the answer only if no other editor or provider was opened meanwhile; a stale answer is dropped.
// While it runs the tab is locked like during a write (`applyBusy`). A stale failure is dropped as quietly as a stale
// success: the operator has moved on, so there is nothing to tell them.
async function current(ask, draw) {
    const g = framesView.gen;
    framesView.loading += 1;
    applyBusy();
    try {
        let answer;
        try { answer = await ask(); } catch (err) { if (g !== framesView.gen) return null; throw err; }
        if (g !== framesView.gen) return null;
        draw(answer);
        return answer;
    } finally {
        framesView.loading -= 1;
        applyBusy();
    }
}

function openTarget(provider, kind, target) {
    framesView.gen += 1;
    framesView.provider = provider;
    framesView.kind = kind;
    framesView.target = target;
}

// The table and the open editor, read together so they are drawn together.
async function readView(at) {
    const provider = at.provider, kind = at.kind, target = at.target;
    let data;
    try {
        data = await api('/frames?provider=' + encodeURIComponent(provider));
    } catch (err) {
        if (!(err instanceof ApiError) || err.status !== 404) throw err;
        return { provider: provider, data: null, info: null };
    }
    const info = data.kinds.some((k) => k.kind === kind) ? await api(framesPath({ provider: provider, kind: kind, target: target })) : null;
    return { provider: provider, kind: kind, target: target, data: data, info: info };
}

function drawView(view) {
    framesView.data = view.data;
    framesView.listed = view.provider;
    const host = document.getElementById('framesKinds');
    if (!view.data) {
        host.innerHTML = '<p class="text-sm text-muted-foreground">This provider has no prompt frames: the core frame applies.</p>';
        framesView.shown = null;
        document.getElementById('framesEditorBox').innerHTML = '';
        return;
    }
    host.innerHTML = view.data.kinds.map(kindHtml).join('');
    drawEditor(view);
}

// Draws the editor of an answer (history and all); the draft is the stored text of the current version.
function drawEditor(view) {
    framesView.info = view.info;
    framesView.shown = view.info ? { provider: view.provider, kind: view.kind, target: view.target } : null;
    document.getElementById('framesEditorBox').innerHTML = editorHtml();
    if (view.info) {
        const loaded = view.info.current && view.info.current.text !== null ? view.info.current.text : (view.info.effective ? view.info.effective.text : '');
        const keep = framesView.keep;
        framesView.loaded = loaded;
        document.getElementById('frameText').value = (keep && keep.key === targetKey(view)) ? keep.draft : loaded;
    }
    applyBusy();
}

function targetKey(t) { return t.provider + '/' + t.kind + '/' + t.target; }

// Reloads the table and the editor of `at` (what was asked for last, unless a write names its own target); nothing is
// drawn if the operator has moved on. `keepDraft`: the open editor keeps what was typed (a lock toggle changes the
// table, not the frame text). Answers whether it drew.
function loadFrames(keepDraft, at) {
    const keep = keepDraft && framesView.shown ? { key: targetKey(framesView.shown), draft: document.getElementById('frameText').value } : null;
    const view = at || { provider: framesView.provider, kind: framesView.kind, target: framesView.target };
    return current(() => readView(view), (v) => { framesView.keep = keep; drawView(v); framesView.keep = null; });
}

// The editor open for a provider's table, if it is that provider's.
function openFor(provider) {
    const t = framesView.shown;
    return t && t.provider === provider ? t : { provider: provider, kind: '', target: '' };
}

// While a change is in flight, or a view is loading, the draft cannot be typed over and nothing in the view being
// replaced can be clicked. During a load only the controls that open another view stay usable: they are how it is replaced.
function applyBusy() {
    const writing = framesView.busy, loading = framesView.loading > 0;
    const text = document.getElementById('frameText');
    if (text) text.readOnly = writing || loading;
    for (const el of document.querySelectorAll('#panel-frames button, #panel-frames input, #panel-frames select')) {
        const opens = el.matches('[data-act="frameEdit"], #framesProvider');
        el.disabled = writing || (loading && !opens);
    }
    updateSave();
}
// Save is for a change: an untouched draft would only store the text that is already in force (and give a model that
// follows the default a frame of its own).
function updateSave() {
    const save = document.querySelector('#panel-frames [data-act="frameSave"]');
    const text = document.getElementById('frameText');
    if (save && text) save.disabled = framesView.busy || framesView.loading > 0 || text.value === framesView.loaded;
}
document.addEventListener('input', (e) => { if (e.target.id === 'frameText') updateSave(); });
async function changing(fn) {
    framesView.busy = true;
    applyBusy();
    try { return await fn(); } finally { framesView.busy = false; applyBusy(); }
}

function showRefusal(err) {
    const el = document.getElementById('frameRefusal');
    el.textContent = err.param ? err.message + ' (' + err.param + ')' : err.message;
    el.classList.remove('hidden');
}
function clearRefusal() { document.getElementById('frameRefusal').classList.add('hidden'); }

function showWarnings(warnings) {
    const el = document.getElementById('frameWarnings');
    if (!el) return;
    el.classList.toggle('hidden', !(warnings && warnings.length));
    el.textContent = (warnings || []).join(' ');
}

// A refusal is shown beside the editor, where the fault is, if that editor is still the one that asked; anything else
// is the shared toast.
async function guarded(fn, stillAsked) {
    const g = framesView.gen;
    clearRefusal();
    try { return await fn(); } catch (err) {
        if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
            if (g === framesView.gen && (!stillAsked || stillAsked())) showRefusal(err);
            return null;
        }
        throw err;
    }
}

ACTIONS.framesProvider = async (el) => {
    openTarget(el.value, '', '');
    await loadFrames();
};
ACTIONS.frameEdit = async (el) => {
    const provider = framesView.listed, kind = el.dataset.kind, target = el.dataset.target;
    openTarget(provider, kind, target);
    await current(async () => ({ provider: provider, kind: kind, target: target, info: await api(framesPath({ provider: provider, kind: kind, target: target })) }), drawEditor);
};
ACTIONS.frameSave = async () => {
    const t = framesView.shown;
    const done = await changing(() => guarded(async () => {
        const saved = await api(framesPath(t), { method: 'PUT', body: { text: document.getElementById('frameText').value } });
        if (await loadFrames(false, t)) showWarnings(saved.warnings);
        return saved;
    }));
    if (done) toast(done.unchanged ? 'Nothing changed' : 'Saved: applies to jobs created from now on', 'success');
};
ACTIONS.frameFollow = async () => {
    const t = framesView.shown;
    const done = await changing(() => guarded(async () => {
        await api(framesPath(t), { method: 'PUT', body: { text: null } });
        await loadFrames(false, t);
        return true;
    }));
    if (done) toast('This model follows the default', 'success');
};
ACTIONS.frameRollback = async (el) => {
    const t = framesView.shown;
    const done = await changing(() => guarded(async () => {
        const answer = await api(framesPath(t, '/rollback'), { method: 'POST', body: { version_id: Number(el.dataset.version) } });
        if (await loadFrames(false, t)) showWarnings(answer.warnings);
        return true;
    }));
    if (done) toast('Rolled back: a new version was saved', 'success');
};
ACTIONS.frameReset = async (el) => {
    const t = { provider: framesView.listed, kind: el.dataset.kind, target: 'default' };
    await changing(async () => {
        const result = await api('/frames/' + encodeURIComponent(t.provider) + '/' + encodeURIComponent(t.kind) + '/reset', { method: 'POST' });
        if (await loadFrames(false, openFor(t.provider))) showWarnings(result.warnings);
    });
    toast('The default is the shipped text again', 'success');
};
ACTIONS.frameLock = async (el) => {
    const t = { provider: framesView.listed, kind: el.dataset.kind, target: el.dataset.model };
    const locked = el.checked;
    await changing(async () => {
        await api(framesPath(t, '/lock'), { method: 'PUT', body: { locked: locked } });
        await loadFrames(true, openFor(t.provider));
    });
    toast(locked ? 'Locked: apply to all keeps this model' : 'Unlocked', 'success');
};
ACTIONS.frameApplyAll = async () => {
    const t = framesView.shown;
    let drawn = null;
    const answer = await changing(() => guarded(async () => {
        const result = await api('/frames/' + encodeURIComponent(t.provider) + '/' + encodeURIComponent(t.kind) + '/apply-all', { method: 'POST', body: { text: document.getElementById('frameText').value } });
        drawn = await loadFrames(false, t);
        if (drawn) showWarnings(result.warnings);
        return result;
    }));
    if (!answer) return;
    toast('Applied to ' + answer.applied.length + ' model(s)', 'success');
    if (!drawn) return;                                  // the operator moved on: the editor that asked is gone
    const el = document.getElementById('frameApplied');
    el.classList.remove('hidden');
    el.textContent = 'Applied: ' + (answer.applied.join(', ') || '-') + '. Kept (locked): ' + (answer.kept.join(', ') || '-') + '. Unchanged: ' + (answer.unchanged.join(', ') || '-') + '.';
};
ACTIONS.framePreview = async () => {
    const t = framesView.shown;
    const k = kindOf(t.kind);
    const model = t.target === 'default' ? (k.models[0] && k.models[0].model) : t.target;
    if (!model) { toast('This kind has no model to preview with', 'error'); return; }
    const draft = document.getElementById('frameText').value;
    const g = framesView.gen;
    document.getElementById('framePreviewText').classList.add('hidden');
    document.getElementById('framePreviewMeta').textContent = '';
    // the answer belongs to the editor and the draft that asked: another editor, or another draft, drops it
    const stillAsked = () => g === framesView.gen && document.getElementById('frameText').value === draft;
    const answer = await guarded(() => api('/frames/preview', { method: 'POST', body: { model: model, prompt: document.getElementById('framePreviewPrompt').value, text: draft } }), stillAsked);
    if (!answer || !stillAsked()) return;
    const pre = document.getElementById('framePreviewText');
    pre.classList.remove('hidden');
    pre.textContent = answer.text;
    document.getElementById('framePreviewMeta').textContent = answer.length + ' characters, shown with the sample tracking code (' + answer.source + ' frame)';
    showWarnings(answer.warnings);
};
