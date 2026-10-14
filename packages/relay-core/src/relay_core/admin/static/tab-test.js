'use strict';

// Model test tab. It calls only public routes with the API key typed here.
// What is kept in this browser, and where:
//   - the form (model, prompt, options) in localStorage under TEST_FORM_KEY;
//   - the attached image files in IndexedDB (TEST_DB), capped at TEST_MAX_IMAGES files and TEST_MAX_BYTES together;
//   - the API key in sessionStorage only (TEST_KEY, see common.js): it is gone when the browser tab closes and it is
//     never written to localStorage or IndexedDB.
// Every store access is wrapped: with storage blocked the tab still works, it just forgets on reload.
const TEST_FORM_KEY = 'relay_test_form';
const TEST_DB = 'relay-admin-test';
const TEST_FIELDS = ['prompt', 'duration', 'ratio', 'count', 'style', 'images', 'title', 'options'];
const TEST_MAX_IMAGES = 10;
const TEST_MAX_BYTES = 12 * 1024 * 1024;
const testState = { apiKey: '', models: [], chosen: null, model: '', files: [], generation: 0 };

const tid = (id) => document.getElementById(id);
const TEST_AREA = 'flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm';

function testPanelHtml() {
    return '<div class="space-y-4">' +
        '<div class="rounded-lg border border-border bg-background p-4 space-y-2">' +
        '<div class="flex flex-wrap items-center gap-3"><label class="text-sm font-medium" for="apiKey">API key</label>' +
        '<input id="apiKey" type="password" autocomplete="off" placeholder="rk-..." spellcheck="false" class="' + FIELD + ' max-w-md">' +
        '<button id="loadKey" data-act="testLoadModels" class="' + BTN_PRIMARY + '">Load models</button>' +
        '<button data-act="testForgetKey" class="' + BTN + '">Forget key</button>' +
        '<button data-act="testClear" class="' + BTN + ' text-destructive" title="Reset the form and the attached images">Clear form</button></div>' +
        '<p class="text-xs text-muted-foreground">The key is kept for this browser tab only (session storage): it is forgotten when the tab closes and never saved anywhere else. ' +
        'The model, the options, the prompt and the attached images are saved in this browser until you press Clear form.</p></div>' +
        '<div class="grid gap-4 lg:grid-cols-4">' +
        '<aside class="space-y-2 min-w-0"><div id="models"></div><pre id="points" class="text-xs text-muted-foreground whitespace-pre-wrap break-all"></pre></aside>' +
        '<section class="lg:col-span-2 min-w-0 rounded-lg border border-border bg-background p-4 space-y-3">' +
        '<h3 id="modelName" class="text-lg font-semibold">Pick a model</h3><p id="modelInfo" class="text-xs text-muted-foreground"></p><div id="extras"></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="prompt">Prompt</label><textarea id="prompt" rows="8" class="' + TEST_AREA + '"></textarea></div>' +
        '<div class="grid grid-cols-2 gap-3 md:grid-cols-4">' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="duration">Duration (s, video)</label><input id="duration" type="text" inputmode="numeric" class="' + FIELD + '"></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="ratio">Ratio</label><input id="ratio" type="text" list="ratioList" placeholder="16:9" class="' + FIELD + '"><datalist id="ratioList"></datalist></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="count">Count (n)</label><input id="count" type="text" inputmode="numeric" class="' + FIELD + '"></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="style">Style (image)</label><input id="style" type="text" list="styleList" class="' + FIELD + '"><datalist id="styleList"></datalist></div></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="images">Reference image URLs (https, one per line)</label><textarea id="images" rows="2" class="' + TEST_AREA + '"></textarea></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="imageFiles">Attach images (up to ' + TEST_MAX_IMAGES + ' files, ' + (TEST_MAX_BYTES / 1048576) + ' MiB together; sent as data)</label>' +
        '<input id="imageFiles" type="file" accept="image/*" multiple data-change="testAddImages" class="text-xs">' +
        '<ul id="imageList" class="mt-2 space-y-1"></ul><p id="imageNote" class="text-xs text-muted-foreground"></p></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="title">Title</label><input id="title" type="text" class="' + FIELD + '"></div>' +
        '<div><label class="block text-xs text-muted-foreground mb-1" for="options">Extra options (JSON object, optional)</label><textarea id="options" rows="2" class="' + TEST_AREA + '"></textarea></div>' +
        '<button id="generate" data-act="testGenerate" class="' + BTN_PRIMARY + ' w-full" disabled>Generate</button></section>' +
        '<section class="min-w-0 rounded-lg border border-border bg-background p-4 space-y-2"><h3 class="text-lg font-semibold">Result</h3>' +
        '<div id="status" class="text-xs text-muted-foreground">Idle</div><div id="media" class="space-y-2"></div>' +
        '<pre id="raw" class="text-xs text-muted-foreground whitespace-pre-wrap break-all"></pre></section></div></div>';
}

