'use strict';

// API keys tab: per-provider wallets (closed / limit / explicit unlimited), allow-list, max unfinished jobs, the raw
// key shown once at creation, enable switch, delete (only a key with no jobs; any other key is disabled instead).

const KEYS_PAGE = 50;
const keysView = { items: [], offset: 0 };

function walletText(w) {
    if (w.mode === 'closed') return 'closed';
    if (w.mode === 'unlimited') return 'unlimited (' + w.used_total + ' used)';
    const day = w.day_limit === null ? '' : 'day ' + w.used_day + '/' + w.day_limit;
    const total = w.total_limit === null ? '' : 'total ' + w.used_total + '/' + w.total_limit;
    return [day, total].filter(Boolean).join(', ');
}

const KEY_COLUMNS = [
    { key: 'label', label: 'Label', align: 'left', cell: (k) => '<div class="font-medium">' + esc(k.label || '(no label)') + '</div><div class="flex items-center gap-1.5 text-xs text-muted-foreground font-mono">' + esc(k.prefix) + button('copyText', 'Copy', { copy: k.prefix }, BTN_SMALL + ' h-5', 'Copy the key prefix (the full key is not stored)') + '</div>' },
    { key: 'status', label: 'Status', align: 'center', cell: (k) => badge(k.enabled ? 'enabled' : 'disabled', k.enabled ? 'green' : 'gray') },
    { key: 'models', label: 'Models', align: 'left', cell: (k) => k.models.includes('*') ? badge('any model', 'blue') : '<div class="max-w-[260px] text-xs">' + esc(k.models.join(', ')) + '</div>' },
    { key: 'wallets', label: 'Wallets', align: 'left', cell: (k) => k.wallets.map((w) => '<div class="text-xs whitespace-nowrap">' + esc(w.provider + ' ' + w.kind) + ': ' + esc(walletText(w)) + '</div>').join('') || '-' },
    { key: 'unfinished', label: 'Unfinished / max', align: 'center', cell: (k) => esc(k.unfinished + ' / ' + (k.max_unfinished_effective === 0 ? 'unlimited' : k.max_unfinished_effective)) },
    { key: 'jobs', label: 'Jobs total', align: 'center', cell: (k) => esc(k.jobs) },
    { key: 'created', label: 'Created', align: 'left', cell: (k) => esc(fmtTime(k.created_ms)) },
    { key: 'actions', label: 'Actions', align: 'right', cell: (k) => '<div class="flex items-center justify-end gap-1.5">' +
        switchHtml(k.enabled, 'toggleKey', k.id, false) + button('editKey', 'Edit', { id: k.id }) +
        button('deleteKey', 'Delete', { id: k.id }, BTN_SMALL + ' text-destructive') + '</div>' },
];

registerTab({
    id: 'keys',
    label: 'API keys',
    load: async (panel) => {
        panel.innerHTML = '<div class="rounded-lg border border-border bg-background" data-pager>' +
            '<div class="flex items-center justify-between gap-4 p-4 border-b border-border"><h3 class="text-lg font-semibold">API keys</h3>' +
            '<div class="flex items-center gap-3"><button data-act="newKey" class="' + BTN_PRIMARY + '">New key</button>' +
            '<button data-act="refreshKeys" class="' + BTN + '">Refresh</button></div></div>' +
            '<div id="newKeyBox" class="hidden m-4 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm"></div>' +
            '<div id="keysTable"></div><div id="keysPager"></div></div>';
        panel.firstChild.pager = { move: (dir) => { keysView.offset = Math.max(0, keysView.offset + dir * KEYS_PAGE); return showKeys(); } };
        keysView.offset = 0;
        await loadKeys();
    },
});

async function loadKeys() {
    const data = await api('/keys');
    keysView.items = data.items;
    await showKeys();
}

// The keys list answers whole (a deployment has a handful of keys); the page shows it a screenful at a time.
async function showKeys() {
    const page = keysView.items.slice(keysView.offset, keysView.offset + KEYS_PAGE);
    document.getElementById('keysTable').innerHTML = tableHtml(KEY_COLUMNS, page, 'No API keys');
    document.getElementById('keysPager').innerHTML = pagerHtml({ offset: keysView.offset, limit: KEYS_PAGE, total: keysView.items.length });
}

function keyById(id) { return keysView.items.find((k) => k.id === id); }

ACTIONS.refreshKeys = () => loadKeys();
ACTIONS.toggleKey = async (el) => {
    try {
        await api('/keys/' + el.dataset.id + (el.checked ? '/enable' : '/disable'), { method: 'POST' });
    } finally {
        await loadKeys();
    }
};
ACTIONS.deleteKey = async (el) => {
    const k = keyById(el.dataset.id);
    if (!confirm('Delete key "' + (k ? k.label || k.prefix : '') + '"? A key that has jobs is only disabled.')) return;
    const out = await api('/keys/' + el.dataset.id, { method: 'DELETE' });
    await loadKeys();
    toast(out.result === 'deleted' ? 'Key deleted' : 'Key has jobs: disabled instead', 'info');
};

// ---- create / edit form -------------------------------------------------------------------------------------
async function walletPairs() {
    const seen = new Set();
    for (const m of (await catalog())) seen.add(m.owned_by + '\u0000' + m.kind);
    return [...seen].sort().map((s) => s.split('\u0000'));
}

