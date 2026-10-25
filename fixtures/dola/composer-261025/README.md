# dola composer action row, 25/10

The composer's action row (`.guidance-input-actions`) as dola.com rendered it on 2026-10-25 around 06:00Z. Recorded on
the LAN box in the worker-dola Chrome, account n11-00 through its own proxy, about 8 s after clicking "Create Videos"
(`video_row.html`) and "Create Images" (`image_row.html`). Nothing was sent.

Stripped in the page before saving: every `svg`, every `[hidden]` element, every attribute except `class`, `role` and
`disabled` (no image URLs, no ids, no `data-*`). Then, on the Mac, every class except `bp5-overflow-*`, `overflow-*`
and `guidance-input-*` (Tailwind layout only). Text and nesting are as dola wrote them.

`packages/provider-dola/tests/test_composer_browser.py` loads both in a real Chrome and reads every chip the composer
looks for. `packages/provider-dola/FINDINGS.md` ("Composer change 25/10") says what changed.