// ---- stores (every access is guarded) --------------------------------------------------------------------------
function testReadForm() {
    try { return JSON.parse(localStorage.getItem(TEST_FORM_KEY)) || {}; } catch (_) { return {}; }
}

function testSaveForm() {
    const form = { model: testState.model };
    for (const id of TEST_FIELDS) form[id] = tid(id).value;
    try { localStorage.setItem(TEST_FORM_KEY, JSON.stringify(form)); } catch (_) { /* storage blocked or full */ }
}

function testOpenDb() {
    return new Promise((resolve, reject) => {
        try {
            const req = indexedDB.open(TEST_DB, 1);
            req.onupgradeneeded = () => req.result.createObjectStore('kv');
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        } catch (err) { reject(err); }
    });
}

// One value under 'files': the list [{name, type, blob}]. Resolves to the stored value (or null); never rejects.
async function testDb(mode, value) {
    let db = null;
    try {
        db = await testOpenDb();
        return await new Promise((resolve, reject) => {
            const tx = db.transaction('kv', mode === 'get' ? 'readonly' : 'readwrite');
            const store = tx.objectStore('kv');
            const req = mode === 'get' ? store.get('files') : mode === 'put' ? store.put(value, 'files') : store.delete('files');
            tx.oncomplete = () => resolve(mode === 'get' ? (req.result || null) : true);
            tx.onerror = tx.onabort = () => reject(tx.error);
        });
    } catch (_) {
        return null;
    } finally {
        if (db) db.close();
    }
}

// What a stored or about-to-be-stored list may hold: image blobs only, at most TEST_MAX_IMAGES files and
// TEST_MAX_BYTES together. Anything else (a corrupt or edited store) is dropped, in order, from the first that breaks a limit.
function testValidFiles(list) {
    const kept = [];
    let bytes = 0;
    for (const f of Array.isArray(list) ? list : []) {
        if (!f || !(f.blob instanceof Blob) || !f.blob.type.startsWith('image/')) continue;
        if (kept.length >= TEST_MAX_IMAGES || bytes + f.blob.size > TEST_MAX_BYTES) continue;
        kept.push({ name: String(f.name || 'image'), type: f.blob.type, blob: f.blob });
        bytes += f.blob.size;
    }
    return kept;
}

async function testSaveFiles() {
    testState.files = testValidFiles(testState.files);
    const stored = testState.files.length ? await testDb('put', testState.files) : await testDb('delete');
    tid('imageNote').textContent = stored ? '' : 'The attached images could not be saved in this browser: they are lost on reload.';
}

function testRenderFiles() {
    tid('imageList').innerHTML = testState.files.map((f, i) =>
        '<li class="flex items-center gap-2 text-xs"><span class="truncate max-w-[260px]">' + esc(f.name) + '</span>' +
        '<span class="text-muted-foreground">' + Math.ceil(f.blob.size / 1024) + ' KiB</span>' +
        button('testRemoveImage', 'Remove', { index: i }) + '</li>').join('');
}

// ---- API ---------------------------------------------------------------------------------------------------
async function testCall(path, init) {
    const options = init || {};
    const headers = Object.assign({ Authorization: 'Bearer ' + testState.apiKey }, options.headers || {});
    const response = await fetch(path, Object.assign({}, options, { headers }));
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
    if (!response.ok) {
        const err = (data && data.error) || {};
        throw new Error((err.code ? err.code + ': ' : '') + (err.message || 'HTTP ' + response.status));
    }
    return data;
}

function testShowError(err) { tid('status').textContent = String(err.message || err); }

