import type { ApiError } from "@/api/client";
import type { ApplyAllResult, FrameInfo, FrameLock, FrameTarget, FrameVersion } from "@/api/frames";
import { fmtTime } from "@/lib/format";

export const NO_FRAMES_TEXT = "This provider has no prompt frames: the core frame applies.";
export const NO_PREVIEW_MODEL_TEXT = "This kind has no model to preview with";
export const SAMPLE_PROMPT = "a cat on a sofa";
export const DEFAULT_TARGET = "default";
// The two markers a frame must hold once each. Kept as values so no template has to hold a double brace.
export const PROMPT_MARKER = "{{client_prompt}}";
export const TRACKING_MARKER = "{{tracking_code}}";

// "Video": a kind as its section heading writes it
export const kindTitle = (kind: string): string => kind.charAt(0).toUpperCase() + kind.slice(1);

// "v7": the version as the tables and the history draw it. No version at all is said so.
export const versionTag = (v: FrameVersion | null | undefined): string => (v ? `v${v.id}` : "no version");

const two = (n: number): string => String(n).padStart(2, "0");

// "14/10": the day a lock was set, day first
export const shortDay = (ms: number | null): string => {
  if (!ms) return "-";
  const d = new Date(ms);
  return `${two(d.getDate())}/${two(d.getMonth() + 1)}`;
};

// "14/10 18:40": the time of a history row
export const shortStamp = (ms: number | null): string => {
  if (!ms) return "-";
  const d = new Date(ms);
  return `${shortDay(ms)} ${two(d.getHours())}:${two(d.getMinutes())}`;
};

// The history newest version first (ids only grow), the input list left as it was.
export const newestFirst = (history: FrameVersion[]): FrameVersion[] => [...history].sort((a, b) => b.id - a.id);

const lockWords = (lock: FrameLock): string => `${lock.locked ? "locked" : "unlocked"} by ${lock.created_by}`;

// "locked 14/10 by admin"; a frame whose lock was never toggled has nothing to say
export const lockLine = (lock: FrameLock | null | undefined): string =>
  lock ? `${lock.locked ? "locked" : "unlocked"} ${shortDay(lock.created_ms)} by ${lock.created_by}` : "";

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
