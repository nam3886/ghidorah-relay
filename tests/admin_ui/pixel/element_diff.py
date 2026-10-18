"""Element by element comparison of a canvas board (or a part of one) with the app in the same state.

A whole-screen pixel percentage hides local defects (a clipped label is a few hundred pixels of a 1440 x 1374 board),
so this compares what the eye compares: every visible thing that holds text or draws a box.

`extract(page, root, region)` runs in a page (the canvas render or the app) and lists, relative to the `root` element:
- text runs: each element's own text (its direct text nodes), with the box of the glyphs (a Range, trimmed of spaces,
  clipped by every ancestor that hides overflow), font family, size, weight, line height, letter spacing, transform,
  colour, decoration and the effective opacity;
- boxes: each element that paints something (a border, a background that differs from what is under it, a shadow,
  an outline), and every svg, img and input, with its border box, background, borders, radius, padding, shadow,
  outline, opacity and a signature (aria-label, title, its text, or an svg's shapes); an input also with its margin
  and its state: checked, and indeterminate (the mixed box: a dash, neither empty nor ticked).

`diff(canvas, app)` pairs them: text runs by equal text, boxes by equal signature (nearest first in both), then what is
left by position (same kind, overlap of at least half). Every pair is compared property by property: a box edge more
than 0.5 px off, or any style value that is not equal, is a difference. What is left unpaired on either side is listed.
A padding is compared only between two boxes that both hold their own text: elsewhere the text runs inside show
where it puts things, whichever element of the markup carries it.
`markdown(...)` writes the table; `side_by_side(...)` puts the canvas, the app and the pixel diff in one PNG.
`popup_place(page, popup, trigger)` checks an open popup against its trigger: a list fixed in the body is no part of the
trigger's markup, so the tables never see it cover the trigger; it must sit `gap` px under the trigger or above it.
`extract(..., opaque=, skip=)`: boxes whose inside is data (a thumbnail's picture: the box is compared, not what it
holds), and parts left out whole (sections an accepted deviation adds). Two boxes paired by their place must carry the
same accessible name. `place_of` / `place_differences`: where a root or a scrim sits in the viewport and what it paints.

Text on one side that only differs from its partner because it is live data (a time, a total) is listed apart when the
entry names it (`data`), so the table that has to be empty holds only real differences."""

import base64
import math
from dataclasses import dataclass, field

