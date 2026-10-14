// Copy to the clipboard on every origin the admin is opened from. The owner uses plain http on the LAN, which is not a
// secure context, so `navigator.clipboard` is missing there and the hidden-textarea path is the one that runs.
// The scratch element is positioned by a CSS class, never a style attribute (the CSP blocks those).
import "./clipboard.css";

export type CopyOutcome = "clipboard" | "exec" | "select" | "failed";

function execCopy(text: string): boolean {
  const previous = document.activeElement as HTMLElement | null;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  area.className = "clipboard-scratch";
  document.body.appendChild(area);
  try {
    area.select();
    area.setSelectionRange(0, text.length);
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
    previous?.focus?.();
  }
}

// Selects the text of `el` so the user can press Ctrl+C. Returns false when there is nothing to select.
function selectText(el: HTMLElement | null | undefined): boolean {
  if (!el) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
    el.focus();
    el.select();
    return true;
  }
  const selection = window.getSelection();
  if (!selection) return false;
  const range = document.createRange();
  range.selectNodeContents(el);
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}

// `selectFrom` is the element the text sits in, for the last resort. The caller toasts on 'select' and 'failed'.
export async function copyText(text: string, selectFrom?: () => HTMLElement | null | undefined): Promise<CopyOutcome> {
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return "clipboard";
    } catch {
      // permission refused or the document lost focus: the textarea path may still work
    }
  }
  if (execCopy(text)) return "exec";
  return selectText(selectFrom?.()) ? "select" : "failed";
}
