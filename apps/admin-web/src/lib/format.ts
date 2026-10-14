// Times from the API are epoch milliseconds.
export function fmtTime(epochMs: number | null | undefined): string {
  if (!epochMs) return "-";
  return new Date(epochMs).toLocaleString("en-GB", { hour12: false });
}
