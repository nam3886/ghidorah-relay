'use strict';

// Request logs tab: one row per request a known key made. Dates are the viewer's own midnights (half-open on the
// server), the search looks at job id prefix, upstream reference prefix and title.

const LOGS_PAGE = 50;
const logsView = { offset: 0, items: [], status: 'all', from: '', to: '', q: '' };

function statusTone(code) { return code < 400 ? 'green' : code < 500 ? 'amber' : 'red'; }

const LOG_COLUMNS = [
    { key: 'time', label: 'Time', align: 'left', cell: (l) => esc(fmtTime(l.at_ms)) },
    { key: 'status', label: 'Status', align: 'center', cell: (l) => badge(l.status_code, statusTone(l.status_code)) },
    { key: 'route', label: 'Route', align: 'left', cell: (l) => '<span class="font-mono text-xs">' + esc(l.route) + '</span>' },
    { key: 'provider', label: 'Provider', align: 'left', cell: (l) => esc(l.provider || '-') },
    { key: 'key', label: 'Key', align: 'left', cell: (l) => esc(l.key_label || '-') },
    { key: 'job', label: 'Job', align: 'left', cell: (l) => l.job_id ? '<span class="font-mono text-xs">' + esc(l.job_id.slice(0, 8)) + '</span><div class="text-xs text-muted-foreground max-w-[200px] truncate">' + esc(l.job_title || '') + '</div>' : badge('no job', 'gray') },
    { key: 'error', label: 'Error', align: 'left', cell: (l) => l.error_code ? '<span class="text-xs text-destructive">' + esc(l.error_code) + '</span>' : '-' },
    { key: 'duration', label: 'Duration', align: 'right', cell: (l) => esc(l.duration_ms + ' ms') },
    { key: 'actions', label: 'Actions', align: 'right', cell: (l) => button('openLog', 'Details', { id: l.id }) },
];

registerTab({
    id: 'logs',
    label: 'Request logs',
    load: async (panel) => {
        const statuses = [['all', 'All'], ['ok', 'OK (below 400)'], ['error', 'Errors'], ['pre_job', 'Failed before a job']];
        const options = statuses.map((s) => '<option value="' + s[0] + '"' + (s[0] === logsView.status ? ' selected' : '') + '>' + s[1] + '</option>').join('');
        panel.innerHTML = '<div class="rounded-lg border border-border bg-background" data-pager>' +
            '<div class="flex flex-wrap items-center justify-between gap-3 p-4 border-b border-border"><h3 class="text-lg font-semibold">Request logs</h3>' +
            '<div class="flex flex-wrap items-center gap-2">' +
            '<input id="logsQuery" data-enter="searchLogs" placeholder="Job id, reference or title" class="h-8 w-56 rounded-md border border-input px-2 text-xs" value="' + esc(logsView.q) + '">' +
            '<select id="logsStatus" data-change="searchLogs" title="The request\u2019s own outcome. The Jobs tab filters by job status." class="h-8 rounded-md border border-input px-2 text-xs">' + options + '</select>' +
            '<input id="logsFrom" type="date" data-change="searchLogs" class="h-8 rounded-md border border-input px-2 text-xs" value="' + esc(logsView.from) + '">' +
            '<input id="logsTo" type="date" data-change="searchLogs" class="h-8 rounded-md border border-input px-2 text-xs" value="' + esc(logsView.to) + '">' +
            '<button data-act="searchLogs" class="' + BTN + '">Search</button>' +
            '<button data-act="clearLogs" class="' + BTN + ' text-destructive">Clear logs</button></div></div>' +
            '<div id="logsTable"></div><div id="logsPager"></div></div>';
        panel.firstChild.pager = { move: (dir) => { logsView.offset = Math.max(0, logsView.offset + dir * LOGS_PAGE); return loadLogs(); } };
        logsView.offset = 0;
        await loadLogs();
    },
});

// A date field is a day; the window is [that day's midnight, the next day's midnight) in the viewer's time zone.
function dayStart(value) {
    const [y, m, d] = value.split('-').map(Number);
    return new Date(y, m - 1, d).getTime();
}
function dayAfter(value) {
    const [y, m, d] = value.split('-').map(Number);
    return new Date(y, m - 1, d + 1).getTime();
}

async function loadLogs() {
    const parts = ['limit=' + LOGS_PAGE, 'offset=' + logsView.offset, 'status=' + logsView.status];
    if (state.provider) parts.push(providerParam());
    if (logsView.q) parts.push('q=' + encodeURIComponent(logsView.q));
    if (logsView.from) parts.push('since_ms=' + dayStart(logsView.from));
    if (logsView.to) parts.push('until_ms=' + dayAfter(logsView.to));
    const data = await api('/logs?' + parts.join('&'));
    logsView.items = data.items;
    document.getElementById('logsTable').innerHTML = tableHtml(LOG_COLUMNS, data.items, 'No log rows', (l) => ' data-act="openLog" data-id="' + esc(l.id) + '"');
    document.getElementById('logsPager').innerHTML = pagerHtml({ offset: logsView.offset, limit: LOGS_PAGE, total: data.total });
}

ACTIONS.searchLogs = () => {
    logsView.q = document.getElementById('logsQuery').value.trim();
    logsView.status = document.getElementById('logsStatus').value;
    logsView.from = document.getElementById('logsFrom').value;
    logsView.to = document.getElementById('logsTo').value;
    logsView.offset = 0;
    return loadLogs();
};

ACTIONS.clearLogs = async () => {
    if (!confirm('Clear every request log row? Jobs, the ledger and alerts are kept.')) return;
    const out = await api('/logs', { method: 'DELETE' });
    toast('Cleared ' + out.deleted + ' rows', 'success');
    logsView.offset = 0;
    await loadLogs();
};

ACTIONS.openLog = async (el) => {
    const log = await api('/logs/' + el.dataset.id);
    openModal('Request ' + log.id, '<div class="space-y-3 text-sm"><div>' + badge(log.status_code, statusTone(log.status_code)) + ' <span class="font-mono text-xs">' + esc(log.route) + '</span></div>' +
        '<h4 class="font-semibold">Request</h4><pre id="logRequest" class="max-h-60 overflow-auto rounded border border-border bg-muted p-2 text-xs whitespace-pre-wrap"></pre>' +
        '<h4 class="font-semibold">Answer</h4><pre id="logResponse" class="max-h-60 overflow-auto rounded border border-border bg-muted p-2 text-xs whitespace-pre-wrap"></pre></div>', true);
    document.getElementById('logRequest').textContent = JSON.stringify(log.request, null, 2);
    document.getElementById('logResponse').textContent = log.response === null ? '(none kept)' : JSON.stringify(log.response, null, 2);
};
