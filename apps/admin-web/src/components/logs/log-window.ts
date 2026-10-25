// The date window of the Request logs: a date field is a day, and the window is [that day's midnight, the next day's
// midnight) in the viewer's own time zone, sent as epoch milliseconds. The server reads `until` as exclusive.
import type { LogFilterQuery, LogQuery, LogRow, LogStatus } from "@/api/logs";
import { keyName } from "@/components/jobs/job-text";

export const PAGE_SIZE = 50;
// the rows one CSV export holds at most: the server's own cap (routes_logs.EXPORT_MAX), said in the toast
export const EXPORT_MAX = 10_000;
export const LOG_STATUSES: readonly LogStatus[] = ["all", "2xx", "4xx", "5xx", "pre_job"];

// The Status chips: the canvas's four, the values what the server filters by. The canvas has no place for a failed
// request that made no job (the Jobs stats link here with it), so it is the last chip.
export const STATUS_OPTIONS: ReadonlyArray<{ value: LogStatus; label: string }> = [
  { value: "all", label: "All" },
  { value: "2xx", label: "2xx" },
  { value: "4xx", label: "4xx" },
  { value: "5xx", label: "5xx" },
  { value: "pre_job", label: "Failed before a job" },
];

// 1980 -> "1,980", for durations and counts.
export const thousands = (n: number): string => n.toLocaleString("en-US");

const two = (n: number): string => String(n).padStart(2, "0");

// `20:54:22`, the time column of the list (local time); a dash for a row with no time.
export function clockTime(ms: number | null | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// `24 Oct 14:31:58.903`, the Time of the request drawer; a dash for a row with no time.
export function fullTime(ms: number | null | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${clockTime(ms)}${clockMs(ms)}`;
}

// `.412`, the milliseconds of a time, three digits; empty for a row with no time.
export function clockMs(ms: number | null | undefined): string {
  return ms ? `.${String(new Date(ms).getMilliseconds()).padStart(3, "0")}` : "";
}

// `412 ms`, and from one second `1.84 s` (the list draws that one in amber).
export const durationText = (ms: number): string =>
  ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${thousands(ms)} ms`;
export const isSlow = (ms: number): boolean => ms >= 1000;

// `POST /v1/videos` -> its method and its path; a route with no method keeps all of it as the path.
export function splitRoute(route: string): { method: string; path: string } {
  const at = route.indexOf(" ");
  return at > 0 ? { method: route.slice(0, at), path: route.slice(at + 1) } : { method: "", path: route };
}

// The colour family of a status code: below 400 fine, a client error amber, a server error red.
export const statusTone = (code: number): "ok" | "warn" | "bad" => (code >= 500 ? "bad" : code >= 400 ? "warn" : "ok");

// The colour of an error code: the family of a failing status; red when the request itself went through (a job that
// failed behind a 200).
export const errorTone = (code: number): "warn" | "bad" => (code >= 400 ? (statusTone(code) as "warn" | "bad") : "bad");

// A row's key as Jobs names it: its label, else its prefix; "unknown key" for a row whose key the relay did not know
// (Logs: an auth.invalid_key request).
export const logKey = (row: Pick<LogRow, "key_id" | "key_label" | "key_prefix">): string =>
  row.key_id ? keyName({ id: row.key_id, label: row.key_label, prefix: row.key_prefix }) : "unknown key";

export interface LogFilters {
  q: string;
  status: LogStatus;
  // `YYYY-MM-DD` from a date field, or empty
  from: string;
  to: string;
  // a key's id, or empty for every key
  key?: string;
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

// The calendar day `n` days before `now`, at local midnight: built from the calendar date (not "- n x 24 hours"), so a
// clock change or a month end cannot move it.
export function daysAgo(n: number, now: Date = new Date()): Date {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() - n);
}

// The quick options of the range picker, in the order they are listed: each is [first day, last day], both inclusive.
export const RANGE_SHORTCUTS: ReadonlyArray<{ text: string; value: () => [Date, Date] }> = [
  { text: "Today", value: () => [daysAgo(0), daysAgo(0)] },
  { text: "Yesterday", value: () => [daysAgo(1), daysAgo(1)] },
  { text: "Last 7 days", value: () => [daysAgo(6), daysAgo(0)] },
  { text: "Last 30 days", value: () => [daysAgo(29), daysAgo(0)] },
  { text: "This month", value: () => [daysAgo(new Date().getDate() - 1), daysAgo(0)] },
];

export function isLogStatus(value: unknown): value is LogStatus {
  return typeof value === "string" && (LOG_STATUSES as readonly string[]).includes(value);
}

export function logQuery(filters: LogFilters, provider: string, offset: number, limit = PAGE_SIZE): LogQuery {
  return { limit, offset, ...filterQuery(filters, provider) };
}

// The filters alone: what the list and its CSV export both send.
export function filterQuery(filters: LogFilters, provider: string): LogFilterQuery {
  const query: LogFilterQuery = { status: filters.status };
  if (provider) query.provider = provider;
  if (filters.key) query.key_id = filters.key;
  const q = filters.q.trim();
  if (q) query.q = q;
  if (filters.from) query.since_ms = dayStart(filters.from);
  if (filters.to) query.until_ms = dayAfter(filters.to);
  return query;
}
