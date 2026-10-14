// Helpers shared by every admin page. No inline script or handler exists anywhere (the CSP forbids them): markup
// carries data-act / data-change / data-enter attributes and the delegates at the bottom call ACTIONS[name].
'use strict';

const TOKEN_KEY = 'relay_admin_token';
const USER_KEY = 'relay_admin_user';
const TEST_KEY = 'relay_test_key';
const ACTIONS = {};

// ---- escaping -----------------------------------------------------------------------------------------------
// Every server value that reaches innerHTML goes through esc(); attribute values too (it escapes quotes).
function esc(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Media is shown only from this origin's /m/ share links; anything else (a script URL, data:, blob:, another host)
// is refused and the caller shows text instead.
function safeMediaUrl(url) {
    if (typeof url !== 'string' || !url) return null;
    let parsed;
    try { parsed = new URL(url, location.origin); } catch (_) { return null; }
    if (parsed.origin !== location.origin || !parsed.pathname.startsWith('/m/')) return null;
    return parsed.pathname + parsed.search;
}

// ---- session ------------------------------------------------------------------------------------------------
function token() { try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; } }
function clearSession() {
    try { sessionStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(USER_KEY); sessionStorage.removeItem(TEST_KEY); } catch (_) { /* private mode */ }
}

class ApiError extends Error {
    constructor(status, body) {
        const err = (body && body.error) || {};
        super(err.message || ('HTTP ' + status));
        this.status = status;
        this.code = err.code || '';
        this.param = err.param || '';
    }
}

// JSON call to the admin API. A 401 means the session is gone: back to the login page.
async function api(path, options) {
    const opts = options || {};
    const headers = { 'X-Admin-Session': token() };
    const init = { method: opts.method || 'GET', headers };
    if (opts.body !== undefined) {
        headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(opts.body);
    }
    const response = await fetch('/admin' + path, init);
    if (response.status === 401 && !opts.noRedirect) {
        clearSession();
        location.href = '/login';
        throw new ApiError(401, null);
    }
    if (opts.raw) return response;
    const text = await response.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) { data = {}; }
    if (!response.ok) throw new ApiError(response.status, data);
    return data;
}

// ---- small UI helpers ---------------------------------------------------------------------------------------
function toast(message, kind) {
    const colors = { success: 'bg-green-600', error: 'bg-destructive', info: 'bg-primary' };
    const el = document.createElement('div');
    el.className = 'fixed bottom-4 right-4 ' + (colors[kind] || colors.error) +
        ' text-white px-4 py-2.5 rounded-lg shadow-lg text-sm font-medium z-50 animate-slide-up';
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => { el.classList.add('toast-fade'); setTimeout(() => el.remove(), 300); }, 2500);
}

function errorText(err) {
    if (err && err.retrySafe) return 'Trying again is safe. Retry the same selection; it will not charge twice.';
    if (err instanceof ApiError) return err.param ? err.message + ' (' + err.param + ')' : err.message;
    return 'Network error, please try again';
}

// Runs fn(button) with the button disabled; an error becomes a toast.
async function runBusy(button, fn) {
    if (button) button.disabled = true;
    try { await fn(button); } catch (err) { toast(errorText(err), 'error'); } finally { if (button) button.disabled = false; }
}

function fmtTime(epochMs) {
    if (!epochMs) return '-';
    return new Date(epochMs).toLocaleString('en-GB', { hour12: false });
}

// Copies text to the clipboard. `navigator.clipboard` only exists on a secure origin (https or localhost) and the
// console is often opened over plain http on a LAN, so the fallback selects a hidden field and uses execCommand.
async function copyToClipboard(text) {
    try {
        if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch (_) { /* fall through to the field */ }
    const field = document.createElement('textarea');
    field.value = text;
    field.className = 'sr-only';
    document.body.appendChild(field);
    field.select();
    try { return document.execCommand('copy'); } catch (_) { return false; } finally { field.remove(); }
}

// data-act="copyText": copies data-copy, or the text of the element named by data-from; the button says "Copied" for a moment.
ACTIONS.copyText = async (btn) => {
    const source = btn.dataset.from ? document.querySelector(btn.dataset.from) : null;
    const text = source ? source.textContent : btn.dataset.copy;
    if (!text || !(await copyToClipboard(text))) {
        // Last resort: select the visible text so the operator can press Ctrl+C / Cmd+C.
        if (source) { const range = document.createRange(); range.selectNodeContents(source); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range); }
        toast('Could not copy automatically: the text is selected, press Ctrl+C (Cmd+C on a Mac)', 'error');
        return;
    }
    const label = btn.dataset.label || btn.textContent;
    btn.dataset.label = label;
    btn.textContent = 'Copied';
    clearTimeout(btn.copiedTimer);
    btn.copiedTimer = setTimeout(() => { btn.textContent = label; }, 2000);
};

// ---- delegated events ---------------------------------------------------------------------------------------
function dispatch(attr, event) {
    const el = event.target.closest('[' + attr + ']');
    if (!el) return;
    const fn = ACTIONS[el.getAttribute(attr)];
    if (fn) runBusy(el.tagName === 'BUTTON' ? el : null, () => fn(el, event));
}
document.addEventListener('click', (e) => dispatch('data-act', e));
document.addEventListener('change', (e) => dispatch('data-change', e));
document.addEventListener('keydown', (e) => { if (e.key === 'Enter') dispatch('data-enter', e); });
document.addEventListener('search', (e) => dispatch('data-search', e));
