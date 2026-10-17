// The desktop Jobs table keeps the drawn column shares, and Account and Key grow as the canvas's auto layout grows a
// content-sized column: to their longest value on the page (the cell padding included), never under their drawn width
// and never over COLUMN_CAP. The Job column gives up the room, so the table keeps its width. With the board's data
// every value fits its drawn width and nothing moves.

export const COLUMN_CAP = 120;

// the drawn shares (% of the table) of the three columns this moves, as JobsView's stylesheet sets them
export const SHARES = { job: 23.3257, account: 6.5438, key: 7.4347 } as const;

export interface ColumnWidths {
  job: number;
  account: number;
  key: number;
}

// null when both values fit their drawn widths (the stylesheet's shares stay); else the three widths in px
export function autoColumns(tableWidth: number, need: { account: number; key: number }): ColumnWidths | null {
  const drawn = {
    job: (tableWidth * SHARES.job) / 100,
    account: (tableWidth * SHARES.account) / 100,
    key: (tableWidth * SHARES.key) / 100,
  };
  const account = Math.max(drawn.account, Math.min(COLUMN_CAP, need.account));
  const key = Math.max(drawn.key, Math.min(COLUMN_CAP, need.key));
  if (account === drawn.account && key === drawn.key) return null;
  return { account, key, job: drawn.job - (account - drawn.account) - (key - drawn.key) };
}

// the same widths, give or take half a pixel (a new measure that changes nothing is not written)
export function sameColumns(a: ColumnWidths | null, b: ColumnWidths | null): boolean {
  if (!a || !b) return a === b;
  return Math.abs(a.job - b.job) < 0.5 && Math.abs(a.account - b.account) < 0.5 && Math.abs(a.key - b.key) < 0.5;
}
