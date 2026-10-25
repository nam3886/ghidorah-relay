// The request logs admin routes.
import { request, requestRaw } from "@/api/client";

// all, a status family (the Status chips), or a failed request that made no job
export type LogStatus = "all" | "2xx" | "4xx" | "5xx" | "pre_job";

export interface LogRow {
  id: string;
  at_ms: number | null;
  key_id: string | null;
  key_label: string | null;
  // the key's prefix, null when the row has no key
  key_prefix: string | null;
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

// The filters of the list, which its export takes too.
export interface LogFilterQuery {
  status: LogStatus;
  provider?: string;
  key_id?: string;
  q?: string;
  since_ms?: number;
  until_ms?: number;
}

export interface LogQuery extends LogFilterQuery {
  limit: number;
  offset: number;
  // the viewer's IANA zone: the answer then holds the day totals
  tz?: string;
  // the answer then holds the rows of each Status chip under the other filters
  counts?: boolean;
}

export type StatusCounts = Record<LogStatus, number>;

// Today in numbers, from midnight in the dashboard zone (`day_tz`); `kept_days` is the log retention setting.
export interface LogsToday {
  day_tz: string;
  day_start_ms: number;
  kept_days: number;
  requests: number;
  by_class: Record<"2xx" | "4xx" | "5xx", number>;
  top_code: Record<"4xx" | "5xx", { code: string; count: number } | null>;
  p50_ms: number | null;
  p95_ms: number | null;
}

export const listLogs = (
  query: LogQuery,
): Promise<{ items: LogRow[]; total: number; day_totals?: Record<string, number>; status_counts?: StatusCounts }> =>
  request("/logs", { query: { ...query } });

export const getLogsToday = (): Promise<LogsToday> => request("/logs/today");

// The filtered view as CSV (the newest 10,000 rows at most), redacted by the server.
export const exportLogs = async (query: LogFilterQuery): Promise<Blob> =>
  (await requestRaw("/logs/export", { query: { ...query } })).blob();

export const getLog = (id: string): Promise<LogDetail> => request(`/logs/${encodeURIComponent(id)}`);

export const clearLogs = (): Promise<{ deleted: number }> => request("/logs", { method: "DELETE" });
