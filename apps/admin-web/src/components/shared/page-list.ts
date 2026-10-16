// The numbered slots of the pager, at most five numbers and plain ellipses (specs/shared.md 12a.4). The canvas draws
// one state, page 1 of 133 = `1 2 3 … 133`; the other states follow from it:
//   five pages or fewer   every number, no ellipsis
//   page 3 or lower       1 2 3 … N
//   page N-2 or higher    1 … N-2 N-1 N
//   in between            1 … c-1 c c+1 … N
export type PageSlot = number | "…";

export function pageSlots(current: number, count: number): PageSlot[] {
  if (count <= 5) return Array.from({ length: count }, (_, i) => i + 1);
  if (current <= 3) return [1, 2, 3, "…", count];
  if (current >= count - 2) return [1, "…", count - 2, count - 1, count];
  return [1, "…", current - 1, current, current + 1, "…", count];
}
