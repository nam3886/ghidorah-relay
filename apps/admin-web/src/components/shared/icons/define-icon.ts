import { defineComponent, h, type PropType } from "vue";
import type { IconNode, IconShape } from "./icon-shapes";

// One local SVG component per canvas icon: viewBox 0 0 24 24, no fill, `currentColor` stroke, round caps and joins.
// `size` is the drawn width and height in px (the canvas draws the same shape at 12 to 22), `stroke` the stroke width
// (1.8 unless the canvas says otherwise). Everything is an attribute, so the CSP (no inline style) holds.
function node(n: IconNode) {
  if ("path" in n) return h("path", { d: n.path });
  if ("circle" in n) {
    const [cx, cy, r] = n.circle;
    return h("circle", { cx, cy, r });
  }
  const [x, y, width, height, rx] = n.rect;
  return h("rect", { x, y, width, height, rx });
}

export function defineIcon(name: string, shape: IconShape) {
  const solid = shape.fill === "solid";
  const stroked = !shape.noStroke;
  return defineComponent({
    name,
    props: {
      size: { type: Number as PropType<number>, default: 16 },
      stroke: { type: Number as PropType<number>, default: 1.8 },
    },
    setup(props) {
      return () =>
        h(
          "svg",
          {
            xmlns: "http://www.w3.org/2000/svg",
            viewBox: "0 0 24 24",
            width: props.size,
            height: props.size,
            fill: solid ? "currentColor" : "none",
            stroke: stroked ? "currentColor" : "none",
            "stroke-width": stroked ? props.stroke : undefined,
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
            "aria-hidden": "true",
            focusable: "false",
          },
          shape.nodes.map(node),
        );
    },
  });
}
