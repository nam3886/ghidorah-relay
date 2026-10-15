// Times from the API are epoch milliseconds. The space after the comma is a no-break space (code 160), so a date and
// its clock stay on one line and a table row keeps one line of height.
const NO_BREAK_SPACE = String.fromCharCode(160);

export function fmtTime(epochMs: number | null | undefined): string {
  if (!epochMs) return "-";
  return new Date(epochMs).toLocaleString("en-GB", { hour12: false }).replace(", ", `,${NO_BREAK_SPACE}`);
}
