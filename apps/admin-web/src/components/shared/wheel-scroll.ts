import type { Directive } from "vue";

// A vertical wheel over a sideways scroll area scrolls it sideways (a mouse wheel, or a vertical trackpad swipe), so a
// chip row, a provider strip or a wide table never needs the arrows or a sideways swipe. The page is never trapped:
// only an area that overflows sideways and not vertically takes the wheel, and at its start or end (in the wheel's
// direction) the wheel passes on to the page. A native sideways delta, Shift+wheel and pinch-zoom (ctrl+wheel) are
// left to the browser. `preventDefault` only when the area actually scrolls.
export const LINE_PX = 32;

type Area = Pick<HTMLElement, "scrollLeft" | "scrollWidth" | "clientWidth" | "scrollHeight" | "clientHeight">;
type Wheel = Pick<WheelEvent, "deltaX" | "deltaY" | "deltaMode" | "shiftKey" | "ctrlKey">;

// The pixels the area moves sideways for this wheel, 0 when the wheel is not its to take.
export function wheelStep(el: Area, e: Wheel): number {
  if (e.ctrlKey || e.shiftKey || e.deltaY === 0 || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return 0;
  const max = el.scrollWidth - el.clientWidth;
  if (max < 1 || el.scrollHeight - el.clientHeight > 1) return 0;
  const unit = e.deltaMode === 1 ? LINE_PX : e.deltaMode === 2 ? Math.round(el.clientWidth * 0.8) : 1;
  const to = Math.min(max, Math.max(0, el.scrollLeft + e.deltaY * unit));
  const step = to - el.scrollLeft;
  return Math.abs(step) < 1 ? 0 : step;
}

export function onWheel(e: WheelEvent): void {
  if (e.defaultPrevented) return; // an inner area (a table in a scroller) already took it
  const el = e.currentTarget as HTMLElement;
  const step = wheelStep(el, e);
  if (!step) return;
  e.preventDefault();
  // a mouse wheel in lines or pages glides like the arrows; pixel deltas (trackpads, Chrome's wheel) follow at once
  if (e.deltaMode === 0) el.scrollLeft += step;
  else el.scrollBy({ left: step, behavior: "smooth" });
}

// v-wheel-x: the only non-passive wheel listener, on the sideways scroll areas themselves.
export const vWheelX: Directive<HTMLElement> = {
  mounted: (el) => el.addEventListener("wheel", onWheel, { passive: false }),
  unmounted: (el) => el.removeEventListener("wheel", onWheel),
};
