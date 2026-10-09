'use strict';

// Accounts tab. Delete, check, Check all, proxy re-check and wake are commands run by the provider's worker: the
// page queues one and polls its status. A used account can only be disabled, and its switch is hidden while a
// worker holds it.

const ACCOUNTS_PAGE = 50;
const STATE_TONE = { available: 'green', dead: 'red', disabled: 'gray' };

function accountStatus(a) {
    let html = badge(a.state, STATE_TONE[a.state] || 'gray');
    if (a.in_use) html += ' ' + badge('in use', 'blue');
    if (a.state_reason) html += '<div class="text-xs text-muted-foreground">' + esc(a.state_reason) + '</div>';
    return html;
}

function accountPoints(a) {
    if (!a.capacity.length) return '-';
    return a.capacity.map((c) => '<div class="text-xs whitespace-nowrap">' + esc(c.pool) + ': <span class="font-medium">' +
        esc(c.left_today === null || c.left_today === undefined ? '?' : c.left_today) + '</span>' +
        (c.budget !== null && c.budget !== undefined ? ' / ' + esc(c.budget) : '') +
        (c.drift === true ? ' ' + badge('drift', 'red') : '') + '</div>').join('');
}

function accountAsleep(a) {
    const parts = [];
    if (a.cooldown_ms && a.cooldown_ms > Date.now()) parts.push('cooldown until ' + fmtTime(a.cooldown_ms) + (a.cooldown_reason ? ' (' + a.cooldown_reason + ')' : ''));
    for (const c of a.capacity) {
        if (c.rest_until_ms && c.rest_until_ms > Date.now()) parts.push(c.pool + ' rests until ' + fmtTime(c.rest_until_ms) + (c.rest_reason ? ' (' + c.rest_reason + ')' : ''));
    }
    return parts.length ? parts.map((p) => '<div class="text-xs">' + esc(p) + '</div>').join('') : '-';
}

function accountProxy(a) {
    if (!a.proxy_host) return '-';
    return esc(a.proxy_host + ':' + a.proxy_port) + (a.has_proxy_auth ? ' ' + badge('auth', 'gray') : '');
}

function accountProxyCheck(a) {
    if (!a.proxy_host) return '-';
    const tone = a.proxy_ok === true ? 'green' : a.proxy_ok === false ? 'red' : 'gray';
    const label = a.proxy_ok === true ? 'ok' : a.proxy_ok === false ? 'failed' : 'not checked';
    return badge(label, tone) + (a.proxy_checked_ms ? '<div class="text-xs text-muted-foreground">' + esc(fmtTime(a.proxy_checked_ms)) + '</div>' : '');
}

const ACCOUNT_COLUMNS = [
    { key: 'account', label: 'Account', align: 'left', cell: (a) => '<div class="font-medium">' + esc(a.label) + '</div><div class="text-xs text-muted-foreground">' + esc(a.display_name || a.identity || '') + ' · ' + esc(a.provider) + (a.composer ? ' · composer ' + esc(a.composer) : '') + '</div>' },
    { key: 'status', label: 'Status', align: 'center', cell: accountStatus },
    { key: 'check', label: 'Account check', align: 'center', cell: (a) => esc(fmtTime(a.last_checked_ms)) },
    { key: 'points', label: 'Points left / budget', align: 'center', cell: accountPoints },
    { key: 'asleep', label: 'Asleep', align: 'center', cell: accountAsleep },
    { key: 'videos', label: 'Videos', align: 'center', cell: (a) => esc(a.made.video) },
    { key: 'images', label: 'Images', align: 'center', cell: (a) => esc(a.made.image) },
    { key: 'proxy', label: 'Proxy', align: 'center', cell: accountProxy },
    { key: 'proxy_check', label: 'Proxy check', align: 'center', cell: accountProxyCheck },
    { key: 'last_error', label: 'Last error', align: 'left', cell: (a) => a.last_error ? '<div class="max-w-[260px] truncate text-xs text-destructive" title="' + esc(a.last_error) + '">' + esc(a.last_error) + '</div>' : '-' },
    { key: 'actions', label: 'Actions', align: 'right', cell: (a) => '<div class="flex items-center justify-end gap-1.5">' +
        (a.in_use ? '' : switchHtml(a.state !== 'disabled', 'toggleAccount', a.id, false)) +
        button('checkAccount', 'Check', { id: a.id }) +
        (a.has_screenshot ? button('showShot', 'Shot', { id: a.id }) : '') +
        button('wakeAccount', 'Wake', { id: a.id }) +
        button('editAccount', 'Edit', { id: a.id }) +
        button('deleteAccount', 'Delete', { id: a.id }, BTN_SMALL + ' text-destructive') + '</div>' },
];

