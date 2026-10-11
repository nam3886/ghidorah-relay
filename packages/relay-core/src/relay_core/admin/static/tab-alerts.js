'use strict';

// Alerts tab: every stored alert kind (billing clamps and drifts, unclear provider replies) grouped by the hub API, with
// an acknowledge per group, and beside them the live conditions the dashboard computes (no acknowledge: they clear
// themselves). Titles and reply texts hold customer-influenced words, so every one reaches the page as escaped text.

// `asked` is what the operator asked for last (filters, and the group whose detail is open); everything drawn
// (`groups`, `opened`) comes from one answer. A write takes its group from what was drawn, never from
// `asked`. Every load takes the next `gen`, and an answer is drawn only if no later load started since it was asked
// (`alertsCurrent`).
const alertsView = { asked: { status: 'open', kind: '', open: null }, groups: [], opened: null, gen: 0, loading: 0, busy: false };
const SEVERITY_TONE = { error: 'red', warning: 'amber' };
// ponytail: a kind added to the hub later is listed under "All kinds"; add it here to filter on it.
const ALERT_KINDS = ['clamp', 'drift', 'unclear_reply'];
const GROUP_LIMIT = 200, ROW_LIMIT = 100;      // the page sizes it asks for; a full page says there may be more
const EMPTY_WORDING_TITLE = "(no reply text read: the provider's API gave no text)";

function alertsPath(g, tail) {
    return '/alerts/groups/' + encodeURIComponent(g.kind) + '/' + encodeURIComponent(g.group) + (tail || '');
}

function isEmptyWording(g) { return g.kind === 'unclear_reply' && g.title === EMPTY_WORDING_TITLE; }

function groupTitle(g) {
    return isEmptyWording(g) ? 'API read failure: no reply text was read' : g.title;
}

// A wording seen under one key only may still be one customer's own words; a billing group is one wallet, not a wording.
function oneCustomer(g) {
    return g.kind === 'unclear_reply' && g.keys === 1 && !isEmptyWording(g)
        ? '<div class="text-xs text-amber-900">one customer only: may be the customer\'s own words</div>' : '';
}

const ALERT_COLUMNS = [
    { key: 'severity', label: 'Severity', align: 'left', cell: (g) => badge(g.severity, SEVERITY_TONE[g.severity] || 'gray') },
    { key: 'title', label: 'Alert', align: 'left', cell: (g) => '<div class="font-medium break-all">' + esc(groupTitle(g)) + '</div><div class="text-xs text-muted-foreground">' + esc(g.kind) + '</div>' + oneCustomer(g) },
    { key: 'provider', label: 'Provider', align: 'left', cell: (g) => esc(g.provider) },
    { key: 'first', label: 'First seen', align: 'left', cell: (g) => esc(fmtTime(g.first_ms)) },
    { key: 'last', label: 'Last seen', align: 'left', cell: (g) => esc(fmtTime(g.last_ms)) },
    { key: 'count', label: 'Rows', align: 'right', cell: (g) => esc(g.count) },
    { key: 'open', label: 'Open', align: 'right', cell: (g) => esc(g.open_count) },
    { key: 'keys', label: 'Keys', align: 'right', cell: (g) => esc(g.keys) },
    { key: 'actions', label: '', align: 'right', cell: (g) => button('alertOpen', 'Details', { kind: g.kind, group: g.group }) + (g.open_count > 0 ? ' ' + button('alertAck', 'Acknowledge', { kind: g.kind, group: g.group }) : '') },
];

const UNCLEAR_COLUMNS = [
    { key: 'at', label: 'Seen', align: 'left', cell: (i) => esc(fmtTime(i.at_ms)) },
    { key: 'text', label: 'Stored reply', align: 'left', cell: (i) => '<span class="whitespace-pre-wrap break-all">' + esc(i.text === '' ? '(no text)' : i.text) + '</span>' },
    { key: 'job', label: 'Job', align: 'left', cell: (i) => button('openJob', i.job_id.slice(0, 8), { id: i.job_id }) },
    { key: 'outcome', label: 'Outcome', align: 'left', cell: (i) => badge(i.job_status, i.job_status === 'done' ? 'green' : i.job_status === 'failed' ? 'red' : 'amber') + (i.job_error_code ? ' <span class="text-xs">' + esc(i.job_error_code) + '</span>' : '') },
    { key: 'account', label: 'Account', align: 'left', cell: (i) => esc(i.account_label === null ? '-' : i.account_label) },
    { key: 'key', label: 'Key', align: 'left', cell: (i) => '<span class="font-mono text-xs">' + esc(i.key_id.slice(0, 8)) + '</span>' },
    { key: 'ack', label: 'Acknowledged', align: 'left', cell: (i) => i.acknowledged_ms === null ? badge('open', 'amber') : esc(i.acknowledged_by + ', ' + fmtTime(i.acknowledged_ms)) },
];

