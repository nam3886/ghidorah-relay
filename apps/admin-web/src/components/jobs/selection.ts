// What a bulk button acts on: the ticked jobs of the page on screen, in table order. A ticked id that is no longer on
// the page (the list reloaded) is not sent. The same ids give the same Regenerate action (bulkAction sorts them), so a
// retry of an unchanged selection sends the same key and a changed selection is a new action.
export function pickedIds(items: readonly { id: string }[], picked: ReadonlySet<string>): string[] {
  return items.filter((job) => picked.has(job.id)).map((job) => job.id);
}

// The picked set after one job's box is ticked or cleared.
export function toggled(picked: ReadonlySet<string>, id: string, on: boolean): Set<string> {
  const next = new Set(picked);
  if (on) next.add(id);
  else next.delete(id);
  return next;
}

// The picked set after the select-all box: every job of the page, or none.
export function setPage(items: readonly { id: string }[], on: boolean): Set<string> {
  return on ? new Set(items.map((job) => job.id)) : new Set();
}
