import type { ApiError } from "@/api/client";
import type { ApplyAllResult, FrameInfo, FrameKind, FrameLock, FrameTarget, FrameVersion } from "@/api/frames";
import { fmtTime } from "@/lib/format";

export const NO_FRAMES_TEXT = "This provider has no prompt frames: the core frame applies.";
export const NO_PREVIEW_MODEL_TEXT = "This kind has no model to preview with";
export const SAMPLE_PROMPT = "a cat on a sofa";
export const DEFAULT_TARGET = "default";
// The longest frame the server takes (`FRAME_MAX` in relay_core.frames).
export const FRAME_MAX = 1000;
// The two markers a frame must hold once each. Kept as values so no template has to hold a double brace.
export const PROMPT_MARKER = "{{client_prompt}}";
export const TRACKING_MARKER = "{{tracking_code}}";

// A frame cut into its plain runs and its markers (`{{name}}`), so a page can pick the markers out as text.
export function markerParts(text: string): Array<{ text: string; marker: boolean }> {
  return text
    .split(/(\{\{\w+\}\})/)
    .filter(Boolean)
    .map((part) => ({ text: part, marker: /^\{\{\w+\}\}$/.test(part) }));
}

// "Video": a kind as its section heading writes it
export const kindTitle = (kind: string): string => kind.charAt(0).toUpperCase() + kind.slice(1);

// "v7": the version as the tables and the history draw it. No version at all is said so.
export const versionTag = (v: FrameVersion | null | undefined): string => (v ? `v${v.id}` : "no version");

const two = (n: number): string => String(n).padStart(2, "0");

// The history newest version first (ids only grow), the input list left as it was.
export const newestFirst = (history: FrameVersion[]): FrameVersion[] => [...history].sort((a, b) => b.id - a.id);

const lockWords = (lock: FrameLock): string => `${lock.locked ? "locked" : "unlocked"} by ${lock.created_by}`;

// "locked 22 Oct": the lock as a table cell says it; a frame whose lock was never toggled has nothing to say
export const lockDay = (lock: FrameLock | null | undefined): string =>
  lock ? `${lock.locked ? "locked" : "unlocked"} ${dayMonth(lock.created_ms)}` : "";

// "Locked 22 Oct by admin": the same with who, for a phone row and the checkbox's tooltip
export const lockSentence = (lock: FrameLock | null | undefined): string =>
  lock ? `${lock.locked ? "Locked" : "Unlocked"} ${dayMonth(lock.created_ms)} by ${lock.created_by}` : "";

export const lockHistoryLine = (history: FrameLock[]): string =>
  `Lock history: ${history.map((l) => `${lockWords(l)} ${fmtTime(l.created_ms)}`).join("; ")}`;

// The text the editor starts with: the current version's own text, else the text that is in force for it.
export function loadedText(info: FrameInfo): string {
  if (info.current && info.current.text !== null) return info.current.text;
  return info.effective?.text ?? "";
}

export const targetKey = (t: FrameTarget): string => `${t.provider}/${t.kind}/${t.target}`;

// A 4xx refusal as shown beside the editor: the server text, with the field it names.
export const refusalText = (err: ApiError): string => (err.param ? `${err.message} (${err.param})` : err.message);

const list = (names: string[]): string => names.join(", ") || "-";

export function appliedLine(r: ApplyAllResult): string {
  return `Applied: ${list(r.applied)}. Kept (locked): ${list(r.kept)}. Unchanged: ${list(r.unchanged)}.`;
}

// What the preview shows for the tracking code: the marker of the all-zero job id (`marker_for` in relay_core.jobs.create),
// which is what the preview route renders. A provider that sends the text verbatim renders it as nothing.
export const SAMPLE_TRACKING = "[4060]";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "22 Oct": the day a lock was set, as the design writes it
export const dayMonth = (ms: number | null): string => {
  if (!ms) return "-";
  const d = new Date(ms);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
};

// "22 Oct 16:04": the time of a history row
export const longStamp = (ms: number | null): string => {
  if (!ms) return "-";
  const d = new Date(ms);
  return `${dayMonth(ms)} ${two(d.getHours())}:${two(d.getMinutes())}`;
};

// The target a kind opens on: the first model that keeps a frame of its own, else the default.
export const defaultTargetOf = (kind: FrameKind): string =>
  kind.models.find((m) => m.source === "own")?.model ?? DEFAULT_TARGET;

export const ownCount = (kind: FrameKind): number => kind.models.filter((m) => m.source === "own").length;
export const followerCount = (kind: FrameKind): number => kind.models.length - ownCount(kind);

// Video first, as drawn; any other kind keeps the server order after it.
export const kindsInOrder = (kinds: FrameKind[]): FrameKind[] =>
  [...kinds].sort((a, b) => Number(b.kind === "video") - Number(a.kind === "video"));

const occurrences = (text: string, needle: string): number => text.split(needle).length - 1;

// The grammar the editor checks as it is typed (the server's rule: each marker exactly once). Advisory: Save is still
// the server's call.
export function markerChecks(text: string): { prompt: number; tracking: number; ok: boolean } {
  const prompt = occurrences(text, PROMPT_MARKER);
  const tracking = occurrences(text, TRACKING_MARKER);
  return { prompt, tracking, ok: prompt === 1 && tracking === 1 };
}

// A text cut at the first place each needle occurs, so a preview can pick out the sample prompt and the tracking code.
export function pickOut(text: string, needles: string[]): Array<{ text: string; hit: boolean }> {
  const found = needles
    .filter((n) => n)
    .map((n) => ({ n, at: text.indexOf(n) }))
    .filter((f) => f.at >= 0)
    .sort((a, b) => a.at - b.at);
  const parts: Array<{ text: string; hit: boolean }> = [];
  let from = 0;
  for (const f of found) {
    if (f.at < from) continue;
    if (f.at > from) parts.push({ text: text.slice(from, f.at), hit: false });
    parts.push({ text: f.n, hit: true });
    from = f.at + f.n.length;
  }
  if (from < text.length) parts.push({ text: text.slice(from), hit: false });
  return parts;
}
