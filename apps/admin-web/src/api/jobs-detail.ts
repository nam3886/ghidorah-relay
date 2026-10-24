// One job: the detail, its actions and its files (/admin/jobs/{id}).
import { request, requestRaw } from "@/api/client";
import type { JobSummary } from "@/api/jobs";
import { jobAction, regenerateCall } from "@/lib/operation-key";

export interface JobMediaItem {
  role: "output" | "input";
  idx: number;
  mime: string;
  size: number;
  width: number | null;
  height: number | null;
  duration_s: number | null;
  // A same-origin /m/ share link, or null once it was revoked or expired.
  url: string | null;
}

export interface JobDebugFile {
  idx: number;
  mime: string;
  size: number;
  url: string;
}

export interface JobEvent {
  id: number;
  at_ms: number | null;
  source: string;
  stage: string;
  text: string;
}

export interface RawExchange {
  route: string | null;
  status_code: number | null;
  body: unknown;
}

export interface JobDetail extends JobSummary {
  // the key's display prefix: what names a key without a label
  key_prefix: string | null;
  prompt: string;
  marker: string;
  params: unknown;
  completion: string | null;
  wallet_kind: string | null;
  pool: string | null;
  account_cost: number | null;
  attempts: number;
  claims: number;
  sent_ms: number | null;
  render_since_ms: number | null;
  updated_ms: number | null;
  upstream_price: number | null;
  upstream_model: string | null;
  error: string | null;
  warnings: unknown[] | null;
  callback: { status: string | null; attempts: number | null; last: unknown };
  no_recheck: boolean;
  frame_id: string | number | null;
  sent_text: string | null;
  // net points charged less the upstream cost (a unit counts as a point); null while that cost is unknown
  upstream_cost?: number | null;
  // the unit of that cost: its pool's (credits, jobs or uses); null when the job has no pool
  cost_unit?: "credits" | "jobs" | "uses" | null;
  margin?: number | null;
  // the account's proxy as host:port, never its credentials
  proxy?: string | null;
  conversation?: string | null;
  provider_task_id?: string | null;
  // redacted on the server: the creating call as the request log kept it (route null: the stored request), and what
  // the status read answers the job's key now
  raw_request?: RawExchange | null;
  raw_response?: RawExchange | null;
  // the provider's own state of the job, redacted
  diagnostics?: Record<string, unknown> | null;
  events: JobEvent[];
  media: JobMediaItem[];
  debug_files: JobDebugFile[];
}

const path = (id: string): string => `/jobs/${encodeURIComponent(id)}`;

export const getJob = (id: string): Promise<JobDetail> => request(path(id));

// `title: ""` clears the title (the server stores it as empty and the provider's title sweep may fill it again).
export const renameJob = (id: string, title: string): Promise<{ id: string; title: string | null }> =>
  request(path(id), { method: "PATCH", body: { title } });

export const recheckJob = (id: string): Promise<{ id: string; recharged: boolean }> =>
  request(`${path(id)}/recheck`, { method: "POST" });

export const recoverJob = (id: string, account: string): Promise<{ command_id: string }> =>
  request(`${path(id)}/recover`, { method: "POST", body: { account } });

export const fetchTitle = (id: string): Promise<{ command_id: string }> =>
  request(`${path(id)}/fetch-title`, { method: "POST" });

export interface RegenerateResult {
  id: string;
  status: string;
  queue_position: number | null;
  replayed: boolean;
}

export const regenerateJob = (id: string): Promise<RegenerateResult> =>
  regenerateCall(jobAction(id), (operationKey) =>
    request<RegenerateResult>(`${path(id)}/regenerate`, { method: "POST", body: { operation_key: operationKey } }),
  );

export const resendWebhook = (id: string): Promise<{ id: string; resent: boolean }> =>
  request(`${path(id)}/resend-webhook`, { method: "POST" });

export const revokeLinks = (id: string): Promise<{ id: string; revoked: number }> =>
  request(`${path(id)}/revoke-links`, { method: "POST" });

// Files go through the admin session as blobs; none of them is ever a public link.
export async function fetchMediaBlob(id: string, role: "output" | "input", idx: number): Promise<Blob> {
  const route = role === "output" ? `outputs/${idx}/content` : `images/${idx}/content`;
  return (await requestRaw(`${path(id)}/${route}`)).blob();
}

export async function fetchDownload(id: string, idx: number): Promise<Blob> {
  return (await requestRaw(`${path(id)}/content/${idx}`)).blob();
}

export type DebugContent = { kind: "image"; blob: Blob } | { kind: "text"; text: string };

// A debug file is a raster image or text (the server turns anything else into text/plain).
export async function fetchDebug(id: string, idx: number): Promise<DebugContent> {
  const response = await requestRaw(`${path(id)}/debug/${idx}`);
  const type = (response.headers.get("content-type") || "").split(";")[0]!.trim().toLowerCase();
  if (type.startsWith("image/")) return { kind: "image", blob: await response.blob() };
  return { kind: "text", text: await response.text() };
}
