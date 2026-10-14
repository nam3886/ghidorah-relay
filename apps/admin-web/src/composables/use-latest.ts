// Generation counter for "only the newest answer counts". Take a ticket before a call; after the await, drop the
// answer (and any error) unless the ticket is still current.
//
//   const ticket = latest.next();
//   const data = await load();
//   if (!latest.isCurrent(ticket)) return;
export function useLatest() {
  let generation = 0;
  return {
    next: (): number => ++generation,
    isCurrent: (ticket: number): boolean => ticket === generation,
    // Makes every ticket taken so far stale (a view leaving, a target changing) without taking a new one.
    invalidate: (): void => {
      generation++;
    },
  };
}
