// The request logs admin routes.
import { request } from "@/api/client";

export type LogStatus = "all" | "ok" | "error" | "pre_job";

export interface LogRow {
  id: string;
  at_ms: number | null;
  key_id: string | null;
  key_label: string | null;
  provider: string | null;
  route: string;
  status_code: number;
  job_id: string | null;
  job_title: string | null;
  duration_ms: number;
  error_code: string | null;
}

export interface LogDetail extends LogRow {
  // redacted by the server
  request: unknown;
  response: unknown;
}

export interface LogQuery {
  limit: number;
  offset: number;
  status: LogStatus;
  provider?: string;
  q?: string;
  since_ms?: number;
  until_ms?: number;
}

export const listLogs = (query: LogQuery): Promise<{ items: LogRow[]; total: number }> =>
  request("/logs", { query: { ...query } });

export const getLog = (id: string): Promise<LogDetail> => request(`/logs/${encodeURIComponent(id)}`);

export const clearLogs = (): Promise<{ deleted: number }> => request("/logs", { method: "DELETE" });