EXTRACT_JS = r"""({root, region, opaque, skip}) => {
  const rootEl = root ? document.querySelector(root) : document.documentElement;
  if (!rootEl) return {error: "no element matches " + root};
  // `opaque`: its box is listed, what is inside it is data (a thumbnail); `skip`: left out with all it holds
  const matches = (el, sel) => Boolean(sel) && el.matches(sel);
  const rr = rootEl.getBoundingClientRect();
  const ox = root ? rr.left : 0, oy = root ? rr.top : 0;
  const area = region
    ? {l: region[0], t: region[1], r: region[0] + region[2], b: region[1] + region[3]}
    : (root ? {l: rr.left, t: rr.top, r: rr.right, b: rr.bottom} : {l: 0, t: 0, r: innerWidth, b: innerHeight});
  const SKIP = new Set(["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT", "OPTION", "HEAD", "TITLE", "META", "LINK"]);
  const LEAF = new Set(["svg", "IMG", "INPUT", "CANVAS", "VIDEO", "SELECT", "TEXTAREA"]);
  const alpha = (c) => {
    if (!c || c === "transparent") return 0;
    const m = c.match(/rgba?\(([^)]*)\)/);
    if (!m) return 1;
    const p = m[1].split(/[ ,/]+/).filter(Boolean);
    return p.length > 3 ? parseFloat(p[3]) : 1;
  };
  const r1 = (n) => Math.round(n * 10) / 10;
  const rel = (r) => [r1(r.l - ox), r1(r.t - oy), r1(r.r - r.l), r1(r.b - r.t)];
  const clipCache = new Map();
  // the visible part of an element: every ancestor that does not let overflow show cuts its box down
  const clipOf = (el) => {
    if (clipCache.has(el)) return clipCache.get(el);
    let c = {l: -1e9, t: -1e9, r: 1e9, b: 1e9};
    const parent = el.parentElement;
    if (parent && getComputedStyle(el).position !== "fixed") {
      const pc = clipOf(parent);
      c = {...pc};
      const s = getComputedStyle(parent);
      if (s.overflowX !== "visible" || s.overflowY !== "visible") {
        const b = parent.getBoundingClientRect();
        if (s.overflowX !== "visible") { c.l = Math.max(c.l, b.left); c.r = Math.min(c.r, b.right); }
        if (s.overflowY !== "visible") { c.t = Math.max(c.t, b.top); c.b = Math.min(c.b, b.bottom); }
      }
    }
    clipCache.set(el, c);
    return c;
  };
  const cut = (r, c) => ({l: Math.max(r.l, c.l, area.l), t: Math.max(r.t, c.t, area.t),
                          r: Math.min(r.r, c.r, area.r), b: Math.min(r.b, c.b, area.b)});
  const empty = (r) => r.r - r.l < 0.5 || r.b - r.t < 0.5;
  const opCache = new Map();
  const opacityOf = (el) => {
    if (!el || el.nodeType !== 1) return 1;
    if (opCache.has(el)) return opCache.get(el);
    const v = parseFloat(getComputedStyle(el).opacity) * opacityOf(el.parentElement);
    opCache.set(el, v);
    return v;
  };
  // the background an element is painted over: the nearest ancestor's own non-transparent background
  const under = (el) => {
    for (let a = el.parentElement; a; a = a.parentElement) {
      const bg = getComputedStyle(a).backgroundColor;
      if (alpha(bg) > 0) return bg;
    }
    return "rgb(255, 255, 255)";
  };
  const norm = (t) => t.replace(/\s+/g, " ").trim();
  const firstFamily = (f) => f.split(",")[0].replace(/["']/g, "").trim();
  const sides = ["Top", "Right", "Bottom", "Left"];
  const shapes = (svg) => [...svg.querySelectorAll("path, circle, rect, line, polyline, polygon")].map((n) =>
      n.tagName === "path" ? n.getAttribute("d") : n.tagName + ["cx", "cy", "r", "x", "y", "width", "height", "rx",
      "points"].map((a) => n.getAttribute(a) ?? "").join(",")).join(" | ");
  const label = (el) => el.getAttribute("aria-label") || el.getAttribute("title") || "";
  const texts = [], boxes = [];
  const walk = (el) => {
    if (SKIP.has(el.tagName) || matches(el, skip)) return;
    const s = getComputedStyle(el);
    if (s.display === "none") return;
    const op = opacityOf(el);
    const rect = el.getBoundingClientRect();
    const box = {l: rect.left, t: rect.top, r: rect.right, b: rect.bottom};
    const seen = cut(box, clipOf(el));
    const shown = s.visibility !== "hidden" && op > 0.01;
    const tag = el.tagName;
    const leaf = LEAF.has(tag) || el instanceof SVGSVGElement || matches(el, opaque);
    // own text: the direct text nodes, as one run
    if (shown && !leaf && !(el instanceof SVGElement)) {
      let own = "", run = null;
      for (const n of el.childNodes) {
        if (n.nodeType !== 3 || !n.data.trim()) continue;
        own += " " + n.data;
        const start = n.data.search(/\S/), end = n.data.length - n.data.match(/\s*$/)[0].length;
        const range = document.createRange();
        range.setStart(n, start);
        range.setEnd(n, end);
        for (const q of range.getClientRects()) {
          if (q.width < 0.5 && q.height < 0.5) continue;
          run = run ? {l: Math.min(run.l, q.left), t: Math.min(run.t, q.top), r: Math.max(run.r, q.right),
                       b: Math.max(run.b, q.bottom)} : {l: q.left, t: q.top, r: q.right, b: q.bottom};
        }
      }
      own = norm(own);
      if (own && run) {
        // the text is inside `el`: its own hidden overflow cuts it too (an ellipsis)
        const within = {...clipOf(el)};
        if (s.overflowX !== "visible") {
          within.l = Math.max(within.l, rect.left); within.r = Math.min(within.r, rect.right);
        }
        if (s.overflowY !== "visible") {
          within.t = Math.max(within.t, rect.top); within.b = Math.min(within.b, rect.bottom);
        }
        const vis = cut(run, within);
        if (!empty(vis)) {
          texts.push({kind: "text", text: own, tag: tag.toLowerCase(), rect: rel(vis), full: rel(run),
            cls: typeof el.className === "string" ? el.className : "",
            clipped: vis.l - run.l > 0.5 || run.r - vis.r > 0.5 || vis.t - run.t > 0.5 || run.b - vis.b > 0.5,
            style: {family: firstFamily(s.fontFamily), size: s.fontSize, weight: s.fontWeight,
                    lineHeight: s.lineHeight, letterSpacing: s.letterSpacing, transform: s.textTransform,
                    color: s.color, decoration: s.textDecorationLine, numeric: s.fontVariantNumeric,
                    opacity: String(Math.round(op * 100) / 100)}});
        }
      }
    }
    if (shown && !empty(seen)) {
      const border = sides.map((d) => {
        const w = parseFloat(s["border" + d + "Width"]);
        const st = s["border" + d + "Style"];
        return w > 0 && st !== "none" && st !== "hidden" && alpha(s["border" + d + "Color"]) > 0
          ? w + "px " + st + " " + s["border" + d + "Color"] : "none";
      });
      const bg = s.backgroundColor;
      const floating = s.position === "fixed" || s.position === "sticky" || s.position === "absolute";
      // the compared root's own fill always counts: what lies under it is outside the comparison
      const paints = alpha(bg) > 0 && (el === rootEl || floating || bg !== under(el));
      const shadow = s.boxShadow !== "none";
      const outline = s.outlineStyle !== "none" && parseFloat(s.outlineWidth) > 0;
      const image = s.backgroundImage !== "none";
      if (leaf || paints || shadow || outline || image || border.some((b) => b !== "none")) {
        const sig = el instanceof SVGSVGElement ? "svg " + rect.width + "x" + rect.height + " " + shapes(el)
          : tag === "INPUT" ? "input " + el.type + " " + label(el)
          : label(el) || (matches(el, opaque) ? "" : norm(el.textContent || "").slice(0, 80));
        boxes.push({kind: el instanceof SVGSVGElement ? "icon" : (tag === "INPUT" || tag === "IMG" ? "control" : "box"),
          sig, label: label(el), root: el === rootEl, tag: tag.toLowerCase(), role: el.getAttribute("role") || "",
          rect: rel(seen), full: rel(box),
          holdsText: [...el.childNodes].some((n) => n.nodeType === 3 && n.data.trim() !== ""),
          style: {background: paints ? bg : "none", border: border.join(" / "),
                  radius: sides.map((d, i) => s["border" + ["TopLeft", "TopRight", "BottomRight", "BottomLeft"][i] +
                    "Radius"]).join(" "),
                  padding: sides.map((d) => s["padding" + d]).join(" "),
                  shadow: s.boxShadow, outline: outline ? s.outlineWidth + " " + s.outlineStyle + " " +
                    s.outlineColor + " offset " + s.outlineOffset : "none",
                  color: el instanceof SVGSVGElement ? "stroke " + s.stroke + " fill " + s.fill : "",
                  checked: tag === "INPUT" ? String(el.checked) : "",
                  indeterminate: tag === "INPUT" ? String(el.indeterminate) : "",
                  margin: tag === "INPUT" ? sides.map((d) => s["margin" + d]).join(" ") : "",
                  opacity: String(Math.round(op * 100) / 100)}});
      }
    }
    if (leaf) return;
    for (const child of el.children) walk(child);
  };
  walk(rootEl);
  return {texts, boxes, width: r1(rr.width), height: r1(rr.height)};
}"""

