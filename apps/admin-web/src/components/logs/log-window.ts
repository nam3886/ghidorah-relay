// The date window of the Request logs: a date field is a day, and the window is [that day's midnight, the next day's
// midnight) in the viewer's own time zone, sent as epoch milliseconds. The server reads `until` as exclusive.
import type { LogQuery, LogStatus } from "@/api/logs";

export const PAGE_SIZE = 50;
export const LOG_STATUSES: readonly LogStatus[] = ["all", "ok", "error", "pre_job"];

export interface LogFilters {
  q: string;
  status: LogStatus;
  // `YYYY-MM-DD` from a date field, or empty
  from: string;
  to: string;
}

function parts(value: string): [number, number, number] {
  const [y, m, d] = value.split("-").map(Number) as [number, number, number];
  return [y, m - 1, d];
}

// Local midnight at the start of that day.
export function dayStart(value: string): number {
  const [y, m, d] = parts(value);
  return new Date(y, m, d).getTime();
}

// Local midnight at the start of the NEXT day, built from the calendar date (not "+24 hours": a day with a clock
// change is 23 or 25 hours long, and a month or year end rolls over by itself).
export function dayAfter(value: string): number {
  const [y, m, d] = parts(value);
  return new Date(y, m, d + 1).getTime();
}

export function isLogStatus(value: unknown): value is LogStatus {
  return typeof value === "string" && (LOG_STATUSES as readonly string[]).includes(value);
}

export function logQuery(filters: LogFilters, provider: string, offset: number): LogQuery {
  const query: LogQuery = { limit: PAGE_SIZE, offset, status: filters.status };
  if (provider) query.provider = provider;
  const q = filters.q.trim();
  if (q) query.q = q;
  if (filters.from) query.since_ms = dayStart(filters.from);
  if (filters.to) query.until_ms = dayAfter(filters.to);
  return query;
}
