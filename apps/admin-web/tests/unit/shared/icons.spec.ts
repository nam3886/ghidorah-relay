import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import type { Component } from "vue";
import * as icons from "@/components/shared/icons";

// The exact shapes of specs/shared.md section 1, written out once more here: a changed path must turn this red.
const EXPECTED: Record<string, string> = {
  IconClose: "path:M18 6 6 18M6 6l12 12",
  IconChevronDown: "path:m6 9 6 6 6-6",
  IconChevronRight: "path:m9 6 6 6-6 6",
  IconMoon: "path:M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z",
  IconCheck: "path:M20 6 9 17l-5-5",
  IconRefresh: "path:M21 12a9 9 0 1 1-2.64-6.36|path:M21 3v6h-6",
  IconPlus: "path:M12 5v14M5 12h14",
  IconMoreVertical: "path:M12 5h.01M12 12h.01M12 19h.01",
  IconMoreHorizontal: "path:M5 12h.01M12 12h.01M19 12h.01",
  IconMoreCircles: "circle:5,12,1|circle:12,12,1|circle:19,12,1",
  IconCopy: "rect:9,9,12,12,2|path:M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1",
  IconImage: "rect:3,3,18,18,2|circle:9,9,2|path:m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21",
  IconDownload: "path:M12 3v12|path:m7 10 5 5 5-5|path:M5 21h14",
  IconAlertCircle: "circle:12,12,9|path:M12 8v5|path:M12 16.5h.01",
  IconAlertCircleJoined: "circle:12,12,9|path:M12 8v5M12 16.5h.01",
  IconAlertCircleShort: "circle:12,12,9|path:M12 8v4|path:M12 16h.01",
  IconAlertTriangle:
    "path:M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z|path:M12 9v4|path:M12 17h.01",
  IconUndo: "path:M9 14 4 9l5-5|path:M20 20v-7a4 4 0 0 0-4-4H4",
  IconCheckCircle: "circle:12,12,9|path:m8 12.5 3 3 5-6",
  IconPencil: "path:M12 20h9|path:M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z",
  IconPencilMenu: "path:M4 20h4L19 9a2.8 2.8 0 0 0-4-4L4 16z|path:m13.5 6.5 4 4",
  IconTrash:
    "path:M3 6h18|path:M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2|path:M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6|path:M10 11v6M14 11v6",
  IconTrashClear: "path:M3 6h18|path:M8 6V4h8v2|path:M6 6l1 14h10l1-14",
  IconTrashMenu: "path:M4 7h16|path:M9 7V4h6v3|path:M6 7l1 13h10l1-13",
  IconEye: "path:M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z|circle:12,12,3",
  IconSearch: "circle:11,11,7|path:m20 20-3.5-3.5",
  IconCamera:
    "path:M4 8a2 2 0 0 1 2-2h2l1.5-2h5L16 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z|circle:12,12.5,3.5",
  IconPower: "path:M12 3v9|path:M6.3 6.8a8 8 0 1 0 11.4 0",
  IconPlay: "path:M8 5.5v13l11-6.5z",
  IconPlayFlat: "path:m8 5 12 7-12 7z",
  IconUpload: "path:M12 16V4|path:m7 9 5-5 5 5|path:M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3",
  IconFileText:
    "path:M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z|path:M14 2v6h6|path:M16 13H8|path:M16 17H8|path:M10 9H8",
  IconLogOut: "path:M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4|path:m16 17 5-5-5-5|path:M21 12H9",
  IconArrowLeft: "path:M19 12H5|path:m12 19-7-7 7-7",
};

function shapes(svg: Element): string {
  return [...svg.children]
    .map((el) => {
      const a = (n: string) => el.getAttribute(n);
      if (el.tagName === "path") return `path:${a("d")}`;
      if (el.tagName === "circle") return `circle:${a("cx")},${a("cy")},${a("r")}`;
      return `rect:${a("x")},${a("y")},${a("width")},${a("height")},${a("rx")}`;
    })
    .join("|");
}

describe("local icons", () => {
  it("has exactly the canvas icons, each with its exact shape", () => {
    const names = Object.keys(icons).filter((name) => name.startsWith("Icon"));
    expect(names.sort()).toEqual(Object.keys(EXPECTED).sort());
    for (const name of names) {
      const svg = mount((icons as unknown as Record<string, Component>)[name]!).element;
      expect(shapes(svg), name).toBe(EXPECTED[name]);
      expect(svg.getAttribute("focusable"), name).toBe("false");
      expect(svg.getAttribute("aria-hidden"), name).toBe("true");
      expect(svg.getAttribute("stroke-linejoin"), name).toBe("round");
      expect(svg.getAttribute("stroke-linecap"), name).toBe("round");
      expect(svg.getAttribute("viewBox"), name).toBe("0 0 24 24");
    }
  });

  it("draws on the 24 box with a currentColor round stroke, 16 px and 1.8 wide by default", () => {
    const svg = mount(icons.IconMoon).element;
    expect(svg.getAttribute("viewBox")).toBe("0 0 24 24");
    expect(svg.getAttribute("width")).toBe("16");
    expect(svg.getAttribute("height")).toBe("16");
    expect(svg.getAttribute("fill")).toBe("none");
    expect(svg.getAttribute("stroke")).toBe("currentColor");
    expect(svg.getAttribute("stroke-width")).toBe("1.8");
    expect(svg.getAttribute("stroke-linecap")).toBe("round");
    expect(svg.getAttribute("focusable")).toBe("false");
    expect(svg.getAttribute("stroke-linejoin")).toBe("round");
    expect(svg.getAttribute("aria-hidden")).toBe("true");
    expect(svg.hasAttribute("style")).toBe(false);
  });

  it("takes its size and stroke from the props", () => {
    const svg = mount(icons.IconCheck, { props: { size: 18, stroke: 2.2 } }).element;
    expect(svg.getAttribute("width")).toBe("18");
    expect(svg.getAttribute("stroke-width")).toBe("2.2");
  });

  it("fills the play triangles and nothing else", () => {
    expect(mount(icons.IconPlay).element.getAttribute("fill")).toBe("currentColor");
    expect(mount(icons.IconPlayFlat).element.getAttribute("fill")).toBe("currentColor");
    expect(mount(icons.IconSearch).element.getAttribute("fill")).toBe("none");
  });

  it("keeps the stroke of the B-States play triangle and draws the P-JobDetail one with no stroke", () => {
    const play = mount(icons.IconPlay).element;
    expect(play.getAttribute("stroke")).toBe("currentColor");
    expect(play.getAttribute("stroke-width")).toBe("1.8");
    expect(play.getAttribute("stroke-linejoin")).toBe("round");
    const flat = mount(icons.IconPlayFlat).element;
    expect(flat.getAttribute("stroke")).toBe("none");
    expect(flat.hasAttribute("stroke-width")).toBe(false);
  });

  it("draws the failed-banner alert with the bar and the dot in one path, unlike the error alert", () => {
    expect(mount(icons.IconAlertCircleJoined).element.querySelectorAll("path")).toHaveLength(1);
    expect(mount(icons.IconAlertCircle).element.querySelectorAll("path")).toHaveLength(2);
  });
});
