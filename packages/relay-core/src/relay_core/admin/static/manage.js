'use strict';

// The dashboard shell: the tab switcher (the registry is in tables.js), the provider filter and sign-out. A tab is one script file that calls
// registerTab({id, label, load}) before this file runs its start-up; `load(panel)` fills the panel it is given
// and is called each time the tab is opened.
function panelOf(id) { return document.getElementById('panel-' + id); }

function switchTab(id) {
    state.tab = id;
    for (const tab of TABS) {
        const active = tab.id === id;
        panelOf(tab.id).classList.toggle('hidden', !active);
        const btn = document.getElementById('tab-' + tab.id);
        btn.classList.toggle('border-primary', active);
        btn.classList.toggle('border-transparent', !active);
    }
    const tab = TABS.find((t) => t.id === id);
    runBusy(null, refreshStats);
    if (tab) runBusy(null, () => tab.load(panelOf(id)));
}

function reloadTab() { switchTab(state.tab); }

function buildShell() {
    const nav = document.getElementById('tabNav');
    const panels = document.getElementById('panels');
    for (const tab of TABS) {
        const btn = document.createElement('button');
        btn.id = 'tab-' + tab.id;
        btn.dataset.act = 'switchTab';
        btn.dataset.tab = tab.id;
        btn.className = 'tab-btn border-b-2 border-transparent text-sm font-medium py-3 px-1';
        btn.textContent = tab.label;
        nav.appendChild(btn);
        const panel = document.createElement('div');
        panel.id = 'panel-' + tab.id;
        panel.className = 'hidden';
        panels.appendChild(panel);
    }
    for (const link of [['/test', 'Test page', ''], ['/reference', 'API docs', '_blank']]) {
        const a = document.createElement('a');
        a.href = link[0];
        if (link[2]) { a.target = link[2]; a.rel = 'noopener'; }
        a.className = 'tab-btn inline-flex items-center border-b-2 border-transparent text-sm font-medium py-3 px-1';
        a.textContent = link[1];
        nav.appendChild(a);
    }
}

ACTIONS.switchTab = (el) => switchTab(el.dataset.tab);
ACTIONS.providerFilter = (el) => { state.provider = el.value; reloadTab(); };
ACTIONS.logout = async () => {
    try { await api('/logout', { method: 'POST', noRedirect: true }); } catch (_) { /* the session may be gone already */ }
    clearSession();
    location.href = '/login';
};

async function startManage() {
    if (!token()) { location.href = '/login'; return; }
    const me = await api('/me');
    document.getElementById('whoami').textContent = me.username;
    const select = document.getElementById('providerFilter');
    for (const name of await providerNames()) {
        const option = document.createElement('option');
        option.value = name;
        option.textContent = name;
        select.appendChild(option);
    }
    buildShell();
    if (TABS.length) switchTab(TABS[0].id);
}

// Tab scripts load before this one (see manage.html) and have registered themselves by now.
startManage().catch((err) => toast(errorText(err), 'error'));
