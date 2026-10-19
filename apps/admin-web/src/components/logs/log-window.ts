// The date window of the Request logs: a date field is a day, and the window is [that day's midnight, the next day's
// midnight) in the viewer's own time zone, sent as epoch milliseconds. The server reads `until` as exclusive.
import type { LogQuery, LogRow, LogStatus } from "@/api/logs";
import { keyName } from "@/components/jobs/job-text";

export const PAGE_SIZE = 50;
export const LOG_STATUSES: readonly LogStatus[] = ["all", "ok", "error", "pre_job"];

// The Status select: the words are the canvas's, the values what the server filters by.
export const STATUS_OPTIONS: ReadonlyArray<{ value: LogStatus; label: string }> = [
  { value: "all", label: "All" },
  { value: "ok", label: "OK below 400" },
  { value: "error", label: "Errors" },
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

// `15/10 20:33:48`, the Time of the request drawer.
export function dateClock(ms: number | null | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  return `${two(d.getDate())}/${two(d.getMonth() + 1)} ${clockTime(ms)}`;
}

// A row's key as Jobs names it: its label, else its prefix; a dash for a row with no key.
export const logKey = (row: Pick<LogRow, "key_id" | "key_label" | "key_prefix">): string =>
  row.key_id ? keyName({ id: row.key_id, label: row.key_label, prefix: row.key_prefix }) : "—";

// The status of a request in words of the row tint: only a server error fills the row.
export const isServerError = (code: number): boolean => code >= 500;

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
];

export function isLogStatus(value: unknown): value is LogStatus {
  return typeof value === "string" && (LOG_STATUSES as readonly string[]).includes(value);
}

// The IANA zone of the viewer's browser: the day headers count each local day in it.
export const viewerZone = (): string => Intl.DateTimeFormat().resolvedOptions().timeZone;

// `2026-10-15`: the local day of a time, the key the server's day totals use.
export function dayKey(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
}

// The muted tail of a day header: the day's total from the server under the current filters, else the rows on the page.
export function dayNote(shown: number, total?: number): string {
  const n = total ?? shown;
  return `${thousands(n)} ${n === 1 ? "request" : "requests"} shown`;
}

export function logQuery(filters: LogFilters, provider: string, offset: number, limit = PAGE_SIZE): LogQuery {
  const query: LogQuery = { limit, offset, status: filters.status, tz: viewerZone() };
  if (provider) query.provider = provider;
  const q = filters.q.trim();
  if (q) query.q = q;
  if (filters.from) query.since_ms = dayStart(filters.from);
  if (filters.to) query.until_ms = dayAfter(filters.to);
  return query;
}
