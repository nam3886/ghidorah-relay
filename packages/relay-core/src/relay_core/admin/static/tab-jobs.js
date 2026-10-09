'use strict';

// Jobs tab: the table (newest first, keyset pages) and the job detail modal. Everything the server sends reaches the
// page through esc() or textContent; media is shown only from same-origin /m/ links (safeMediaUrl).

const JOBS_PAGE = 50;
const STATUS_TONE = { done: 'green', failed: 'red' };
const STEPS = { queued: 1, waiting: 2, unknown: 2, fetching: 3, done: 4, failed: 4 };
const STEP_WIDTH = { 1: 'w-1/4', 2: 'w-2/4', 3: 'w-3/4', 4: 'w-full' };
const jobsView = { cursors: [null], page: 0, next: null, items: [], status: '' };

function progressHtml(job) {
    const step = STEPS[job.status] || 2;
    const bar = job.status === 'failed' ? 'bg-destructive' : job.status === 'done' ? 'bg-green-600' : 'bg-blue-600';
    return '<div class="h-1.5 w-20 rounded bg-gray-200"><div class="h-1.5 rounded ' + bar + ' ' + STEP_WIDTH[step] + '"></div></div>';
}

const JOB_COLUMNS = [
    { key: 'select', label: '', align: 'center', cell: (j) => '<input type="checkbox" data-job-select="' + esc(j.id) + '">' },
    { key: 'progress', label: 'Progress', align: 'left', cell: progressHtml },
    { key: 'title', label: 'Title', align: 'left', cell: (j) => '<div class="max-w-[260px] truncate" title="' + esc(j.title || '') + '">' + esc(j.title || '(untitled)') + '</div>' },
    { key: 'job', label: 'Job', align: 'left', cell: (j) => '<span class="font-mono text-xs">' + esc(j.id.slice(0, 8)) + '</span><div class="text-xs text-muted-foreground">' + esc(j.provider) + '</div>' },
    { key: 'operation', label: 'Operation', align: 'left', cell: (j) => esc(j.operation + ' · ' + j.model) + (j.model_substituted ? ' ' + badge('model differs', 'amber') : '') },
    { key: 'status', label: 'Status', align: 'center', cell: (j) => badge(j.status, STATUS_TONE[j.status] || 'amber') + (j.status === 'failed' && j.price > 0 && j.net_charged === 0 ? ' ' + badge('refunded', 'blue') : '') + (j.error_code ? '<div class="text-xs text-destructive">' + esc(j.error_code) + '</div>' : '') },
    { key: 'points', label: 'Points charged', align: 'center', cell: (j) => esc(j.price) },
    { key: 'results', label: 'Results', align: 'center', cell: (j) => esc(j.outputs) },
    { key: 'references', label: 'References', align: 'center', cell: (j) => esc(j.inputs) },
    { key: 'account', label: 'Account', align: 'left', cell: (j) => esc(j.account_label || '-') },
    { key: 'key', label: 'Key', align: 'left', cell: (j) => esc(j.key_label || '-') },
    { key: 'created', label: 'Created', align: 'left', cell: (j) => esc(fmtTime(j.created_ms)) },
    { key: 'actions', label: 'Actions', align: 'right', cell: (j) => button('openJob', 'Details', { id: j.id }) },
];

registerTab({
    id: 'jobs',
    label: 'Jobs',
    load: async (panel) => {
        const statuses = ['', 'queued', 'waiting', 'fetching', 'unknown', 'done', 'failed'];
        const options = statuses.map((s) => '<option value="' + s + '"' + (s === jobsView.status ? ' selected' : '') + '>' + (s || 'All statuses') + '</option>').join('');
        panel.innerHTML = '<div class="rounded-lg border border-border bg-background" data-pager>' +
            '<div class="flex items-center justify-between gap-4 p-4 border-b border-border"><h3 class="text-lg font-semibold">Jobs</h3>' +
            '<div class="flex items-center gap-3">' + button('bulkRecheck', 'Re-check selected', {}, BTN) + button('bulkRegenerate', 'Regenerate selected', {}, BTN) + '<select id="jobsStatus" data-change="jobsStatus" class="h-8 rounded-md border border-input px-2 text-xs">' + options + '</select>' +
            '<button data-act="refreshJobs" class="' + BTN + '">Refresh</button></div></div>' +
            '<div id="jobsTable"></div><div id="jobsPager"></div></div>';
        panel.firstChild.pager = {
            move: async (dir) => { jobsView.page = Math.max(0, jobsView.page + dir); await loadJobs(); },
        };
        jobsView.cursors = [null];
        jobsView.page = 0;
        await loadJobs();
    },
});