EDGE_PX = 0.5
POPUP_JS = r"""([popup, trigger]) => {
  const box = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return [r.left, r.top, r.right, r.bottom];
  };
  return {popup: box(popup), trigger: box(trigger)};
}"""
MAX_PAIR_PX = 400          # two equal texts further apart than this are not one element moved
MIN_OVERLAP = 0.5          # position pairing: intersection over union


@dataclass
class Result:
    name: str
    pairs: list = field(default_factory=list)          # (canvas item, app item, how)
    differences: list = field(default_factory=list)    # (canvas item, app item, property, canvas value, app value)
    data: list = field(default_factory=list)           # pairs whose texts differ only as live data
    canvas_only: list = field(default_factory=list)
    app_only: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def failures(self) -> int:
        return len(self.differences) + len(self.canvas_only) + len(self.app_only)


def extract(page, root: str | None = None, region: tuple | None = None, opaque: str = "", skip: str = "") -> dict:
    """`opaque`: elements whose box is compared and whose inside is data (a thumbnail: the picture is the job's);
    `skip`: elements left out with everything inside them (a section an accepted deviation adds)."""
    got = page.evaluate(EXTRACT_JS, {"root": root, "region": list(region) if region else None, "opaque": opaque,
                                     "skip": skip}, isolated_context=False)
    if "error" in got:
        raise AssertionError(got["error"])
    return got