async function testLoadModels() {
    testState.apiKey = tid('apiKey').value.trim();
    if (!testState.apiKey) { testShowError(new Error('Enter an API key')); return; }
    testState.models = (await testCall('/v1/models')).data;
    try { sessionStorage.setItem(TEST_KEY, testState.apiKey); } catch (_) { /* private mode: the key is only in memory */ }
    const groups = {};
    for (const m of testState.models) (groups[m.kind] = groups[m.kind] || []).push(m);
    tid('models').innerHTML = Object.keys(groups).sort().map((kind) =>
        '<div class="mt-3 rounded-t-md border border-border bg-muted px-3 py-2 text-sm font-semibold">' + esc(kind) + '</div>' + groups[kind].map((m) =>
            '<button data-act="testPick" data-id="' + esc(m.id) + '" class="flex w-full justify-between border border-t-0 border-border bg-background px-3 py-2 text-left text-sm hover:bg-accent"><span>' + esc(m.id) + '</span><span class="text-muted-foreground">' + esc(m.owned_by) + '</span></button>').join('')).join('');
    try {
        tid('points').textContent = JSON.stringify(await testCall('/v1/points'), null, 2);
    } catch (err) { tid('points').textContent = String(err.message); }
    tid('status').textContent = 'Pick a model';
    const saved = testState.models.find((m) => m.id === testState.model);
    if (saved) await testPickModel(saved);
}

function testOptionLabel(o) {
    for (const key of ['name', 'label', 'title', 'value', 'id']) if (typeof o[key] === 'string' && o[key]) return o[key];
    return '';
}

async function testPickModel(model) {
    testState.chosen = model;
    testState.model = model.id;
    for (const b of document.querySelectorAll('#models [data-act="testPick"]')) {
        const active = b.dataset.id === model.id;
        b.classList.toggle('bg-accent', active);
        b.classList.toggle('font-semibold', active);
    }
    tid('modelName').textContent = model.id;
    tid('modelInfo').textContent = model.kind + ' · ' + model.owned_by + ' · price ' + JSON.stringify(model.prices) + ' · limits ' + JSON.stringify(model.limits);
    tid('generate').disabled = false;
    tid('extras').innerHTML = '';
    tid('ratioList').innerHTML = '';
    tid('styleList').innerHTML = '';
    testSaveForm();
    let extras = {};
    try { extras = await testCall('/v1/providers/' + encodeURIComponent(model.owned_by) + '/options'); } catch (_) { return; }
    for (const [id, list] of [['ratioList', extras.ratios], ['styleList', extras.styles]]) {
        tid(id).innerHTML = (list || []).map((o) => testOptionLabel(o)).filter(Boolean).map((v) => '<option value="' + esc(v) + '">').join('');
    }
    // A template is a UI convenience: picking one copies its prompt into the box. The request has no template field.
    const templates = extras.templates || [];
    if (templates.length) {
        tid('extras').innerHTML = '<div class="text-xs text-muted-foreground mb-1">Templates (click to use the prompt)</div><div class="max-h-40 overflow-y-auto rounded-md border border-border">' +
            templates.map((t, i) => '<button data-act="testUseTemplate" data-index="' + i + '" class="block w-full border-b border-border px-3 py-2 text-left text-xs hover:bg-accent">' + esc(t.show_value || t.prompt) + '</button>').join('') + '</div>';
        tid('extras').templates = templates;
    }
}

function dataUri(blob) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error || new Error('Could not read ' + blob.name));
        reader.readAsDataURL(blob);
    });
}

async function testBody() {
    const chosen = testState.chosen;
    const out = { model: chosen.id, prompt: tid('prompt').value };
    const add = (name, value) => { if (value !== '') out[name] = value; };
    if (chosen.kind === 'video' && tid('duration').value.trim()) out.duration = Number(tid('duration').value);
    add('ratio', tid('ratio').value.trim());
    if (tid('count').value.trim()) out.n = Number(tid('count').value);
    if (chosen.kind === 'image') add('style', tid('style').value.trim());
    add('title', tid('title').value.trim());
    const images = tid('images').value.split('\n').map((l) => l.trim()).filter(Boolean);
    for (const f of testState.files) images.push(await dataUri(f.blob));
    if (images.length) out.images = images;
    if (tid('options').value.trim()) out.options = JSON.parse(tid('options').value);
    return out;
}

function testShowResult(job) {
    tid('raw').textContent = JSON.stringify(job, null, 2);
    const urls = [...(job.result_urls || []), ...(job.images || [])];
    tid('media').innerHTML = urls.map((u) => {
        const safe = safeMediaUrl(u);
        if (!safe) return '<p class="text-xs text-muted-foreground">link not shown: ' + esc(u) + '</p>';
        return testState.chosen.kind === 'image' ? '<img src="' + esc(safe) + '" alt="" class="max-w-full rounded-md">' : '<video src="' + esc(safe) + '" controls class="max-w-full rounded-md"></video>';
    }).join('');
}

