// Where a popup opens against its trigger, in window px: one rule for the app's popups.
// - Across: on the trigger edge it is drawn on (`start`: its left edge on the trigger's; `end`: its right edge on the
//   trigger's), on the other edge when the drawn one would pass the window, then kept POPUP_MARGIN inside the window.
//   A popup wider than the window less both margins is narrowed to it.
// - Down: POPUP_GAP under the trigger; POPUP_GAP above it when it would pass the bottom and fits above; when it fits on
//   neither side, on the side with more room, its height cut to that room (it scrolls inside).
export type PopupAlign = "start" | "end";

export interface PopupBox {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface PopupSize {
  width: number;
  height: number;
}

export interface PopupPlace {
  left: number;
  top: number;
  maxWidth?: number;
  maxHeight?: number;
}

export const POPUP_GAP = 6;
export const POPUP_MARGIN = 8;

export function placePopup(trigger: PopupBox, popup: PopupSize, view: PopupSize, align: PopupAlign): PopupPlace {
  const width = Math.min(popup.width, view.width - 2 * POPUP_MARGIN);
  let left = align === "start" ? trigger.left : trigger.right - width;
  if (align === "start" && left + width > view.width) left = trigger.right - width;
  if (align === "end" && left < 0) left = trigger.left;
  left = Math.min(Math.max(left, POPUP_MARGIN), view.width - POPUP_MARGIN - width);
  const place: PopupPlace = { left, top: trigger.bottom + POPUP_GAP };
  if (width < popup.width) place.maxWidth = width;

  const roomBelow = view.height - POPUP_MARGIN - place.top;
  const roomAbove = trigger.top - POPUP_GAP - POPUP_MARGIN;
  if (popup.height <= roomBelow) return place;
  if (popup.height <= roomAbove) return { ...place, top: trigger.top - POPUP_GAP - popup.height };
  if (roomBelow >= roomAbove) return { ...place, maxHeight: Math.max(0, roomBelow) };
  return { ...place, top: POPUP_MARGIN, maxHeight: roomAbove };
}