const BILLING_ITEM_COLUMNS = [
    { key: 'at', label: 'When', align: 'left', cell: (i) => esc(fmtTime(i.at_ms)) },
    { key: 'alert', label: 'Alert', align: 'left', cell: (i) => badge(i.alert, 'red') },
    { key: 'wallet', label: 'Wallet', align: 'left', cell: (i) => esc(i.provider + ' / ' + i.kind) },
    { key: 'amount', label: 'Amount', align: 'right', cell: (i) => esc(i.amount) },
    { key: 'used_day', label: 'Used today', align: 'right', cell: (i) => esc(i.used_day) },
    { key: 'used_total', label: 'Used total', align: 'right', cell: (i) => esc(i.used_total) },
    { key: 'detail', label: 'Detail', align: 'left', cell: ({ detail }) => esc(detail === null ? '-' : detail) },
    { key: 'ack', label: 'Acknowledged', align: 'left', cell: (i) => i.acknowledged_ms === null ? badge('open', 'amber') : esc(i.acknowledged_by + ', ' + fmtTime(i.acknowledged_ms)) },
];

function liveHtml(live) {
    if (!live.length) return '<p class="text-sm text-muted-foreground">No live condition right now</p>';
    return '<ul class="space-y-1 text-sm" id="alertsLiveList">' + live.map((a) =>
        '<li>' + badge(a.severity, SEVERITY_TONE[a.severity] || 'gray') + ' <span class="font-medium">' + esc(a.kind) + '</span>: ' + esc(a.message) + '</li>').join('') + '</ul>';
}

function outcomesHtml(d) {
    const o = d.outcomes;
    const codes = Object.entries(d.failed_codes).map(([code, n]) => esc(code) + ' x' + esc(n)).join(', ');
    return '<p class="text-sm" id="alertOutcomes">Jobs of this wording: delivered ' + esc(o.done) + ', refunded ' + esc(o.failed) + ', still open ' + esc(o.open) +
        (codes ? ' (refused with: ' + codes + ')' : '') + '. Delivered means the wording was an acceptance; refunded, a refusal or nothing.</p>';
}

function detailHtml(d) {
    const rows = d.kind === 'unclear_reply' ? tableHtml(UNCLEAR_COLUMNS, d.items, 'No rows') : tableHtml(BILLING_ITEM_COLUMNS, d.items, 'No rows');
    const read = isEmptyWording(d)
        ? '<p class="text-sm text-destructive" id="alertReadFailure">The provider\'s API gave no reply text, so there is no wording to read: this is an API read failure.</p>' : '';
    return '<div class="p-4 space-y-3" id="alertsDetailBody">' +
        '<div class="flex flex-wrap items-center gap-3"><h3 class="text-lg font-semibold break-all" id="alertDetailTitle">' + esc(groupTitle(d)) + '</h3>' +
        badge(d.severity, SEVERITY_TONE[d.severity] || 'gray') + '<span class="flex-1"></span>' +
        (d.open_count > 0 ? button('alertAck', 'Acknowledge', { kind: d.kind, group: d.group }, BTN) : '') + button('alertClose', 'Close', {}, BTN) + '</div>' +
        '<p class="text-xs text-muted-foreground">' + esc(d.kind + ' / ' + d.provider + ' · first ' + fmtTime(d.first_ms) + ' · last ' + fmtTime(d.last_ms) + ' · ' + d.count + ' rows, ' + d.open_count + ' open · ' + d.keys + ' key(s)') + '</p>' +
        oneCustomer(d) + read + (d.outcomes ? outcomesHtml(d) : '') + rows +
        (d.items.length >= ROW_LIMIT ? '<p class="text-xs text-muted-foreground" id="alertDetailMore">Showing the newest ' + ROW_LIMIT + ' rows; older rows are not listed.</p>' : '') + '</div>';
}

registerTab({
    id: 'alerts',
    label: 'Alerts',
    load: async (panel) => {
        const a = alertsView.asked;
        const options = (list, chosen, none) => (none ? '<option value="">' + none + '</option>' : '') + list.map((v) => '<option value="' + esc(v) + '"' + (v === chosen ? ' selected' : '') + '>' + esc(v) + '</option>').join('');
        panel.innerHTML = '<div class="space-y-6"><div class="flex flex-wrap items-center gap-3"><h3 class="text-lg font-semibold">Alerts</h3>' +
            '<select id="alertsStatus" data-change="alertsFilter" class="h-8 rounded-md border border-input bg-background px-2 text-xs">' + options(['open', 'acknowledged', 'all'], a.status) + '</select>' +
            '<select id="alertsKind" data-change="alertsFilter" class="h-8 rounded-md border border-input bg-background px-2 text-xs">' + options(ALERT_KINDS, a.kind, 'All kinds') + '</select></div>' +
            '<p class="text-xs text-muted-foreground">Acknowledging a group marks the rows you saw; a row that arrives later reopens it. ' +
            'Which replies count as unclear is decided in code: new wording is a code change, there is nothing to edit here.</p>' +
            '<section class="rounded-lg border border-border bg-background p-4 space-y-2"><h4 class="text-sm font-semibold">Live conditions</h4>' +
            '<p class="text-xs text-muted-foreground">Computed now; they clear themselves, so there is nothing to acknowledge.</p><div id="alertsLive"></div></section>' +
            '<section class="rounded-lg border border-border bg-background"><div id="alertsTable"></div><p class="hidden p-3 text-xs text-muted-foreground" id="alertsMore">Showing the newest ' + GROUP_LIMIT + ' groups; older groups are not listed.</p></section>' +
            '<div class="rounded-lg border border-border bg-background" id="alertsDetail"></div></div>';
        alertsView.opened = null;
        await loadAlerts();
    },
});

