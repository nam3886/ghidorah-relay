'use strict';

// Settings tab: every live setting with its source, edit and reset; boot-only values read-only; password change.

const SETTING_SOURCE_TONE = { db: 'blue', env: 'amber', default: 'gray' };
const settingsView = { items: [] };

function settingValueText(item) {
    return typeof item.value === 'string' ? item.value : JSON.stringify(item.value);
}

const SETTING_COLUMNS = [
    { key: 'key', label: 'Setting', align: 'left', cell: (s) => '<div class="font-mono text-xs">' + esc(s.key) + '</div><div class="text-xs text-muted-foreground max-w-[420px]">' + esc(s.description) + '</div>' },
    { key: 'value', label: 'Value', align: 'left', cell: (s) => '<div class="flex items-center gap-2"><input data-setting-input="' + esc(s.key) + '" class="h-8 w-44 rounded-md border border-input px-2 text-xs font-mono" value="' + esc(settingValueText(s)) + '"></div>' },
    { key: 'source', label: 'Source', align: 'center', cell: (s) => badge(s.source, SETTING_SOURCE_TONE[s.source] || 'gray') },
    { key: 'default', label: 'Default', align: 'left', cell: (s) => '<span class="font-mono text-xs">' + esc(JSON.stringify(s.default)) + '</span>' },
    { key: 'actions', label: 'Actions', align: 'right', cell: (s) => '<div class="flex justify-end gap-1.5">' + button('saveSetting', 'Save', { key: s.key }) + button('resetSetting', 'Reset', { key: s.key }) + '</div>' },
];

const ENV_COLUMNS = [
    { key: 'name', label: 'Boot-only value', align: 'left', cell: (e) => '<span class="font-mono text-xs">' + esc(e.name) + '</span>' },
    { key: 'value', label: 'Value (read-only)', align: 'left', cell: (e) => '<span class="font-mono text-xs">' + esc(e.value === null || e.value === undefined ? '-' : e.value) + '</span>' },
];

const PRICE_COLUMNS = [
    { key: 'provider', label: 'Provider', align: 'left', cell: (p) => esc(p.provider) },
    { key: 'model', label: 'Model', align: 'left', cell: (p) => esc(p.model) },
    { key: 'duration', label: 'Duration', align: 'center', cell: (p) => esc(p.duration === null ? '-' : p.duration + 's') },
    { key: 'charged', label: 'Charged', align: 'center', cell: (p) => esc(p.charged) },
    { key: 'jobs', label: 'Jobs with a figure', align: 'center', cell: (p) => esc(p.jobs) },
    { key: 'disagreed', label: 'Upstream disagreed', align: 'center', cell: (p) => p.disagreed ? badge(p.disagreed, 'red') : '0' },
    { key: 'lowest', label: 'Lowest upstream', align: 'center', cell: (p) => esc(p.lowest_upstream === null ? '-' : p.lowest_upstream) },
    { key: 'substituted', label: 'Substituted (not counted)', align: 'center', cell: (p) => esc(p.substituted) },
];

registerTab({
    id: 'settings',
    label: 'Settings',
    load: async (panel) => {
        panel.innerHTML = '<div class="space-y-6"><div class="rounded-lg border border-border bg-background"><div class="p-4 border-b border-border"><h3 class="text-lg font-semibold">Settings</h3></div><div id="settingsTable"></div></div>' +
            '<div class="rounded-lg border border-border bg-background" id="envTable"></div>' +
            '<div class="rounded-lg border border-border bg-background"><div class="p-4 border-b border-border"><h3 class="text-lg font-semibold">Measured prices</h3>' +
            '<p class="text-xs text-muted-foreground">Points charged against what the upstream said it took. A job the upstream did something else for (another model or length) is counted apart.</p></div><div id="pricesTable"></div></div>' +
            '<div class="rounded-lg border border-border bg-background p-4 space-y-3 max-w-md"><h3 class="text-lg font-semibold">Change password</h3>' +
            '<input id="oldPassword" type="password" autocomplete="current-password" placeholder="Current password" class="' + FIELD + '">' +
            '<input id="newPassword" type="password" autocomplete="new-password" placeholder="New password" class="' + FIELD + '">' +
            '<button data-act="changePassword" class="' + BTN_PRIMARY + '">Change password</button>' +
            '<p class="text-xs text-muted-foreground">Every session ends, this one included: you sign in again.</p></div></div>';
        await loadSettings();
    },
});

async function loadSettings() {
    const data = await api('/settings');
    settingsView.items = data.items;
    document.getElementById('settingsTable').innerHTML = tableHtml(SETTING_COLUMNS, data.items, 'No settings');
    document.getElementById('envTable').innerHTML = tableHtml(ENV_COLUMNS, data.env_only, 'None');
    const prices = await api('/prices' + (state.provider ? '?' + providerParam() : ''));
    document.getElementById('pricesTable').innerHTML = tableHtml(PRICE_COLUMNS, prices.items, 'No job has an upstream figure yet');
}

// The input holds text; a number, boolean or list is parsed as JSON first, anything else is sent as a string.
function parseSettingValue(text) {
    try { return JSON.parse(text); } catch (_) { return text; }
}

ACTIONS.saveSetting = async (el) => {
    const input = document.querySelector('[data-setting-input="' + CSS.escape(el.dataset.key) + '"]');
    await api('/settings/' + encodeURIComponent(el.dataset.key), { method: 'PUT', body: { value: parseSettingValue(input.value) } });
    toast('Saved', 'success');
    await loadSettings();
};
ACTIONS.resetSetting = async (el) => {
    await api('/settings/' + encodeURIComponent(el.dataset.key), { method: 'DELETE' });
    toast('Reset', 'success');
    await loadSettings();
};
ACTIONS.changePassword = async () => {
    await api('/password', { method: 'POST', body: { old_password: document.getElementById('oldPassword').value, new_password: document.getElementById('newPassword').value } });
    clearSession();
    location.href = '/login';
};