const accountsView = { offset: 0, items: [], timer: null };
const AUTO_REFRESH_MS = 10000;

registerTab({
    id: 'accounts',
    label: 'Accounts',
    load: async (panel) => {
        panel.innerHTML = '<div class="rounded-lg border border-border bg-background" data-pager>' +
            '<div class="flex items-center justify-between gap-4 p-4 border-b border-border"><h3 class="text-lg font-semibold">Accounts</h3>' +
            '<div class="flex items-center gap-3">' +
            '<label class="flex items-center gap-2 text-xs text-muted-foreground">Auto-refresh' + switchHtml(accountsView.timer !== null, 'toggleAutoRefresh', 'accounts', false) + '</label>' +
            '<button data-act="checkAll" class="' + BTN + '" title="Check every enabled account, one at a time">Check all</button>' +
            '<button data-act="refreshCatalog" class="' + BTN + '" title="Read the provider\u2019s models and options again">Refresh catalog</button>' +
            '<button data-act="refreshAccounts" class="' + BTN + '">Refresh</button>' +
            '<button data-act="openImport" class="' + BTN_PRIMARY + '">Import</button></div></div>' +
            '<div id="accountsTable"></div><div id="accountsPager"></div></div>';
        panel.firstChild.pager = { move: (dir) => { accountsView.offset = Math.max(0, accountsView.offset + dir * ACCOUNTS_PAGE); return loadAccounts(); } };
        accountsView.offset = 0;
        await loadAccounts();
    },
});

async function loadAccounts() {
    const query = 'limit=' + ACCOUNTS_PAGE + '&offset=' + accountsView.offset + (state.provider ? '&' + providerParam() : '');
    const data = await api('/accounts?' + query);
    accountsView.items = data.items;
    document.getElementById('accountsTable').innerHTML = tableHtml(ACCOUNT_COLUMNS, data.items, 'No accounts');
    document.getElementById('accountsPager').innerHTML = pagerHtml({ offset: accountsView.offset, limit: ACCOUNTS_PAGE, total: data.total });
}

function accountById(id) { return accountsView.items.find((a) => a.id === id); }

async function queue(promise, label) {
    const out = await promise;
    toast(label + ': queued', 'info');
    await pollCommand(out.command_id, label);
    await loadAccounts();
}

ACTIONS.refreshAccounts = () => loadAccounts();
// The timer only runs while the Accounts tab is the open one and no modal is up.
ACTIONS.toggleAutoRefresh = (el) => {
    clearInterval(accountsView.timer);
    accountsView.timer = null;
    if (el.checked) {
        accountsView.timer = setInterval(() => {
            if (state.tab === 'accounts' && !document.getElementById('modalBody')) runBusy(null, loadAccounts);
        }, AUTO_REFRESH_MS);
    }
};
ACTIONS.checkAccount = (el) => queue(api('/accounts/' + el.dataset.id + '/check', { method: 'POST' }), 'Check');
ACTIONS.wakeAccount = (el) => queue(api('/accounts/' + el.dataset.id + '/wake', { method: 'POST' }), 'Clear cooldown');
ACTIONS.deleteAccount = async (el) => {
    const a = accountById(el.dataset.id);
    if (!confirm('Delete account "' + (a ? a.label : '') + '"? A used account cannot be deleted: disable it instead.')) return;
    await queue(api('/accounts/' + el.dataset.id, { method: 'DELETE' }), 'Delete');
};
ACTIONS.checkAll = async () => {
    const names = state.provider ? [state.provider] : await providerNames();
    for (const name of names) await queue(api('/providers/' + encodeURIComponent(name) + '/check-all', { method: 'POST' }), 'Check all ' + name);
};
ACTIONS.refreshCatalog = async () => {
    const names = state.provider ? [state.provider] : await providerNames();
    for (const name of names) await queue(api('/providers/' + encodeURIComponent(name) + '/refresh-catalog', { method: 'POST' }), 'Refresh catalog ' + name);
};
ACTIONS.toggleAccount = async (el) => {
    try {
        await api('/accounts/' + el.dataset.id, { method: 'PATCH', body: { enabled: el.checked } });
    } finally {
        await loadAccounts();
    }
};
ACTIONS.showShot = async (el) => {
    const response = await api('/accounts/' + el.dataset.id + '/screenshot', { raw: true });
    if (!response.ok) throw new ApiError(response.status, null);
    window.open(URL.createObjectURL(await response.blob()), '_blank', 'noopener');
};

