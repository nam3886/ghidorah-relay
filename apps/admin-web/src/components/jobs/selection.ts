// What a bulk button acts on: the ticked jobs of the page on screen, in table order. A ticked id that is no longer on
// the page (the list reloaded) is not sent. The same ids give the same Regenerate action (bulkAction sorts them), so a
// retry of an unchanged selection sends the same key and a changed selection is a new action. The table's selection
// column reports which rows are ticked; this only filters and orders them.
export function pickedIds(items: readonly { id: string }[], picked: ReadonlySet<string>): string[] {
  return items.filter((job) => picked.has(job.id)).map((job) => job.id);
}