async function loadJobs() {
    const parts = ['limit=' + JOBS_PAGE];
    if (jobsView.status) parts.push('status=' + encodeURIComponent(jobsView.status));
    if (state.provider) parts.push(providerParam());
    const cursor = jobsView.cursors[jobsView.page];
    if (cursor) parts.push('cursor=' + encodeURIComponent(cursor));
    const data = await api('/jobs?' + parts.join('&'));
    jobsView.items = data.items;
    jobsView.next = data.next_cursor;
    if (data.next_cursor) jobsView.cursors[jobsView.page + 1] = data.next_cursor;
    document.getElementById('jobsTable').innerHTML = tableHtml(JOB_COLUMNS, data.items, 'No jobs', (j) => ' data-act="openJob" data-id="' + esc(j.id) + '"');
    document.getElementById('jobsPager').innerHTML = cursorPagerHtml({ page: jobsView.page, next: data.next_cursor });
}

ACTIONS.refreshJobs = () => loadJobs();

function selectedJobs() {
    return [...document.querySelectorAll('[data-job-select]:checked')].map((box) => box.dataset.jobSelect);
}
ACTIONS.bulkRecheck = async () => {
    const ids = selectedJobs();
    if (!ids.length) { toast('Tick at least one job', 'error'); return; }
    const out = await api('/jobs/bulk-recheck', { method: 'POST', body: { job_ids: ids } });
    const skipped = Object.keys(out.skipped).length;
    toast('Re-checked ' + out.rechecked.length + (skipped ? ', left ' + skipped + ' alone' : ''), skipped ? 'info' : 'success');
    await loadJobs();
};
// ---- Regenerate: one random key per confirmed action -------------------------------------------------------------
// The key is kept (sessionStorage, so a reload keeps it) until the server answers with any HTTP status. A network
// error leaves it, so the next try of the same action (same job, or same selection) sends the same key and the
// server returns the job it already made instead of charging again. After an answer the next click is a new action.
const OP_STORE = 'regenerateOperations';
function readOps() {
    try { return JSON.parse(sessionStorage.getItem(OP_STORE) || '{}'); } catch (_) { return {}; }
}
function writeOps(ops) {
    try { sessionStorage.setItem(OP_STORE, JSON.stringify(ops)); } catch (_) { /* private mode: the key stays in memory */ }
}
const memoryOps = {};
function operationKey(action) {
    const ops = Object.assign(memoryOps, readOps());
    if (!ops[action]) {
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        ops[action] = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
        writeOps(ops);
    }
    return ops[action];
}
function forgetOperation(action) {
    delete memoryOps[action];
    const ops = readOps();
    delete ops[action];
    writeOps(ops);
}
async function regenerateCall(action, path, body) {
    const operation_key = operationKey(action);
    try {
        const out = await api(path, { method: 'POST', body: Object.assign({}, body, { operation_key }) });
        forgetOperation(action);
        return out;
    } catch (err) {
        // The key is forgotten only after an answer below 500. A 5xx (a bulk cut short by a stop) or a lost answer may
        // leave jobs made, so the retry of the same action must send the same key.
        if (err instanceof ApiError && err.status < 500) forgetOperation(action);
        else err.retrySafe = true;
        throw err;
    }
}

ACTIONS.bulkRegenerate = async () => {
    const ids = selectedJobs();
    if (!ids.length) { toast('Tick at least one job', 'error'); return; }
    if (!confirm('Regenerate ' + ids.length + ' job(s)? Each makes a NEW job at today\u2019s price and charges its key.')) return;
    const out = await regenerateCall('bulk:' + ids.slice().sort().join(','), '/jobs/bulk-regenerate', { job_ids: ids });
    const failed = Object.keys(out.failed).length;
    const replayed = out.created.filter((c) => c.replayed).length;
    toast('Made ' + (out.created.length - replayed) + ', already made ' + replayed + ', failed ' + failed, failed ? 'info' : 'success');
    await loadJobs();
};
ACTIONS.jobsStatus = (el) => { jobsView.status = el.value; jobsView.cursors = [null]; jobsView.page = 0; return loadJobs(); };

// ---- job detail ---------------------------------------------------------------------------------------------
function conclusion(job) {
    const tone = job.status === 'done' ? 'border-green-300 bg-green-50 text-green-900' : job.status === 'failed' ? 'border-red-300 bg-red-50 text-red-900' : 'border-amber-300 bg-amber-50 text-amber-900';
    const lines = [badge(job.status, STATUS_TONE[job.status] || 'amber') + ' ' + esc(job.operation) + ' · ' + esc(job.model) + ' · net charged ' + esc(job.net_charged)];
    if (job.error_code) lines.push('<span class="font-medium">' + esc(job.error_code) + '</span>' + (job.error ? ': ' + esc(job.error) : ''));
    if (job.warnings && job.warnings.length) lines.push('warnings: ' + esc(JSON.stringify(job.warnings)));
    return '<div class="rounded-md border p-3 text-sm space-y-1 ' + tone + '">' + lines.map((l) => '<div>' + l + '</div>').join('') + '</div>';
}