// ---- import -------------------------------------------------------------------------------------------------
ACTIONS.openImport = async () => {
    const names = await providerNames();
    const options = names.map((n) => '<option value="' + esc(n) + '"' + (n === state.provider ? ' selected' : '') + '>' + esc(n) + '</option>').join('');
    openModal('Import accounts',
        '<div class="space-y-3"><label class="text-sm font-medium" for="importProvider">Provider</label>' +
        '<select id="importProvider" class="' + FIELD + '">' + options + '</select>' +
        '<label class="text-sm font-medium" for="importCredentials">Credentials (one per line)</label>' +
        '<textarea id="importCredentials" rows="8" class="' + FIELD + ' h-auto font-mono text-xs"></textarea>' +
        '<label class="text-sm font-medium" for="importProxy">Proxy for all (optional, host:port:user:pass)</label>' +
        '<input id="importProxy" class="' + FIELD + '" autocomplete="off">' +
        '<div id="importResult" class="text-sm"></div>' +
        '<div class="flex justify-end"><button data-act="submitImport" class="' + BTN_PRIMARY + '">Import</button></div></div>');
};

ACTIONS.submitImport = async () => {
    const provider = document.getElementById('importProvider').value;
    const proxy = document.getElementById('importProxy').value.trim();
    const lines = document.getElementById('importCredentials').value.split('\n').map((l) => l.trim()).filter(Boolean);
    if (!lines.length) { toast('Paste at least one credential', 'error'); return; }
    const items = lines.map((credential) => proxy ? { credential, proxy } : { credential });
    const out = await api('/accounts/import', { method: 'POST', body: { provider, items } });
    document.getElementById('importCredentials').value = '';
    document.getElementById('importResult').textContent = 'Imported ' + out.imported.length + ', rejected ' + out.rejected.length +
        (out.rejected.length ? ' (' + out.rejected.map((r) => 'line ' + (r.index + 1) + ': ' + r.reason).join('; ') + ')' : '');
    await loadAccounts();
};

// ---- edit ---------------------------------------------------------------------------------------------------
ACTIONS.editAccount = (el) => {
    const a = accountById(el.dataset.id);
    if (!a) return;
    openModal('Edit account',
        '<div class="space-y-3" data-id="' + esc(a.id) + '" id="editBox"><label class="text-sm font-medium" for="editLabel">Label</label>' +
        '<input id="editLabel" class="' + FIELD + '" value="' + esc(a.label) + '">' +
        '<label class="text-sm font-medium" for="editProxy">Proxy (empty removes it; the old password is never shown)</label>' +
        '<input id="editProxy" class="' + FIELD + '" value="' + esc(a.proxy_host ? a.proxy_host + ':' + a.proxy_port : '') + '" autocomplete="off">' +
        '<div class="flex justify-end gap-2">' + button('recheckProxy', 'Re-check proxy', { id: a.id }, BTN) +
        '<button data-act="saveAccount" class="' + BTN_PRIMARY + '">Save</button></div></div>');
};

ACTIONS.saveAccount = async () => {
    const id = document.getElementById('editBox').dataset.id;
    const a = accountById(id);
    const body = {};
    const label = document.getElementById('editLabel').value;
    const proxy = document.getElementById('editProxy').value.trim();
    if (label !== a.label) body.label = label;
    const oldProxy = a.proxy_host ? a.proxy_host + ':' + a.proxy_port : '';
    if (proxy !== oldProxy) body.proxy = proxy || null;
    if (!Object.keys(body).length) { closeModal(); return; }
    const out = await api('/accounts/' + id, { method: 'PATCH', body });
    closeModal();
    toast('Saved', 'success');
    if (out.command_id) await pollCommand(out.command_id, 'Proxy check');
    await loadAccounts();
};

ACTIONS.recheckProxy = async (el) => {
    closeModal();
    await queue(api('/accounts/' + el.dataset.id + '/proxy-recheck', { method: 'POST' }), 'Proxy check');
};
