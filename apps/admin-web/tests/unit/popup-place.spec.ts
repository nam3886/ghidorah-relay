import { describe, expect, it } from "vitest";
import { placePopup, POPUP_GAP, POPUP_MARGIN } from "@/lib/popup-place";

// a trigger from x to x + w, 34 px high, at y
const trigger = (x: number, w: number, y = 15) => ({ left: x, right: x + w, top: y, bottom: y + 34 });
const desktop = { width: 1440, height: 900 };

describe("the popup rule", () => {
  it("starts at the trigger's left edge, under it, when that fits", () => {
    // 6 px under the trigger, as drawn
    expect(placePopup(trigger(1085, 114), { width: 180, height: 160 }, desktop, "start")).toEqual({
      left: 1085,
      top: 15 + 34 + 6,
    });
  });

  it("ends at the trigger's right edge when starting there would pass the window's right edge", () => {
    expect(placePopup(trigger(1264, 108), { width: 200, height: 99 }, desktop, "start").left).toBe(1372 - 200);
  });

  it("ends at the trigger's right edge when drawn so, and starts there when ending would pass the left edge", () => {
    const phone = { width: 390, height: 900 };
    expect(placePopup(trigger(164, 114), { width: 200, height: 190 }, phone, "end").left).toBe(278 - 200);
    expect(placePopup(trigger(20, 114), { width: 200, height: 190 }, phone, "end").left).toBe(20);
  });

  it("keeps the popup inside the window by the margin, in a 320 px window", () => {
    const narrow = { width: 320, height: 640 };
    // the account menu (220) on a trigger at the right edge: it ends at the trigger, inside the window
    expect(placePopup(trigger(268, 44), { width: 220, height: 107 }, narrow, "start").left).toBe(312 - 220);
    // a menu that passes the right edge from the trigger's left and the left edge from its right: its other edge, then
    // moved back inside the window to the margin
    expect(placePopup(trigger(150, 30), { width: 250, height: 107 }, narrow, "start").left).toBe(POPUP_MARGIN);
    expect(placePopup(trigger(150, 30), { width: 250, height: 107 }, narrow, "end").left).toBe(
      320 - POPUP_MARGIN - 250,
    );
    // wider than the window less both margins: narrowed to it, at the left margin
    expect(placePopup(trigger(150, 30), { width: 400, height: 107 }, narrow, "end")).toEqual({
      left: POPUP_MARGIN,
      top: 15 + 34 + POPUP_GAP,
      maxWidth: 320 - 2 * POPUP_MARGIN,
    });
  });

  it("opens above the trigger when it would pass the bottom and fits above", () => {
    const place = placePopup(
      trigger(100, 114, 500),
      { width: 200, height: 300 },
      { width: 1024, height: 600 },
      "start",
    );
    expect(place).toEqual({ left: 100, top: 500 - POPUP_GAP - 300 });
  });

  it("in a short window, on the side with more room, cut to it so it scrolls inside", () => {
    const short = { width: 1024, height: 300 };
    // more room below: under the trigger, as high as the room down to the margin
    expect(placePopup(trigger(100, 114), { width: 200, height: 400 }, short, "start")).toEqual({
      left: 100,
      top: 55,
      maxHeight: 300 - POPUP_MARGIN - 55,
    });
    // more room above: from the top margin down to the gap over the trigger
    expect(placePopup(trigger(100, 114, 200), { width: 200, height: 400 }, short, "start")).toEqual({
      left: 100,
      top: POPUP_MARGIN,
      maxHeight: 200 - POPUP_GAP - POPUP_MARGIN,
    });
  });
});