function walletRow(provider, kind, w) {
    const mode = w ? w.mode : 'closed';
    const opts = ['closed', 'limit', 'unlimited'].map((m) => '<option value="' + m + '"' + (m === mode ? ' selected' : '') + '>' + m + '</option>').join('');
    return '<div class="flex items-center gap-2" data-wallet data-provider="' + esc(provider) + '" data-kind="' + esc(kind) + '">' +
        '<span class="w-32 text-sm">' + esc(provider + ' ' + kind) + '</span><select class="h-8 rounded-md border border-input px-2 text-xs" data-f="mode">' + opts + '</select>' +
        '<input type="number" min="1" placeholder="per day" class="h-8 w-24 rounded-md border border-input px-2 text-xs" data-f="day" value="' + esc(w && w.day_limit ? w.day_limit : '') + '">' +
        '<input type="number" min="1" placeholder="total" class="h-8 w-24 rounded-md border border-input px-2 text-xs" data-f="total" value="' + esc(w && w.total_limit ? w.total_limit : '') + '"></div>';
}

async function keyForm(k) {
    const models = await catalog();
    const any = !k || k.models.includes('*');
    const boxes = models.map((m) => '<label class="flex items-center gap-2 text-xs"><input type="checkbox" data-model="' + esc(m.id) + '"' + (k && k.models.includes(m.id) ? ' checked' : '') + '> ' + esc(m.id) + ' <span class="text-muted-foreground">' + esc(m.owned_by) + '</span></label>').join('');
    const byPair = {};
    for (const w of (k ? k.wallets : [])) byPair[w.provider + '\u0000' + w.kind] = w;
    const rows = (await walletPairs()).map(([p, kind]) => walletRow(p, kind, byPair[p + '\u0000' + kind])).join('');
    return '<div class="space-y-3" id="keyForm" data-id="' + esc(k ? k.id : '') + '">' +
        '<label class="text-sm font-medium" for="keyLabel">Label</label><input id="keyLabel" class="' + FIELD + '" value="' + esc(k ? k.label : '') + '">' +
        '<label class="text-sm font-medium" for="keyMaxUnfinished">Max unfinished jobs (empty = default, 0 = unlimited)</label>' +
        '<input id="keyMaxUnfinished" type="number" min="0" class="' + FIELD + '" value="' + esc(k && k.max_unfinished !== null ? k.max_unfinished : '') + '">' +
        '<div class="text-sm font-medium">Allowed models</div><label class="flex items-center gap-2 text-sm"><input type="checkbox" id="keyAnyModel"' + (any ? ' checked' : '') + '> Any model</label>' +
        '<div class="grid grid-cols-2 gap-1 max-h-40 overflow-y-auto">' + boxes + '</div>' +
        '<div class="text-sm font-medium">Wallets (a wallet left closed refuses that kind)</div><div class="space-y-1">' + rows + '</div>' +
        '<div class="flex justify-end"><button data-act="saveKey" class="' + BTN_PRIMARY + '">' + (k ? 'Save' : 'Create') + '</button></div></div>';
}

ACTIONS.newKey = async () => openModal('New API key', await keyForm(null));
ACTIONS.editKey = async (el) => openModal('Edit API key', await keyForm(keyById(el.dataset.id)));

function readKeyForm() {
    const form = document.getElementById('keyForm');
    const models = document.getElementById('keyAnyModel').checked ? ['*'] : [...form.querySelectorAll('[data-model]:checked')].map((b) => b.dataset.model);
    const wallets = [...form.querySelectorAll('[data-wallet]')].map((row) => {
        const mode = row.querySelector('[data-f=mode]').value;
        const w = { provider: row.dataset.provider, kind: row.dataset.kind, mode };
        if (mode === 'limit') {
            const day = row.querySelector('[data-f=day]').value;
            const total = row.querySelector('[data-f=total]').value;
            if (day) w.day_limit = Number(day);
            if (total) w.total_limit = Number(total);
        }
        return w;
    });
    const max = document.getElementById('keyMaxUnfinished').value;
    return { label: document.getElementById('keyLabel').value, models, max_unfinished: max === '' ? null : Number(max), wallets };
}

ACTIONS.saveKey = async () => {
    const id = document.getElementById('keyForm').dataset.id;
    const body = readKeyForm();
    const out = id ? await api('/keys/' + id, { method: 'PATCH', body }) : await api('/keys', { method: 'POST', body });
    closeModal();
    if (!id) {
        // The raw key exists only in this answer: shown once, as text, and gone on the next reload.
        const box = document.getElementById('newKeyBox');
        box.classList.remove('hidden');
        box.innerHTML = '<p class="font-medium">Copy this key now, it is not shown again:</p>' +
            '<div class="mt-1 flex items-start gap-2"><code class="block flex-1 break-all font-mono text-xs select-all" id="newKeyValue"></code>' +
            button('copyText', 'Copy', { from: '#newKeyValue' }, BTN) + '</div>' +
            '<p class="mt-1 text-xs text-muted-foreground">Only a hash is stored: once this box is gone (a reload, another tab) the key cannot be shown again.</p>';
        document.getElementById('newKeyValue').textContent = out.key;
    }
    await loadKeys();
};
