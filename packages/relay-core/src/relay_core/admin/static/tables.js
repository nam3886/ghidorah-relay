'use strict';

// Shared table, pager, modal and command helpers for the dashboard tabs. A table is described once as a list of
// columns {key, label, align, cell(row)}: the header and every cell come from the same list, so a column can never
// be off by one. Class names are written out in full because the stylesheet is built by scanning this file.

// ---- tab registry (tab scripts load before manage.js and register themselves) ----------------------------
const TABS = [];
const state = { provider: '', tab: '' };

function registerTab(tab) { TABS.push(tab); }

// The catalog (every model of every provider) is read once; it feeds the provider filter, the key form and import.
let catalogCache = null;
async function catalog() {
    if (!catalogCache) catalogCache = (await api('/models')).data;
    return catalogCache;
}
async function providerNames() {
    return [...new Set((await catalog()).map((m) => m.owned_by))].sort();
}

const ALIGN = { left: 'text-left', center: 'text-center', right: 'text-right' };
const BADGE = {
    green: 'bg-green-100 text-green-800', red: 'bg-red-100 text-red-800', amber: 'bg-amber-100 text-amber-800',
    blue: 'bg-blue-100 text-blue-800', gray: 'bg-gray-100 text-gray-700', purple: 'bg-purple-100 text-purple-800',
};
const BTN = 'inline-flex items-center justify-center rounded-md border border-input bg-background hover:bg-accent h-8 px-3 text-xs disabled:opacity-50';
const BTN_PRIMARY = 'inline-flex items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary/90 h-8 px-3 text-sm font-medium disabled:opacity-50';
const BTN_SMALL = 'inline-flex items-center justify-center rounded-md border border-input hover:bg-accent h-7 px-2 text-xs disabled:opacity-50';
const FIELD = 'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm';

function badge(text, tone) {
    return '<span class="inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ' + (BADGE[tone] || BADGE.gray) + '">' + esc(text) + '</span>';
}

// `rowAttrs(row)` may add attributes to a row (a whole-row action); a click on a button inside it keeps its own.
function tableHtml(columns, rows, emptyText, rowAttrs) {
    const align = (c) => ALIGN[c.align || 'left'];
    const head = columns.map((c) => '<th data-col="' + esc(c.key) + '" class="h-10 px-2.5 ' + align(c) + ' align-middle font-medium text-muted-foreground">' + esc(c.label) + '</th>').join('');
    const body = rows.length
        ? rows.map((row) => '<tr class="hover:bg-accent/50' + (rowAttrs ? ' cursor-pointer"' + rowAttrs(row) : '"') + '>' + columns.map((c) => '<td data-col="' + esc(c.key) + '" class="px-2.5 py-2 ' + align(c) + ' align-middle">' + c.cell(row) + '</td>').join('') + '</tr>').join('')
        : '<tr><td colspan="' + columns.length + '" class="px-2.5 py-6 text-center text-muted-foreground">' + esc(emptyText || 'Nothing here yet') + '</td></tr>';
    return '<div class="relative w-full overflow-x-auto"><table class="w-full table-auto text-sm"><thead><tr class="border-b border-border">' + head + '</tr></thead><tbody class="divide-y divide-border">' + body + '</tbody></table></div>';
}

function switchHtml(checked, act, id, disabled) {
    return '<label class="inline-flex items-center cursor-pointer"><input type="checkbox" class="sr-only peer" data-change="' + act + '" data-id="' + esc(id) + '"' + (checked ? ' checked' : '') + (disabled ? ' disabled' : '') + '><div class="relative w-9 h-5 bg-gray-200 rounded-full peer peer-checked:bg-primary peer-disabled:opacity-50 after:content-[\'\'] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border after:border-gray-300 after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-full"></div></label>';
}

function button(act, label, attrs, cls) {
    const data = Object.entries(attrs || {}).map(([k, v]) => ' data-' + k + '="' + esc(v) + '"').join('');
    return '<button data-act="' + act + '"' + data + ' class="' + (cls || BTN_SMALL) + '">' + esc(label) + '</button>';
}

// ---- pager --------------------------------------------------------------------------------------------------
// A tab builds `host.pager = {move(dir)}` on the element that carries data-pager; the pager buttons call it.
function pagerHtml(p) {
    const from = p.total === 0 ? 0 : p.offset + 1;
    const to = Math.min(p.offset + p.limit, p.total);
    return '<div class="flex items-center justify-end gap-2 p-3 border-t border-border"><span class="text-xs text-muted-foreground">' + from + '-' + to + ' of ' + p.total + '</span>' +
        '<button data-act="pagerMove" data-dir="-1" class="' + BTN_SMALL + '"' + (p.offset <= 0 ? ' disabled' : '') + '>Prev</button>' +
        '<button data-act="pagerMove" data-dir="1" class="' + BTN_SMALL + '"' + (to >= p.total ? ' disabled' : '') + '>Next</button></div>';
}

function cursorPagerHtml(p) {
    return '<div class="flex items-center justify-end gap-2 p-3 border-t border-border"><span class="text-xs text-muted-foreground">Page ' + (p.page + 1) + '</span>' +
        '<button data-act="pagerMove" data-dir="-1" class="' + BTN_SMALL + '"' + (p.page <= 0 ? ' disabled' : '') + '>Prev</button>' +
        '<button data-act="pagerMove" data-dir="1" class="' + BTN_SMALL + '"' + (p.next ? '' : ' disabled') + '>Next</button></div>';
}

ACTIONS.pagerMove = async (el) => {
    await el.closest('[data-pager]').pager.move(Number(el.dataset.dir));
};

// ---- modal --------------------------------------------------------------------------------------------------
function openModal(title, bodyHtml, wide) {
    document.getElementById('modalRoot').innerHTML =
        '<div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4">' +
        '<div class="bg-background rounded-lg shadow-xl w-full ' + (wide ? 'max-w-5xl' : 'max-w-lg') + ' max-h-[90vh] overflow-y-auto">' +
        '<div class="flex items-center justify-between p-4 border-b border-border"><h3 class="text-lg font-semibold">' + esc(title) + '</h3>' +
        '<button data-act="closeModal" class="' + BTN_SMALL + '">Close</button></div><div class="p-4" id="modalBody">' + bodyHtml + '</div></div></div>';
}
function closeModal() { document.getElementById('modalRoot').innerHTML = ''; }
ACTIONS.closeModal = closeModal;

// ---- commands: the worker runs them, the page polls their status ---------------------------------------------
async function pollCommand(commandId, label) {
    for (let i = 0; i < 90; i++) {
        const c = await api('/commands/' + commandId);
        if (c.status === 'done') { toast(label + ': done', 'success'); return c; }
        if (c.status === 'failed') { toast(label + ': failed' + (c.result && c.result.error ? ' - ' + c.result.error : ''), 'error'); return c; }
        await new Promise((r) => setTimeout(r, 1000));
    }
    toast(label + ': still running', 'info');
    return null;
}

function providerParam() {
    return state.provider ? 'provider=' + encodeURIComponent(state.provider) : '';
}