PLACE_JS = r"""(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  const k = (n) => Math.round(n * 10) / 10;
  return {rect: [k(r.left), k(r.top), k(r.width), k(r.height)], background: s.backgroundColor, shadow: s.boxShadow};
}"""


def place_of(page, selector: str) -> dict | None:
    """Where an element sits in the viewport (x, y, w, h) and what it paints there (its background and shadow): the
    tables place everything relative to the compared root, so the root itself, and a scrim beside it, are read here."""
    return page.evaluate(PLACE_JS, selector, isolated_context=False)


def place_differences(canvas: dict | None, app: dict | None) -> list[str]:
    """What differs between two `place_of` readings: a box edge more than EDGE_PX off, the background, the shadow."""
    if not canvas or not app:
        return ["missing on the %s" % ("canvas" if not canvas else "app")]
    out = []
    if any(abs(c - a) > EDGE_PX for c, a in zip(canvas["rect"], app["rect"], strict=True)):
        out.append("box %s,%s %sx%s / %s,%s %sx%s" % (*canvas["rect"], *app["rect"]))
    out += ["%s %s / %s" % (k, canvas[k], app[k]) for k in ("background", "shadow") if canvas[k] != app[k]]
    return out


def _distance(a, b) -> float:
    return math.hypot(a["rect"][0] - b["rect"][0], a["rect"][1] - b["rect"][1])


def _overlap(a, b) -> float:
    ax, ay, aw, ah = a["rect"]
    bx, by, bw, bh = b["rect"]
    w = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = w * h
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _greedy(canvas, app, key, score, accept):
    """Pair items with an equal `key`, the best `score` first (lower is better), each item at most once."""
    candidates = []
    for i, c in enumerate(canvas):
        for j, a in enumerate(app):
            if key(c) == key(a):
                s = score(c, a)
                if accept(s):
                    candidates.append((s, i, j))
    used_c, used_a, out = set(), set(), []
    for _s, i, j in sorted(candidates):
        if i in used_c or j in used_a:
            continue
        used_c.add(i)
        used_a.add(j)
        out.append((canvas[i], app[j]))
    rest_c = [c for i, c in enumerate(canvas) if i not in used_c]
    rest_a = [a for j, a in enumerate(app) if j not in used_a]
    return out, rest_c, rest_a


