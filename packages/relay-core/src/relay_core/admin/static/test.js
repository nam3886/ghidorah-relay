'use strict';

// Model test page. It calls only public routes with the API key typed here. The key lives in this variable and
// nowhere else (no browser store, no cookie), so a reload forgets it.
let apiKey = '';
let catalog = [];
let chosen = null;

const el = (id) => document.getElementById(id);

async function call(path, init) {
    const options = init || {};
    const headers = Object.assign({ Authorization: 'Bearer ' + apiKey }, options.headers || {});
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

function showError(err) { el('status').textContent = String(err.message || err); }

async function loadModels() {
    apiKey = el('apiKey').value.trim();
    if (!apiKey) { showError(new Error('Enter an API key')); return; }
    catalog = (await call('/v1/models')).data;
    const groups = {};
    for (const m of catalog) (groups[m.kind] = groups[m.kind] || []).push(m);
    el('models').innerHTML = Object.keys(groups).sort().map((kind) =>
        '<div class="group-title">' + esc(kind) + '</div>' + groups[kind].map((m) =>
            '<button class="model-item" data-act="pick" data-id="' + esc(m.id) + '"><span>' + esc(m.id) + '</span><span>' + esc(m.owned_by) + '</span></button>').join('')).join('');
    try {
        el('points').textContent = JSON.stringify(await call('/v1/points'), null, 2);
    } catch (err) { el('points').textContent = String(err.message); }
    el('status').textContent = 'Pick a model';
}

function optionLabel(o) {
    for (const key of ['name', 'label', 'title', 'value', 'id']) if (typeof o[key] === 'string' && o[key]) return o[key];
    return '';
}

ACTIONS.pick = async (button) => {
    chosen = catalog.find((m) => m.id === button.dataset.id);
    for (const b of document.querySelectorAll('.model-item')) b.classList.toggle('active', b === button);
    el('modelName').textContent = chosen.id;
    el('modelInfo').textContent = chosen.kind + ' · ' + chosen.owned_by + ' · price ' + JSON.stringify(chosen.prices) + ' · limits ' + JSON.stringify(chosen.limits);
    el('generate').disabled = false;
    el('extras').innerHTML = '';
    el('ratioList').innerHTML = '';
    el('styleList').innerHTML = '';
    let extras = {};
    try { extras = await call('/v1/providers/' + encodeURIComponent(chosen.owned_by) + '/options'); } catch (_) { return; }
    for (const [id, list] of [['ratioList', extras.ratios], ['styleList', extras.styles]]) {
        el(id).innerHTML = (list || []).map((o) => optionLabel(o)).filter(Boolean).map((v) => '<option value="' + esc(v) + '">').join('');
    }
    // A template is a UI convenience: picking one copies its prompt into the box. The request has no template field.
    const templates = extras.templates || [];
    if (templates.length) {
        el('extras').innerHTML = '<div class="extras-title">Templates (click to use the prompt)</div><div class="template-list">' +
            templates.map((t, i) => '<button class="template-item" data-act="useTemplate" data-index="' + i + '">' + esc(t.show_value || t.prompt) + '</button>').join('') + '</div>';
        el('extras').templates = templates;
    }
};

ACTIONS.useTemplate = (button) => {
    el('prompt').value = el('extras').templates[Number(button.dataset.index)].prompt;
};

function body() {
    const out = { model: chosen.id, prompt: el('prompt').value };
    const add = (name, value) => { if (value !== '') out[name] = value; };
    if (chosen.kind === 'video' && el('duration').value.trim()) out.duration = Number(el('duration').value);
    add('ratio', el('ratio').value.trim());
    if (el('count').value.trim()) out.n = Number(el('count').value);
    if (chosen.kind === 'image') add('style', el('style').value.trim());
    add('title', el('title').value.trim());
    const images = el('images').value.split('\n').map((l) => l.trim()).filter(Boolean);
    if (images.length) out.images = images;
    if (el('options').value.trim()) out.options = JSON.parse(el('options').value);
    return out;
}

function showResult(job) {
    el('raw').textContent = JSON.stringify(job, null, 2);
    const urls = [...(job.result_urls || []), ...(job.images || [])];
    el('media').innerHTML = urls.map((u) => {
        const safe = safeMediaUrl(u);
        if (!safe) return '<p class="mini">link not shown: ' + esc(u) + '</p>';
        return chosen.kind === 'image' ? '<img src="' + esc(safe) + '" alt="">' : '<video src="' + esc(safe) + '" controls></video>';
    }).join('');
}

ACTIONS.generate = async (button) => {
    button.disabled = true;
    try {
        el('media').innerHTML = '';
        const path = chosen.kind === 'image' ? '/v1/images/generations' : '/v1/video/generations';
        const made = await call(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body()) });
        el('status').textContent = 'Job ' + made.id + ' ' + made.status;
        showResult(made);
        for (let i = 0; i < 600; i++) {
            await new Promise((r) => setTimeout(r, 3000));
            const job = await call(path + '/' + made.id);
            el('status').textContent = 'Job ' + job.id + ' ' + job.status + (job.stage ? ' (' + job.stage + ')' : '');
            showResult(job);
            if (job.status === 'done' || job.status === 'failed' || job.status === 'completed') return;
        }
    } catch (err) {
        showError(err);
    } finally {
        button.disabled = false;
    }
};

ACTIONS.loadModels = () => loadModels().catch(showError);