// Asks, then draws the answer only if it is the newest load and the operator asked for no other view meanwhile; a stale
// answer is dropped, a stale failure as quietly. Every load takes its own generation, whatever starts it (the tab, a
// filter, a detail, the reload after an acknowledge). While it runs the tab is locked like during a write.
async function alertsCurrent(ask, draw) {
    const g = ++alertsView.gen;
    alertsView.loading += 1;
    applyAlertsBusy();
    try {
        let answer;
        try { answer = await ask(); } catch (err) { if (g !== alertsView.gen) return null; throw err; }
        if (g !== alertsView.gen) return null;
        draw(answer);
        return answer;
    } finally {
        alertsView.loading -= 1;
        applyAlertsBusy();
    }
}

// The list, the live conditions and the open detail, read together so they are drawn together.
async function readAlerts(at) {
    const query = '?status=' + encodeURIComponent(at.status) + (at.kind ? '&kind=' + encodeURIComponent(at.kind) : '') + '&limit=' + GROUP_LIMIT;
    const [list, stats] = await Promise.all([api('/alerts/groups' + query), api('/stats')]);
    let detail = null, detailError = null;
    if (at.open) {
        try { detail = await api(alertsPath(at.open, '?limit=' + ROW_LIMIT)); } catch (err) { detailError = err; }
    }
    return { groups: list.items, live: stats.alerts, opened: detail, detailError: detailError };
}

function drawAlerts(view) {
    alertsView.groups = view.groups;
    alertsView.opened = view.opened;
    document.getElementById('alertsLive').innerHTML = liveHtml(view.live);
    document.getElementById('alertsTable').innerHTML = tableHtml(ALERT_COLUMNS, view.groups, 'No alert groups with this filter');
    document.getElementById('alertsMore').classList.toggle('hidden', view.groups.length < GROUP_LIMIT);
    document.getElementById('alertsDetail').innerHTML = view.opened ? detailHtml(view.opened) : '';
    if (view.detailError) {
        // the detail could not be read: leave it closed, so the list is whole and there is nothing to be stuck on
        alertsView.asked = Object.assign({}, alertsView.asked, { open: null });
        toast(errorText(view.detailError), 'error');
    }
    applyAlertsBusy();
}

function loadAlerts() {
    const at = Object.assign({}, alertsView.asked);
    return alertsCurrent(() => readAlerts(at), drawAlerts);
}

// While a write is in flight, or a view is loading, nothing in the view being replaced can be clicked. During a load
// only the controls that ask for another view stay usable: they are how it is replaced.
function applyAlertsBusy() {
    const writing = alertsView.busy, loading = alertsView.loading > 0;
    for (const el of document.querySelectorAll('#panel-alerts button, #panel-alerts select')) {
        const opens = el.matches('[data-act="alertOpen"], [data-act="alertClose"], #alertsStatus, #alertsKind');
        el.disabled = writing || (loading && !opens);
    }
}

async function alertsChanging(fn) {
    alertsView.busy = true;
    applyAlertsBusy();
    try { return await fn(); } finally { alertsView.busy = false; applyAlertsBusy(); }
}

ACTIONS.alertsFilter = async () => {
    alertsView.asked = { status: document.getElementById('alertsStatus').value, kind: document.getElementById('alertsKind').value, open: alertsView.asked.open };
    await loadAlerts();
};
ACTIONS.alertOpen = async (el) => {
    alertsView.asked = Object.assign({}, alertsView.asked, { open: { kind: el.dataset.kind, group: el.dataset.group } });
    await loadAlerts();
};
// Closing also reloads the list, so the table always matches the filters even if one was changed while a load ran.
ACTIONS.alertClose = async () => {
    alertsView.asked = Object.assign({}, alertsView.asked, { open: null });
    alertsView.opened = null;
    document.getElementById('alertsDetail').innerHTML = '';
    await loadAlerts();
};
// The newest row the operator saw is the one the button was drawn from, in the list or in the detail.
ACTIONS.alertAck = async (el) => {
    const g = el.closest('#alertsDetail') ? alertsView.opened : alertsView.groups.find((x) => x.kind === el.dataset.kind && x.group === el.dataset.group);
    const done = await alertsChanging(async () => {
        const result = await api(alertsPath(g, '/acknowledge'), { method: 'POST', body: { through_id: g.newest_id } });
        let failure = null;
        try { await loadAlerts(); } catch (err) { failure = err; }
        return { result: result, failure: failure };
    });
    const said = done.result.acknowledged ? 'Acknowledged ' + done.result.acknowledged + ' row(s)' : 'Nothing new to acknowledge';
    if (done.failure) toast(said + ', but the list could not be reloaded: ' + errorText(done.failure), 'error');
    else toast(said, 'success');
    await refreshStats();                                // the banner counts open groups
};
