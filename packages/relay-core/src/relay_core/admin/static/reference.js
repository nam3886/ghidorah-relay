'use strict';

// Loads the vendored renderer with its pinned hash. No inline script is allowed on this page, so the loader is a file.
const self_ = document.currentScript;
const renderer = document.createElement('script');
renderer.src = self_.dataset.renderer;
renderer.integrity = self_.dataset.integrity;
renderer.onerror = () => { document.getElementById('fallback').hidden = false; };
document.body.appendChild(renderer);