def _compare(c, a, out, data=False, loose=None):
    free = "xw" if data else ""
    for role in {c.get("role", ""), a.get("role", ""), "*"}:
        free += (loose or {}).get(role, "")
    for axis, cv, av in zip("xywh", c["rect"], a["rect"], strict=True):
        if axis in free:
            continue
        if abs(cv - av) > EDGE_PX:
            out.append((c, a, "box", "%s,%s %sx%s" % tuple(c["rect"]), "%s,%s %sx%s" % tuple(a["rect"])))
            break
    if c["kind"] == "text" and c.get("clipped") != a.get("clipped"):
        out.append((c, a, "clipped", str(c.get("clipped")), str(a.get("clipped"))))
    for prop, cv in c["style"].items():
        # a padding is compared between two boxes that both hold their text themselves; otherwise it is where the text
        # lands (compared as a text run) that it decides, wherever the markup puts it (a td or a div inside it)
        if prop == "padding" and not (c.get("holdsText") and a.get("holdsText")):
            continue
        av = a["style"].get(prop)
        if cv != av:
            out.append((c, a, prop, cv, av))


def diff(name: str, canvas: dict, app: dict, data: tuple = (), data_app: tuple = (),
         loose: dict | None = None) -> Result:
    """Pair the canvas items with the app's and compare them. `data`: canvas texts that are live data (a pair by
    position whose canvas text is one of these is listed under `data`, not as a difference of text); `data_app`: app
    texts that are live data where the canvas has none (more options in a list). `loose`: a role (or "*" for every
    item) -> the box axes ("xywh") not compared for it, where the size is data (a list of more options)."""
    res = Result(name)
    near = lambda s: s <= MAX_PAIR_PX  # noqa: E731
    texts, rest_ct, rest_at = _greedy(canvas["texts"], app["texts"], lambda t: t["text"], _distance, near)
    signed, rest_cb, rest_ab = _greedy([b for b in canvas["boxes"] if b["sig"]], [b for b in app["boxes"] if b["sig"]],
                                       lambda b: (b["kind"], b["sig"]), _distance, near)
    rest_cb += [b for b in canvas["boxes"] if not b["sig"]]
    rest_ab += [b for b in app["boxes"] if not b["sig"]]
    by_place = lambda c, a: -_overlap(c, a)  # noqa: E731
    fits = lambda s: -s >= MIN_OVERLAP  # noqa: E731
    placed_t, rest_ct, rest_at = _greedy(rest_ct, rest_at, lambda t: "text", by_place, fits)
    placed_b, rest_cb, rest_ab = _greedy(rest_cb, rest_ab, lambda b: b["kind"], by_place, fits)
    for c, a in texts + signed:
        res.pairs.append((c, a, "same"))
        _compare(c, a, res.differences, loose=loose)
    for c, a in placed_t:
        res.pairs.append((c, a, "place"))
        live = c["text"] in data
        if live:
            res.data.append((c, a))
        else:
            res.differences.append((c, a, "text", c["text"], a["text"]))
        _compare(c, a, res.differences, data=live, loose=loose)
    for c, a in placed_b:
        res.pairs.append((c, a, "place"))
        # paired by place, so their names differ: an accessible name the canvas gives is part of the design (not the
        # compared root's: a reference card of B-States is named after the card, "Jobs, no results")
        if c.get("label") and not c.get("root") and c["label"] != a.get("label") and c["label"] not in data:
            res.differences.append((c, a, "label", c["label"], a.get("label") or "(none)"))
        _compare(c, a, res.differences, loose=loose)
    res.canvas_only = [c for c in rest_ct if c["text"] not in data] + rest_cb
    res.app_only = [a for a in rest_at if a["text"] not in data_app] + rest_ab
    res.data += [(c, None) for c in rest_ct if c["text"] in data]
    res.data += [(None, a) for a in rest_at if a["text"] in data_app]
    res.counts = {"canvas texts": len(canvas["texts"]), "canvas boxes": len(canvas["boxes"]),
                  "app texts": len(app["texts"]), "app boxes": len(app["boxes"]),
                  "paired by text or signature": len(texts) + len(signed),
                  "paired by position": len(placed_t) + len(placed_b)}
    return res


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _what(item) -> str:
    if item["kind"] == "text":
        return 'text "%s" (%s)' % (item["text"][:60], item["tag"])
    sig = item["sig"]
    if item["kind"] == "icon":
        sig = sig[:70] + ("…" if len(sig) > 70 else "")
    return '%s %s%s "%s"' % (item["kind"], item["tag"], ("[%s]" % item["role"]) if item["role"] else "", sig[:60])


