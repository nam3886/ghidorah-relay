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