function mediaHtml(job, role) {
    const items = job.media.filter((m) => m.role === role);
    if (!items.length) return '<p class="text-sm text-muted-foreground">None</p>';
    return '<div class="flex flex-wrap gap-3">' + items.map((m) => {
        const url = safeMediaUrl(m.url);
        const meta = esc(m.mime + ' · ' + m.size + ' B') + (m.duration_s ? esc(' · ' + m.duration_s + 's') : '');
        let view;
        if (url && m.mime.startsWith('image/')) view = '<img src="' + esc(url) + '" alt="" class="max-h-48 rounded border border-border">';
        else if (url && m.mime.startsWith('video/')) view = '<video src="' + esc(url) + '" controls preload="metadata" class="max-h-48 rounded border border-border"></video>';
        else view = '<span class="text-xs text-muted-foreground">no preview</span>';
        const dl = role === 'output' ? button('downloadMedia', 'Download', { job: job.id, idx: m.idx }) : '';
        return '<div class="space-y-1">' + view + '<div class="text-xs text-muted-foreground">' + meta + '</div>' + dl + '</div>';
    }).join('') + '</div>';
}

function timelineHtml(job) {
    if (!job.events.length) return '<p class="text-sm text-muted-foreground">No events</p>';
    return '<ol class="space-y-1 text-xs">' + job.events.map((e) => '<li><span class="text-muted-foreground">' + esc(fmtTime(e.at_ms)) + '</span> ' +
        badge(e.source, e.stage === 'recovered' ? 'green' : 'gray') + ' <span class="font-medium">' + esc(e.stage) + '</span> ' + esc(e.text) + '</li>').join('') + '</ol>';
}

function technicalHtml(job) {
    const rows = [
        ['Job id', job.id], ['Provider / pool', job.provider + ' / ' + job.pool], ['Wallet', job.wallet_kind], ['Key', job.key_label],
        ['Account', job.account_label], ['External ref', job.external_ref], ['Attempts / claims', job.attempts + ' / ' + job.claims],
        ['Account cost', job.account_cost], ['Upstream price', job.upstream_price], ['Upstream model', job.upstream_model],
        ['Completion', job.completion], ['Sent', fmtTime(job.sent_ms)], ['Updated', fmtTime(job.updated_ms)],
        ['Callback', job.callback.status ? job.callback.status + ' (' + job.callback.attempts + ' attempts)' : '-'],
        ['Params', JSON.stringify(job.params)],
    ];
    return '<dl class="grid grid-cols-[160px_1fr] gap-x-3 gap-y-1 text-xs">' + rows.map((r) => '<dt class="text-muted-foreground">' + esc(r[0]) + '</dt><dd class="break-all">' + esc(r[1] === null || r[1] === undefined ? '-' : r[1]) + '</dd>').join('') + '</dl>';
}

function section(title, body) {
    return '<section class="space-y-2"><h4 class="text-sm font-semibold">' + esc(title) + '</h4>' + body + '</section>';
}

function jobActions(job) {
    const buttons = [];
    if (job.can_recheck) buttons.push(button('jobRecheck', 'Re-check', { id: job.id }, BTN));
    if (job.status === 'done' || job.status === 'failed') {
        buttons.push('<input id="recoverAccount" class="h-8 rounded-md border border-input px-2 text-xs" placeholder="account id or label">');
        buttons.push(button('jobRecover', 'Recover', { id: job.id }, BTN));
        buttons.push(button('jobRegenerate', 'Regenerate', { id: job.id }, BTN));
    }
    if (job.callback.status === 'failed') buttons.push(button('jobResendWebhook', 'Resend webhook', { id: job.id }, BTN));
    if (job.media.some((m) => m.url)) buttons.push(button('jobRevokeLinks', 'Revoke share links', { id: job.id }, BTN));
    if (job.external_ref && job.account_label) buttons.push(button('jobFetchTitle', 'Fetch title', { id: job.id }, BTN));
    return '<div class="flex flex-wrap items-center gap-2">' + buttons.join('') + '</div>';
}

