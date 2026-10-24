// The Ledger icon set, copied from the canvas (MenusToasts `IC`, the icon-button set every board uses: 16 px, stroke
// 1.75), the bottom bar's 22 px glyphs (JobsTablet, stroke 2) and the arrow scroller's double chevrons (ScrollerSpec,
// stroke 2.2). Each entry is the list of `d` strings of one icon on a 24 box; a circle or rect of the canvas is written
// as the equivalent arc path so every icon is drawn the same way.

// The 33 icon-button glyphs, with the tooltip each one carries in the canvas.
export const IB_ICONS = {
  copy: [
    "M9 9h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H11a2 2 0 0 1-2-2zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1",
  ],
  recheck: ["M21 12a9 9 0 0 1-15.5 6.2L3 16M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M3 21v-5h5"],
  wake: ["M12 21a8 8 0 1 0 0-16a8 8 0 1 0 0 16zM12 9v4l2 2M5 3 2 6M22 6l-3-3"],
  edit: ["M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"],
  delete: [
    "M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6",
  ],
  download: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"],
  external: ["M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"],
  shot: [
    "M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3zM12 10a3 3 0 1 0 0 6a3 3 0 1 0 0-6z",
  ],
  close: ["M18 6 6 18M6 6l12 12"],
  back: ["m15 18-6-6 6-6"],
  expand: ["M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"],
  chevronDown: ["m6 9 6 6 6-6"],
  prev: ["m15 18-6-6 6-6"],
  next: ["m9 18 6-6-6-6"],
  filter: ["M22 3H2l8 9.5V19l4 2v-8.5z"],
  search: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM20 20l-3.5-3.5"],
  export: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"],
  columns: ["M3 3h18v18H3zM9 3v18M15 3v18"],
  settings: ["M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"],
  rotate: ["M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"],
  zoomIn: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM20 20l-3.5-3.5M11 8v6M8 11h6"],
  zoomOut: ["M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14zM20 20l-3.5-3.5M8 11h6"],
  rollback: ["M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11"],
  history: ["M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18zM12 7v5l3 2"],
  resend: ["M22 2 11 13M22 2l-7 20-4-9-9-4z"],
  unlink: [
    "M18.8 13.4l1.7-1.7a5 5 0 0 0-7.1-7.1l-1.7 1.7M5.2 10.6l-1.7 1.7a5 5 0 0 0 7.1 7.1l1.7-1.7M8 2v3M2 8h3M16 22v-3M22 16h-3",
  ],
  link: ["M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"],
  more: ["M5 12h.01M12 12h.01M19 12h.01"],
  check: ["M20 6 9 17l-5-5"],
  preview: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z"],
  proxy: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4"],
  reset: ["M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"],
  plus: ["M12 5v14M5 12h14"],
} as const;
export type IbIconName = keyof typeof IB_ICONS;

// The bottom bar's glyphs (22 px, stroke 2; More at 2.4 in the canvas).
export const NAV_ICONS = {
  jobs: ["M6 4h12a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3V7a3 3 0 0 1 3-3z", "M7 9h10", "M7 13h6"],
  accounts: [
    "M9 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8z",
    "M2 21a7 7 0 0 1 14 0",
    "M16 4.5a4 4 0 0 1 0 7",
    "M22 21a7 7 0 0 0-4-6.3",
  ],
  keys: ["M8 11a4 4 0 1 0 0 8a4 4 0 1 0 0-8z", "m11 12 9-9", "m17 6 3 3", "m15 8 2 2"],
  logs: ["M4 6h16", "M4 12h16", "M4 18h10"],
  alerts: ["M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.9 1.9 0 0 0 3.4 0"],
  frames: [
    "M8 4H5a1 1 0 0 0-1 1v3",
    "M16 4h3a1 1 0 0 1 1 1v3",
    "M8 20H5a1 1 0 0 1-1-1v-3",
    "M16 20h3a1 1 0 0 0 1-1v-3",
    "M8 12h8",
  ],
  settings: [
    "M12 8.8a3.2 3.2 0 1 0 0 6.4a3.2 3.2 0 1 0 0-6.4z",
    "M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1",
  ],
  test: ["M9 3h6", "M10 3v6L4.5 18.5A2 2 0 0 0 6.2 21.5h11.6a2 2 0 0 0 1.7-3L14 9V3", "M7 15h10"],
  more: ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
} as const;
export type NavIconName = keyof typeof NAV_ICONS;

// The arrow scroller's chevron-double, one per side.
export const CHEVRONS_LEFT = ["m11 17-5-5 5-5", "m18 17-5-5 5-5"];
export const CHEVRONS_RIGHT = ["m13 17 5-5-5-5", "m6 17 5-5-5-5"];
