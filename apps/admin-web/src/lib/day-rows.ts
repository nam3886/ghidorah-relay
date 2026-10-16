// Day groups for a time-ordered table: one flat list of day header rows and data rows, and the `span-method` that
// draws a header across the table. DataTable recognises day rows by `isDayRow`: they are never selectable, never
// clickable, and never reach `selection-change` or a bulk request.

const DAY_MARK = Symbol("day-row");

export interface DayRow {
  kind: "day";
  // a unique row key, never a job id
  id: string;
  // "Today · 15 Oct"
  label: string;
  // the muted tail of the header ("8 jobs shown"), when the view gave one
  note?: string;
  [DAY_MARK]: true;
}

export type DayItem<T> = T | DayRow;

export function isDayRow(value: unknown): value is DayRow {
  return typeof value === "object" && value !== null && (value as Partial<DayRow>)[DAY_MARK] === true;
}

export interface SpanArgs {
  row: unknown;
  columnIndex: number;
}

// Element Plus span-method: on a day row, column 0 stays a single (empty) cell and column 1 spans the rest; on every
// other row the cell is untouched. `columnCount` counts every column, the selection and Actions columns too.
// `leading` is how many leading cells stay empty: 1 with a selection column, 0 without one.
export function daySpanMethod(columnCount: number, leading = 1) {
  return ({ row, columnIndex }: SpanArgs): [number, number] | undefined => {
    if (!isDayRow(row)) return undefined;
    if (columnIndex < leading) return [1, 1];
    if (columnIndex === leading) return [1, Math.max(1, columnCount - leading)];
    return [0, 0];
  };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
const startOfDay = (ms: number): number => {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
};

// "Today · 15 Oct", "Yesterday · 14 Oct", "13 Oct", and "13 Oct 2025" for another year. Local time of the browser.
export function dayLabel(ms: number, now: number = Date.now()): string {
  const d = new Date(ms);
  const date = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  const days = Math.round((startOfDay(now) - startOfDay(ms)) / 86_400_000);
  if (days === 0) return `Today · ${date}`;
  if (days === 1) return `Yesterday · ${date}`;
  return d.getFullYear() === new Date(now).getFullYear() ? date : `${date} ${d.getFullYear()}`;
}

export interface GroupOptions<T> {
  // the row's time in epoch ms; a row with no time joins the group above it (or an "Unknown date" group first)
  at: (row: T) => number | null | undefined;
  // the muted tail of a header, from the number of data rows under it
  note?: (count: number) => string;
  now?: number;
  // see daySpanMethod
  columnCount?: number;
  leading?: number;
}

// Rows are grouped by consecutive local calendar day, so the list must already be time-ordered (as the API returns it).
export function groupByDay<T extends object>(
  rows: readonly T[],
  options: GroupOptions<T>,
): { items: DayItem<T>[]; spanMethod: ReturnType<typeof daySpanMethod> } {
  const now = options.now ?? Date.now();
  const items: DayItem<T>[] = [];
  let header: DayRow | undefined;
  let count = 0;
  let current: string | undefined;
  const close = () => {
    if (header && options.note) header.note = options.note(count);
  };
  for (const row of rows) {
    const ms = options.at(row);
    const label = typeof ms === "number" ? dayLabel(ms, now) : (current ?? "Unknown date");
    if (label !== current) {
      close();
      header = { kind: "day", id: `day:${items.length}:${label}`, label, [DAY_MARK]: true };
      items.push(header);
      current = label;
      count = 0;
    }
    items.push(row);
    count += 1;
  }
  close();
  return { items, spanMethod: daySpanMethod(options.columnCount ?? 1, options.leading ?? 1) };
}
