// A PostCSS plugin used by the build (vite.config.ts), never shipped in the bundle. Element Plus draws a few glyphs
// (the tick of a selected option in a multiple select) with a mask made from a data:image/svg+xml URL, which the CSP
// (no img-src, so 'self') blocks. The theme draws those glyphs itself, so the build drops every declaration that
// carries a data URL, and the build guard (tests/admin_ui/webguards.py) fails when one is left in the dist CSS.
// A data URL is `data:[<type>][;base64],<payload>` where a value starts, after `(`, a comma or a space, quoted or
// not: so `url(data:...)`, `url("data:...")` and a quoted string inside `image-set(...)` all count, while a string
// that merely says "data: none" does not (no comma before the next space or quote).
export const DATA_URL_IN_VALUE = /(?:^|[\s(,])["']?data:[^\s"')]*,/i;

// A comment may sit inside the value (`url(/*x*/data:...)`): it is removed before the test.
const COMMENT = /\/\*[\s\S]*?\*\//g;

export const stripDataUrls = {
  postcssPlugin: "strip-data-urls",
  Declaration(decl: { value: string; remove: () => void }): void {
    if (DATA_URL_IN_VALUE.test(decl.value.replace(COMMENT, ""))) decl.remove();
  },
};
