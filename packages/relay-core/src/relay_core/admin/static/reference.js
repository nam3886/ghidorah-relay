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
async function copy(text, label) {
  const old = label.textContent;
  try { await navigator.clipboard.writeText(await text()); label.textContent = 'Copied'; } catch { label.textContent = 'Copy failed'; }
  setTimeout(() => { label.textContent = old; }, 1500);
}
document.getElementById('ai-copy').addEventListener('click', () =>
  copy(() => fetch('/llms-full.txt').then((r) => r.text()), document.getElementById('ai-copy-label')));
document.getElementById('ai-link').addEventListener('click', () =>
  copy(async () => origin + '/llms-full.txt', document.getElementById('ai-link-label')));
ai.toggle.addEventListener('click', () => setMenu(ai.list.hidden, false));
document.addEventListener('click', (e) => { if (!document.getElementById('ai').contains(e.target)) setMenu(false); });
document.addEventListener('keydown', (e) => {
  if (ai.list.hidden) return;
  const all = items(), at = all.indexOf(document.activeElement);
  if (e.key === 'Escape') { setMenu(false, true); } else if (e.key === 'ArrowDown') {
    e.preventDefault(); all[(at + 1) % all.length].focus();
  } else if (e.key === 'ArrowUp') { e.preventDefault(); all[(at + all.length - 1) % all.length].focus(); }
});
