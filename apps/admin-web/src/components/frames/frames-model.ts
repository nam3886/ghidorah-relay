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

// "version 7 by owner, 15/10/2026, 10:00:00 (restored from 3)"
export function versionLine(v: FrameVersion | null | undefined): string {
  if (!v) return "no version";
  const restored = v.restored_from ? ` (restored from ${v.restored_from})` : "";
  return `version ${v.id} by ${v.created_by}, ${fmtTime(v.created_ms)}${restored}`;
}

// The history as the timeline draws it: newest version first (ids only grow), the input list left as it was.
export const newestFirst = (history: FrameVersion[]): FrameVersion[] => [...history].sort((a, b) => b.id - a.id);

// The colour of a version on the timeline: a version that follows the default is grey, a restored one amber, the
// others (an own text written by hand) blue.
export const versionType = (v: FrameVersion): "info" | "warning" | "primary" =>
  v.text === null ? "info" : v.restored_from ? "warning" : "primary";

const lockWords = (lock: FrameLock): string => `${lock.locked ? "locked" : "unlocked"} by ${lock.created_by}`;

// "locked by owner, 15/10/2026, 10:00:00" or "never toggled"
export const lockLine = (lock: FrameLock | null | undefined): string =>
  lock ? `${lockWords(lock)}, ${fmtTime(lock.created_ms)}` : "never toggled";

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