def popup_place(page, popup: str, trigger: str, gap: float = 6) -> str | None:
    """None when the open `popup` sits `gap` px under its `trigger` or `gap` px above it (so it covers no part of it),
    else what is wrong, with both boxes (viewport px: left, top, right, bottom)."""
    got = page.evaluate(POPUP_JS, [popup, trigger])
    p, t = got["popup"], got["trigger"]
    if not p or not t:
        return "no element matches %s" % (popup if not p else trigger)
    under, above = p[1] - t[3], t[1] - p[3]
    if abs(under - gap) <= EDGE_PX or abs(above - gap) <= EDGE_PX:
        return None
    return "the popup %s is %.1f px under / %.1f px above the trigger %s, not %s px clear" % (
        [round(v, 1) for v in p], under, above, [round(v, 1) for v in t], gap)


def markdown(res: Result, title: str, notes: str = "") -> str:
    lines = ["# Element diff: %s" % title, ""]
    if notes:
        lines += [notes, ""]
    lines += ["Counts: " + ", ".join("%s %d" % kv for kv in res.counts.items()) + ".", "",
              "Every box edge more than %.1f px off and every style value that is not equal is a difference. Positions "
              "are CSS px relative to the compared root (x,y w x h); colours are computed values." % EDGE_PX, "",
              "## Differences (%d)" % len(res.differences), ""]
    if res.differences:
        lines += ["| # | element | property | canvas | app |", "|---|---|---|---|---|"]
        for n, (c, _a, prop, cv, av) in enumerate(res.differences, 1):
            lines.append("| %d | %s | %s | %s | %s |" % (n, _cell(_what(c)), prop, _cell(cv), _cell(av)))
    else:
        lines.append("None.")
    lines += ["", "## Canvas elements with no app counterpart (%d)" % len(res.canvas_only), ""]
    lines += _items(res.canvas_only)
    lines += ["", "## App elements with no canvas counterpart (%d)" % len(res.app_only), ""]
    lines += _items(res.app_only)
    lines += ["", "## Live data (%d): same place, text is data" % len(res.data), ""]
    if res.data:
        lines += ["| canvas | app |", "|---|---|"]
        lines += ["| %s | %s |" % (_cell(c["text"]) if c else "(none)", _cell(a["text"]) if a else "(none)")
                  for c, a in res.data]
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def _items(items) -> list[str]:
    if not items:
        return ["None."]
    out = ["| # | element | x,y w x h |", "|---|---|---|"]
    for n, item in enumerate(sorted(items, key=lambda i: (i["rect"][1], i["rect"][0])), 1):
        out.append("| %d | %s | %s,%s %sx%s |" % (n, _cell(_what(item)), *item["rect"]))
    return out


SIDE_BY_SIDE_JS = r"""async ({images, gap}) => {
  const bitmaps = await Promise.all(images.map(async (b64) =>
    createImageBitmap(await (await fetch("data:image/png;base64," + b64)).blob())));
  const W = bitmaps.reduce((w, b) => w + b.width, 0) + gap * (bitmaps.length - 1);
  const H = Math.max(...bitmaps.map((b) => b.height));
  const c = new OffscreenCanvas(W, H);
  const g = c.getContext("2d");
  g.fillStyle = "#808080";
  g.fillRect(0, 0, W, H);
  let x = 0;
  for (const b of bitmaps) { g.drawImage(b, x, 0); x += b.width + gap; }
  const bytes = new Uint8Array(await (await c.convertToBlob({type: "image/png"})).arrayBuffer());
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}"""


def side_by_side(page, *pngs: bytes, gap: int = 16) -> bytes:
    """The PNGs next to each other, left to right, on a grey ground (canvas, app, diff)."""
    got = page.evaluate(SIDE_BY_SIDE_JS, {"images": [base64.b64encode(p).decode() for p in pngs], "gap": gap})
    return base64.b64decode(got)
