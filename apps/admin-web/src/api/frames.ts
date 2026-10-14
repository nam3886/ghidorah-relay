// Prompt frames tab. Every path segment is encoded; `target` is a model id or the literal "default".
import { request } from "@/api/client";

export interface FrameVersion {
  id: number;
  text: string | null;
  created_ms: number | null;
  created_by: string;
  created_by_id: string | null;
  restored_from: number | null;
}

export interface FrameLock {
  locked: boolean;
  created_ms: number | null;
  created_by: string;
  created_by_id: string | null;
}

export interface FrameModel {
  model: string;
  source: "own" | "default" | string;
  own: FrameVersion | null;
  effective: FrameVersion | null;
  locked: boolean;
  lock: FrameLock | null;
}

export interface FrameKind {
  kind: string;
  default: FrameVersion | null;
  shipped: string | null;
  shipped_differs: boolean;
  models: FrameModel[];
}

export interface FramesList {
  provider: string;
  kinds: FrameKind[];
}

export interface FrameInfo {
  current: FrameVersion | null;
  effective: FrameVersion | null;
  history: FrameVersion[];
  lock?: FrameLock | null;
  lock_history?: FrameLock[];
}

export interface FrameTarget {
  provider: string;
  kind: string;
  target: string;
}

export interface SavedFrame {
  version: FrameVersion | null;
  unchanged: boolean;
  warnings: string[];
}

export interface ApplyAllResult {
  applied: string[];
  kept: string[];
  unchanged: string[];
  warnings: string[];
}

export interface PreviewResult {
  text: string;
  length: number;
  frame_id: number | null;
  source: string | null;
  warnings: string[];
}

const e = encodeURIComponent;
const kindPath = (provider: string, kind: string): string => `/frames/${e(provider)}/${e(kind)}`;
const targetPath = (t: FrameTarget): string => `${kindPath(t.provider, t.kind)}/${e(t.target)}`;

export const listFrames = (provider: string): Promise<FramesList> => request("/frames", { query: { provider } });

export const getFrame = (t: FrameTarget): Promise<FrameInfo> => request(targetPath(t), { query: { limit: 50 } });

export const putFrame = (t: FrameTarget, text: string | null): Promise<SavedFrame> =>
  request(targetPath(t), { method: "PUT", body: { text } });

export const rollbackFrame = (t: FrameTarget, versionId: number): Promise<SavedFrame> =>
  request(`${targetPath(t)}/rollback`, { method: "POST", body: { version_id: versionId } });

export const resetFrame = (provider: string, kind: string): Promise<SavedFrame> =>
  request(`${kindPath(provider, kind)}/reset`, { method: "POST" });

export const lockFrame = (t: FrameTarget, locked: boolean): Promise<unknown> =>
  request(`${targetPath(t)}/lock`, { method: "PUT", body: { locked } });

export const applyAllFrames = (provider: string, kind: string, text: string): Promise<ApplyAllResult> =>
  request(`${kindPath(provider, kind)}/apply-all`, { method: "POST", body: { text } });

export const previewFrame = (model: string, prompt: string, text: string): Promise<PreviewResult> =>
  request("/frames/preview", { method: "POST", body: { model, prompt, text } });
