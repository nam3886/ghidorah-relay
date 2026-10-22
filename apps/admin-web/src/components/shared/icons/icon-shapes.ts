// The exact shapes of the canvas icons (specs/shared.md section 1), Lucide-style strokes on a 24 box. Each icon is a
// list of SVG nodes: a path `d`, a circle `[cx, cy, r]` or a rect `[x, y, width, height, rx]`. Nothing here is drawn
// by a package: `define-icon.ts` turns each entry into a component.
export type IconNode =
  { path: string } | { circle: [number, number, number] } | { rect: [number, number, number, number, number] };

export interface IconShape {
  nodes: IconNode[];
  /** Filled glyphs (the play triangles) draw with `fill="currentColor"`. */
  fill?: "stroke" | "solid";
  /** P-JobDetail draws its play triangle with no stroke at all. */
  noStroke?: boolean;
}

const p = (path: string): IconNode => ({ path });
const c = (cx: number, cy: number, r: number): IconNode => ({ circle: [cx, cy, r] });
const r = (x: number, y: number, w: number, h: number, rx: number): IconNode => ({ rect: [x, y, w, h, rx] });

export const ICON_SHAPES = {
  close: { nodes: [p("M18 6 6 18M6 6l12 12")] },
  chevronDown: { nodes: [p("m6 9 6 6 6-6")] },
  chevronRight: { nodes: [p("m9 6 6 6-6 6")] },
  moon: { nodes: [p("M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z")] },
  check: { nodes: [p("M20 6 9 17l-5-5")] },
  refresh: { nodes: [p("M21 12a9 9 0 1 1-2.64-6.36"), p("M21 3v6h-6")] },
  plus: { nodes: [p("M12 5v14M5 12h14")] },
  moreVertical: { nodes: [p("M12 5h.01M12 12h.01M12 19h.01")] },
  moreHorizontal: { nodes: [p("M5 12h.01M12 12h.01M19 12h.01")] },
  // the circles variant of the dots, drawn at 18 in the phone and tablet logs
  moreCircles: { nodes: [c(5, 12, 1), c(12, 12, 1), c(19, 12, 1)] },
  copy: { nodes: [r(9, 9, 12, 12, 2), p("M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1")] },
  image: { nodes: [r(3, 3, 18, 18, 2), c(9, 9, 2), p("m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21")] },
  download: { nodes: [p("M12 3v12"), p("m7 10 5 5 5-5"), p("M5 21h14")] },
  // error alert, toast and failed banner: the exclamation sits slightly low
  alertCircle: { nodes: [c(12, 12, 9), p("M12 8v5"), p("M12 16.5h.01")] },
  // login error and settings notes
  // the failed banner in the drawer: the bar and the dot are one path
  alertCircleJoined: { nodes: [c(12, 12, 9), p("M12 8v5M12 16.5h.01")] },
  alertCircleShort: { nodes: [c(12, 12, 9), p("M12 8v4"), p("M12 16h.01")] },
  alertTriangle: {
    nodes: [
      p("M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"),
      p("M12 9v4"),
      p("M12 17h.01"),
    ],
  },
  undo: { nodes: [p("M9 14 4 9l5-5"), p("M20 20v-7a4 4 0 0 0-4-4H4")] },
  checkCircle: { nodes: [c(12, 12, 9), p("m8 12.5 3 3 5-6")] },
  pencil: { nodes: [p("M12 20h9"), p("M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z")] },
  pencilMenu: { nodes: [p("M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z"), p("m13.5 6.5 4 4")] },
  trash: {
    nodes: [
      p("M3 6h18"),
      p("M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"),
      p("M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"),
      p("M10 11v6M14 11v6"),
    ],
  },
  trashClear: { nodes: [p("M3 6h18"), p("M8 6V4h8v2"), p("M6 6l1 14h10l1-14")] },
  trashMenu: { nodes: [p("M4 7h16"), p("M9 7V4h6v3"), p("M6 7l1 13h10l1-13")] },
  eye: { nodes: [p("M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"), c(12, 12, 3)] },
  search: { nodes: [c(11, 11, 7), p("m20 20-3.5-3.5")] },
  camera: {
    nodes: [p("M4 8a2 2 0 0 1 2-2h2l1.5-2h5L16 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z"), c(12, 12.5, 3.5)],
  },
  power: { nodes: [p("M12 3v9"), p("M6.3 6.8a8 8 0 1 0 11.4 0")] },
  // two slightly different triangles in the canvas: B-States keeps its stroke, P-JobDetail has none
  play: { nodes: [p("M8 5.5v13l11-6.5z")], fill: "solid" },
  playFlat: { nodes: [p("m8 5 12 7-12 7z")], fill: "solid", noStroke: true },
  upload: { nodes: [p("M12 16V4"), p("m7 9 5-5 5 5"), p("M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3")] },
  fileText: {
    nodes: [
      p("M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"),
      p("M14 2v6h6"),
      p("M16 13H8"),
      p("M16 17H8"),
      p("M10 9H8"),
    ],
  },
  logOut: { nodes: [p("M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"), p("m16 17 5-5-5-5"), p("M21 12H9")] },
  arrowLeft: { nodes: [p("M19 12H5"), p("m12 19-7-7 7-7")] },
  // The picture viewer's glyphs, which the canvas does not draw (D125), drawn from its own: chevron left mirrors
  // chevron right; zoom in and out are the search glyph with a plus or a minus; rotate right is the refresh glyph,
  // rotate left its mirror; fit is four corners in the same stroke.
  chevronLeft: { nodes: [p("m15 6-6 6 6 6")] },
  zoomIn: { nodes: [c(11, 11, 7), p("m20 20-3.5-3.5"), p("M11 8v6M8 11h6")] },
  zoomOut: { nodes: [c(11, 11, 7), p("m20 20-3.5-3.5"), p("M8 11h6")] },
  rotateLeft: { nodes: [p("M3 12a9 9 0 1 0 2.64-6.36"), p("M3 3v6h6")] },
  fit: {
    nodes: [
      p("M8 3H5a2 2 0 0 0-2 2v3"),
      p("M21 8V5a2 2 0 0 0-2-2h-3"),
      p("M3 16v3a2 2 0 0 0 2 2h3"),
      p("M16 21h3a2 2 0 0 0 2-2v-3"),
    ],
  },
} satisfies Record<string, IconShape>;

export type IconName = keyof typeof ICON_SHAPES;
