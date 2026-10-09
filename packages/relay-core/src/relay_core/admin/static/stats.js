'use strict';

// Stat cards and the "Needs attention" banner above the tabs. Both come from GET /admin/stats: the alerts are
// computed live, so they vanish by themselves when their condition does.

const STAT_CARDS = [
    { label: 'In progress', tone: 'text-blue-600', value: (s) => s.jobs.in_progress + s.jobs.queued },
    { label: 'Completed', tone: 'text-green-600', value: (s) => s.jobs.done },
    { label: 'Failed', tone: 'text-destructive', value: (s) => s.jobs.failed },
    { label: 'Failed before a job (24 h)', tone: 'text-destructive', value: (s) => s.jobs.pre_job_failures_24h },
    { label: 'Accounts ready', tone: 'text-foreground', value: (s) => s.accounts.usable + ' / ' + s.accounts.available },
    { label: 'Videos made', tone: 'text-purple-600', value: (s) => s.made.videos },
    { label: 'Images made', tone: 'text-purple-600', value: (s) => s.made.images },
];

async function refreshStats() {
    const s = await api('/stats' + (state.provider ? '?' + providerParam() : ''));
    document.getElementById('statsCards').innerHTML = STAT_CARDS.map((c) =>
        '<div class="rounded-lg border border-border bg-background p-4"><p class="text-sm font-medium text-muted-foreground mb-2">' + esc(c.label) +
        '</p><h3 class="text-xl font-bold ' + c.tone + '">' + esc(c.value(s)) + '</h3></div>').join('');
    const banner = document.getElementById('alertsBanner');
    banner.classList.toggle('hidden', s.alerts.length === 0);
    document.getElementById('alertsList').innerHTML = s.alerts.map((a) =>
        '<li><span class="font-medium">' + esc(a.kind) + '</span>: ' + esc(a.message) + '</li>').join('');
}
