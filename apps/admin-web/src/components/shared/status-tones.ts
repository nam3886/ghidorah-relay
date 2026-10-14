// The one tone registry of every status chip. A chip shows a dot and a word (never colour alone); the tone only says
// how urgent the word is. The data is status-tones.json: its keys are the exact strings the backend sends (job
// status, account state, command status, alert severity and kind, setting and frame source) plus the few flags the
// tables derive. tests/admin_ui/test_status_tones.py checks it against the backend, so a new backend status fails
// there. A status missing here falls back to neutral.
import entries from "./status-tones.json";

export type Tone = "ok" | "info" | "warn" | "bad" | "neutral";

interface Entry {
  tone: Tone;
  label: string;
}

export const STATUS_TONES: Readonly<Record<string, Entry>> = entries as Record<string, Entry>;

// Request logs show the numeric HTTP code: below 400 fine, a client error is a warning, anything else is bad.
export function httpTone(code: number): Tone {
  return code < 400 ? "ok" : code < 500 ? "warn" : "bad";
}

function entryOf(status: string): Entry | undefined {
  return STATUS_TONES[status.trim().toLowerCase()];
}

export function toneOf(status: string | number): Tone {
  if (typeof status === "number") return httpTone(status);
  return entryOf(status)?.tone ?? "neutral";
}

export function labelOf(status: string | number): string {
  return typeof status === "number" ? String(status) : (entryOf(status)?.label ?? status);
}