async function showJob(id) {
    const job = await api('/jobs/' + id);
    const debug = job.debug_files.length
        ? '<div class="flex flex-wrap gap-2">' + job.debug_files.map((d) => button('openDebug', 'File ' + d.idx + ' (' + d.mime + ')', { job: job.id, idx: d.idx })).join('') + '</div><pre id="debugView" class="hidden mt-2 max-h-72 overflow-auto rounded border border-border bg-muted p-2 text-xs whitespace-pre-wrap"></pre>'
        : '<p class="text-sm text-muted-foreground">None</p>';
    openModal('Job ' + job.id.slice(0, 8), '<div class="space-y-4" id="jobBox" data-id="' + esc(job.id) + '">' +
        conclusion(job) +
        section('Title', '<div class="flex gap-2"><input id="jobTitle" class="' + FIELD + '" value="' + esc(job.title || '') + '">' + button('renameJob', 'Rename', { id: job.id }, BTN) + '</div>') +
        section('Prompt', '<pre class="max-h-40 overflow-auto rounded border border-border bg-muted p-2 text-xs whitespace-pre-wrap" id="jobPrompt"></pre>' + button('copyPrompt', 'Copy prompt', {}, BTN)) +
        section('Actions', jobActions(job)) +
        section('Results', mediaHtml(job, 'output')) +
        section('References', mediaHtml(job, 'input')) +
        section('Debug files', debug) +
        section('Timeline', timelineHtml(job)) +
        section('Technical', technicalHtml(job)) + '</div>', true);
    document.getElementById('jobPrompt').textContent = job.prompt;     // the customer's words only: no frame, no marker
}

ACTIONS.openJob = (el, event) => (event.target.closest('input, label') ? null : showJob(el.dataset.id));
ACTIONS.copyPrompt = async () => {
    await navigator.clipboard.writeText(document.getElementById('jobPrompt').textContent);
    toast('Prompt copied', 'success');
};
ACTIONS.renameJob = async (el) => {
    await api('/jobs/' + el.dataset.id, { method: 'PATCH', body: { title: document.getElementById('jobTitle').value } });
    toast('Renamed', 'success');
    await loadJobs();
};
ACTIONS.jobRecheck = async (el) => {
    const out = await api('/jobs/' + el.dataset.id + '/recheck', { method: 'POST' });
    toast(out.recharged ? 'Re-checked: charged again' : 'Re-checked', 'success');
    await showJob(el.dataset.id);
    await loadJobs();
};
ACTIONS.jobRecover = async (el) => {
    const account = document.getElementById('recoverAccount').value.trim();
    if (!account) { toast('Enter the account id or label to recover from', 'error'); return; }
    const out = await api('/jobs/' + el.dataset.id + '/recover', { method: 'POST', body: { account } });
    await pollCommand(out.command_id, 'Recover');
    await showJob(el.dataset.id);
};
ACTIONS.jobFetchTitle = async (el) => {
    const out = await api('/jobs/' + el.dataset.id + '/fetch-title', { method: 'POST' });
    await pollCommand(out.command_id, 'Fetch title');
    await showJob(el.dataset.id);
    await loadJobs();
};
ACTIONS.jobRegenerate = async (el) => {
    if (!confirm('Regenerate makes a NEW job for the same key at today’s price, and charges it. Continue?')) return;
    const out = await regenerateCall('job:' + el.dataset.id, '/jobs/' + el.dataset.id + '/regenerate', {});
    toast((out.replayed ? 'Already made: job ' : 'New job ') + out.id.slice(0, 8) + ' (' + out.status + ')', 'success');
    await loadJobs();
};
ACTIONS.downloadMedia = async (el) => {
    const response = await api('/jobs/' + el.dataset.job + '/content/' + el.dataset.idx, { raw: true });
    if (!response.ok) throw new ApiError(response.status, null);
    const link = document.createElement('a');
    link.href = URL.createObjectURL(await response.blob());
    link.download = 'job-' + el.dataset.job.slice(0, 8) + '-' + el.dataset.idx;
    document.body.appendChild(link);
    link.click();
    link.remove();
};
// A debug file is a raster image or text. Text is shown with textContent, never parsed as markup.
ACTIONS.openDebug = async (el) => {
    const response = await api('/jobs/' + el.dataset.job + '/debug/' + el.dataset.idx, { raw: true });
    if (!response.ok) throw new ApiError(response.status, null);
    const type = (response.headers.get('content-type') || '').split(';')[0];
    if (type.startsWith('image/')) { window.open(URL.createObjectURL(await response.blob()), '_blank', 'noopener'); return; }
    const view = document.getElementById('debugView');
    view.classList.remove('hidden');
    view.textContent = await response.text();
};
ACTIONS.jobResendWebhook = async (el) => {
    await api('/jobs/' + el.dataset.id + '/resend-webhook', { method: 'POST' });
    toast('Webhook queued again', 'success');
    await showJob(el.dataset.id);
};
ACTIONS.jobRevokeLinks = async (el) => {
    if (!confirm('Every share link of this job stops working. Continue?')) return;
    const out = await api('/jobs/' + el.dataset.id + '/revoke-links', { method: 'POST' });
    toast('Revoked ' + out.revoked + ' link(s)', 'success');
    await showJob(el.dataset.id);
};
