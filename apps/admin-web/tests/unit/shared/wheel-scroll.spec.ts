import { describe, expect, it, vi } from "vitest";
import { LINE_PX, onWheel, wheelStep } from "@/components/shared/wheel-scroll";

// a row 300 wide showing 100: it can scroll 0..200 sideways, and no vertical overflow
const area = (scrollLeft: number, over: Partial<{ scrollWidth: number; scrollHeight: number }> = {}) => ({
  scrollLeft,
  scrollWidth: 300,
  clientWidth: 100,
  scrollHeight: 40,
  clientHeight: 40,
  ...over,
});
const wheel = (deltaY: number, over: Partial<WheelEvent> = {}) => ({
  deltaX: 0,
  deltaY,
  deltaMode: 0,
  shiftKey: false,
  ctrlKey: false,
  ...over,
});

describe("a vertical wheel over a sideways scroll area", () => {
  it("scrolls it sideways from the start, the middle, and back from the end", () => {
    expect(wheelStep(area(0), wheel(40))).toBe(40);
    expect(wheelStep(area(100), wheel(40))).toBe(40);
    expect(wheelStep(area(100), wheel(-40))).toBe(-40);
    expect(wheelStep(area(200), wheel(-40))).toBe(-40);
  });

  it("stops at the end it heads for, and passes on once there", () => {
    expect(wheelStep(area(180), wheel(40))).toBe(20);
    expect(wheelStep(area(20), wheel(-40))).toBe(-20);
    expect(wheelStep(area(200), wheel(40))).toBe(0);
    expect(wheelStep(area(0), wheel(-40))).toBe(0);
    expect(wheelStep(area(199.5), wheel(40))).toBe(0);
  });

  it("leaves an area alone that does not overflow sideways, or overflows vertically", () => {
    expect(wheelStep(area(0, { scrollWidth: 100 }), wheel(40))).toBe(0);
    expect(wheelStep(area(0, { scrollHeight: 400 }), wheel(40))).toBe(0);
  });

  it("leaves a sideways swipe, Shift+wheel and pinch-zoom to the browser", () => {
    expect(wheelStep(area(100), wheel(0, { deltaX: 30 }))).toBe(0);
    expect(wheelStep(area(100), wheel(10, { deltaX: 30 }))).toBe(0);
    expect(wheelStep(area(100), wheel(40, { shiftKey: true }))).toBe(0);
    expect(wheelStep(area(100), wheel(40, { ctrlKey: true }))).toBe(0);
    expect(wheelStep(area(100), wheel(40, { deltaX: 3 }))).toBe(40);
  });

  it("turns lines and pages into pixels", () => {
    expect(wheelStep(area(0), wheel(3, { deltaMode: 1 }))).toBe(3 * LINE_PX);
    expect(wheelStep(area(0), wheel(1, { deltaMode: 2 }))).toBe(80);
    expect(wheelStep(area(0), wheel(5, { deltaMode: 2 }))).toBe(200);
  });
});

describe("the wheel listener", () => {
  const fire = (el: object, e: Partial<WheelEvent>) => {
    const ev = { ...wheel(0), defaultPrevented: false, preventDefault: vi.fn(), currentTarget: el, ...e };
    onWheel(ev as unknown as WheelEvent);
    return ev;
  };

  it("prevents the page scroll only when it scrolls the area", () => {
    const el = { ...area(0), scrollBy: vi.fn() };
    const moved = fire(el, { deltaY: 50 });
    expect(moved.preventDefault).toHaveBeenCalledOnce();
    expect(el.scrollLeft).toBe(50);
    el.scrollLeft = 200;
    expect(fire(el, { deltaY: 50 }).preventDefault).not.toHaveBeenCalled();
    expect(el.scrollLeft).toBe(200);
  });

  it("glides for a line wheel, and leaves a wheel an inner area already took", () => {
    const el = { ...area(0), scrollBy: vi.fn() };
    fire(el, { deltaY: 3, deltaMode: 1 });
    expect(el.scrollBy).toHaveBeenCalledWith({ left: 3 * LINE_PX, behavior: "smooth" });
    const taken = fire(el, { deltaY: 50, defaultPrevented: true });
    expect(taken.preventDefault).not.toHaveBeenCalled();
    expect(el.scrollLeft).toBe(0);
  });
});