// ---- actions -------------------------------------------------------------------------------------------------
ACTIONS.testLoadModels = () => testLoadModels().catch(testShowError);
ACTIONS.testPick = (btn) => testPickModel(testState.models.find((m) => m.id === btn.dataset.id));
ACTIONS.testUseTemplate = (btn) => {
    tid('prompt').value = tid('extras').templates[Number(btn.dataset.index)].prompt;
    testSaveForm();
};

ACTIONS.testForgetKey = () => {
    try { sessionStorage.removeItem(TEST_KEY); } catch (_) { /* nothing was stored */ }
    testState.apiKey = '';
    tid('apiKey').value = '';
};

ACTIONS.testClear = async () => {
    try { localStorage.removeItem(TEST_FORM_KEY); } catch (_) { /* storage blocked */ }
    await testDb('delete');
    testState.files = [];
    testState.model = '';
    testState.chosen = null;
    for (const id of TEST_FIELDS) tid(id).value = '';
    tid('imageFiles').value = '';
    tid('imageNote').textContent = '';
    tid('generate').disabled = true;
    tid('modelName').textContent = 'Pick a model';
    tid('modelInfo').textContent = '';
    tid('extras').innerHTML = '';
    for (const b of document.querySelectorAll('#models [data-act="testPick"]')) b.classList.remove('bg-accent', 'font-semibold');
    testRenderFiles();
};

ACTIONS.testAddImages = async (input) => {
    let bytes = testState.files.reduce((sum, f) => sum + f.blob.size, 0);
    const skipped = [];
    for (const file of input.files) {
        if (!file.type.startsWith('image/') || testState.files.length >= TEST_MAX_IMAGES || bytes + file.size > TEST_MAX_BYTES) { skipped.push(file.name); continue; }
        testState.files.push({ name: file.name, type: file.type, blob: file });
        bytes += file.size;
    }
    input.value = '';
    testRenderFiles();
    if (skipped.length) toast('Not attached (images only, ' + TEST_MAX_IMAGES + ' files, ' + (TEST_MAX_BYTES / 1048576) + ' MiB together): ' + skipped.join(', '), 'error');
    await testSaveFiles();
};

ACTIONS.testRemoveImage = async (btn) => {
    testState.files.splice(Number(btn.dataset.index), 1);
    testRenderFiles();
    await testSaveFiles();
};

ACTIONS.testGenerate = async (btn) => {
    btn.disabled = true;
    const mine = ++testState.generation;
    try {
        tid('media').innerHTML = '';
        const path = testState.chosen.kind === 'image' ? '/v1/images/generations' : '/v1/video/generations';
        const made = await testCall(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(await testBody()) });
        tid('status').textContent = 'Job ' + made.id + ' ' + made.status;
        testShowResult(made);
        for (let i = 0; i < 600 && mine === testState.generation; i++) {
            await new Promise((r) => setTimeout(r, 3000));
            const job = await testCall(path + '/' + made.id);
            tid('status').textContent = 'Job ' + job.id + ' ' + job.status + (job.stage ? ' (' + job.stage + ')' : '');
            testShowResult(job);
            if (job.status === 'done' || job.status === 'failed' || job.status === 'completed') return;
        }
    } catch (err) {
        testShowError(err);
    } finally {
        btn.disabled = false;
    }
};

// ---- the tab ---------------------------------------------------------------------------------------------------
// Built once: leaving the tab and coming back keeps the form, the model list and a running job as they were.
async function testStart(panel) {
    panel.innerHTML = testPanelHtml();
    panel.addEventListener('input', (e) => { if (TEST_FIELDS.includes(e.target.id)) testSaveForm(); });
    const saved = testReadForm();
    testState.model = typeof saved.model === 'string' ? saved.model : '';
    for (const id of TEST_FIELDS) if (typeof saved[id] === 'string') tid(id).value = saved[id];
    const files = await testDb('get');
    testState.files = testValidFiles(files);
    testRenderFiles();
    if (files !== null && (!Array.isArray(files) || files.length !== testState.files.length)) {
        await testSaveFiles();
        tid('imageNote').textContent = 'Saved images that broke the limits (images only, ' + TEST_MAX_IMAGES + ' files, ' + (TEST_MAX_BYTES / 1048576) + ' MiB together) were dropped.';
    }
    let key = '';
    try { key = sessionStorage.getItem(TEST_KEY) || ''; } catch (_) { key = ''; }
    if (key) {
        tid('apiKey').value = key;
        await testLoadModels().catch(testShowError);
    }
}

registerTab({
    id: 'test',
    label: 'Test',
    load: async (panel) => { if (!panel.firstChild) await testStart(panel); },
});
