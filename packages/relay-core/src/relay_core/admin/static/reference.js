'use strict';

// Same rule as the dashboard (apps/admin-web/src/stores/ui.ts): a stored choice wins, else the OS setting.
function storedTheme() {
  let theme = null;
  try { theme = localStorage.getItem('relay_admin_theme'); } catch { theme = null; }
  if (theme === 'dark' || theme === 'light') return theme;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

const dark = storedTheme() === 'dark';
document.documentElement.classList.toggle('dark', dark);
const config = document.getElementById('api-reference');
// The renderer would otherwise follow the OS setting (darkMode only sets its default), and its own toggle would keep a second
// choice in its own storage: the dashboard's stored theme is the only one.
config.dataset.configuration = JSON.stringify({
  ...JSON.parse(config.dataset.configuration),
  forceDarkModeState: dark ? 'dark' : 'light',
  hideDarkModeToggle: true,
});

// Loads the vendored renderer with its pinned hash. No inline script is allowed on this page, so the loader is a file.
const self_ = document.currentScript;
const renderer = document.createElement('script');
renderer.src = self_.dataset.renderer;
renderer.integrity = self_.dataset.integrity;
renderer.onerror = () => { document.getElementById('fallback').hidden = false; };
document.body.appendChild(renderer);

// "Copy page" split button: the whole API as Markdown for an AI tool. The page is public; nothing here reads a secret.
const ai = {
  toggle: document.getElementById('ai-toggle'), list: document.getElementById('ai-list'),
};
const origin = location.origin;
const prompt = encodeURIComponent('Read ' + origin + '/llms.txt so I can ask questions about the Ghidorah API.');
document.getElementById('ai-chatgpt').href = 'https://chatgpt.com/?q=' + prompt;
document.getElementById('ai-claude').href = 'https://claude.ai/new?q=' + prompt;

const items = () => [...ai.list.querySelectorAll('[role=menuitem]')];
function setMenu(open, focus) {
  ai.list.hidden = !open;
  ai.toggle.setAttribute('aria-expanded', String(open));
  if (open) items()[0].focus(); else if (focus) ai.toggle.focus();
}
// The label each button restores to is fixed here: a second click while "Copied" shows must not save that word.
const LABELS = { 'ai-copy-label': 'Copy page', 'ai-link-label': 'Copy Markdown link' };
const timers = {};
const fetchMarkdown = () => fetch('/llms-full.txt').then((r) => {
  if (!r.ok) throw new Error('llms-full.txt ' + r.status);
  return r.text();
});
async function copy(write, label) {
  try { await write(); label.textContent = 'Copied'; } catch { label.textContent = 'Copy failed'; }
  clearTimeout(timers[label.id]);
  timers[label.id] = setTimeout(() => { label.textContent = LABELS[label.id]; }, 1500);
}
// Safari refuses writeText after an await (outside the click): hand the clipboard a promise of the Blob instead.
const copyMarkdown = () => (window.ClipboardItem && navigator.clipboard.write
  ? navigator.clipboard.write([new ClipboardItem({
    'text/plain': fetchMarkdown().then((t) => new Blob([t], { type: 'text/plain' })),
  })])
  : fetchMarkdown().then((t) => navigator.clipboard.writeText(t)));
document.getElementById('ai-copy').addEventListener('click', () =>
  copy(copyMarkdown, document.getElementById('ai-copy-label')));
document.getElementById('ai-link').addEventListener('click', () =>
  copy(() => navigator.clipboard.writeText(origin + '/llms-full.txt'), document.getElementById('ai-link-label')));
ai.list.addEventListener('click', (e) => { if (e.target.closest('[role=menuitem]')) setMenu(false); });
ai.toggle.addEventListener('click', () => setMenu(ai.list.hidden, false));
document.addEventListener('click', (e) => { if (!document.getElementById('ai').contains(e.target)) setMenu(false); });
document.addEventListener('keydown', (e) => {
  if (ai.list.hidden) return;
  const all = items(), at = all.indexOf(document.activeElement);
  if (e.key === 'Escape') { setMenu(false, true); } else if (e.key === 'ArrowDown') {
    e.preventDefault(); all[(at + 1) % all.length].focus();
  } else if (e.key === 'ArrowUp') { e.preventDefault(); all[(at + all.length - 1) % all.length].focus();
  } else if (e.key === 'Home') { e.preventDefault(); all[0].focus();
  } else if (e.key === 'End') { e.preventDefault(); all[all.length - 1].focus();
  } else if (e.key === 'Tab') { setMenu(false); }
});
