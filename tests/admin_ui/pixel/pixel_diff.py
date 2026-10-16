"""A pixel diff of two PNGs, computed in the browser (a canvas; the test image has no Pillow or numpy).

The comparison is the one `pixelmatch` makes: two pixels differ when their YIQ colour distance is over a threshold
(`tolerance`, 0.1 by default), and a differing pixel that is only anti-aliasing (an edge pixel in either image, with
darker and lighter neighbours that have many equal siblings) is counted apart as `aa`, not as a mismatch. The images may
differ in size: the canvas is the larger of the two, and every pixel that lies outside one of them is a mismatch.

The result carries the counts, the mismatch percentage of the larger canvas, the 64 px cells with the most mismatches
(where to look first) and a diff image: matching pixels faded, anti-aliasing yellow, mismatches red."""

import base64
from dataclasses import dataclass, field

CELL = 64

DIFF_JS = r"""async ({a, b, tolerance, cell}) => {
  const decode = async (b64) => {
    const blob = await (await fetch("data:image/png;base64," + b64)).blob();
    return createImageBitmap(blob);
  };
  const [ia, ib] = [await decode(a), await decode(b)];
  const W = Math.max(ia.width, ib.width), H = Math.max(ia.height, ib.height);
  const pixels = (bitmap) => {
    const c = new OffscreenCanvas(W, H);
    const g = c.getContext("2d", {willReadFrequently: true});
    g.drawImage(bitmap, 0, 0);
    return g.getImageData(0, 0, W, H).data;
  };
  const A = pixels(ia), B = pixels(ib);
  const inside = (bitmap, x, y) => x < bitmap.width && y < bitmap.height;
  const blend = (c, a) => 255 + (c - 255) * a;
  const yiq = (img, k) => {
    let r = img[k], g = img[k + 1], b = img[k + 2];
    const al = img[k + 3] / 255;
    if (al < 1) { r = blend(r, al); g = blend(g, al); b = blend(b, al); }
    return [0.29889531 * r + 0.58662247 * g + 0.11448223 * b,
            0.59597799 * r - 0.2741761 * g - 0.32180189 * b,
            0.21147017 * r - 0.52261711 * g + 0.31114694 * b];
  };
  const delta = (i1, i2, k, m, yOnly) => {
    const [y1, iq1, q1] = yiq(i1, k), [y2, iq2, q2] = yiq(i2, m);
    const y = y1 - y2;
    if (yOnly) return y;
    const i = iq1 - iq2, q = q1 - q2;
    const d = 0.5053 * y * y + 0.299 * i * i + 0.1957 * q * q;
    return y1 > y2 ? -d : d;
  };
  const same = (i1, i2, k, m) => i1[k] === i2[m] && i1[k + 1] === i2[m + 1] && i1[k + 2] === i2[m + 2] &&
                                 i1[k + 3] === i2[m + 3];
  const siblings = (img, x, y) => {
    const x0 = Math.max(x - 1, 0), y0 = Math.max(y - 1, 0), x2 = Math.min(x + 1, W - 1), y2 = Math.min(y + 1, H - 1);
    let n = x === x0 || x === x2 || y === y0 || y === y2 ? 1 : 0;
    const k = (y * W + x) * 4;
    for (let xx = x0; xx <= x2; xx++) for (let yy = y0; yy <= y2; yy++) {
      if (xx === x && yy === y) continue;
      if (same(img, img, k, (yy * W + xx) * 4)) { n++; if (n > 2) return true; }
    }
    return false;
  };
  const antialiased = (img, x, y, other) => {
    const x0 = Math.max(x - 1, 0), y0 = Math.max(y - 1, 0), x2 = Math.min(x + 1, W - 1), y2 = Math.min(y + 1, H - 1);
    let zeroes = x === x0 || x === x2 || y === y0 || y === y2 ? 1 : 0;
    let min = 0, max = 0, minX = 0, minY = 0, maxX = 0, maxY = 0;
    const k = (y * W + x) * 4;
    for (let xx = x0; xx <= x2; xx++) for (let yy = y0; yy <= y2; yy++) {
      if (xx === x && yy === y) continue;
      const d = delta(img, img, k, (yy * W + xx) * 4, true);
      if (d === 0) { zeroes++; if (zeroes > 2) return false; }
      else if (d < min) { min = d; minX = xx; minY = yy; }
      else if (d > max) { max = d; maxX = xx; maxY = yy; }
    }
    if (min === 0 || max === 0) return false;
    return (siblings(img, minX, minY) && siblings(img, maxX, maxY)) ||
           (siblings(other, minX, minY) && siblings(other, maxX, maxY));
  };
  const maxDelta = 35215 * tolerance * tolerance;
  const out = new OffscreenCanvas(W, H);
  const og = out.getContext("2d");
  const outData = og.createImageData(W, H);
  const o = outData.data;
  const cols = Math.ceil(W / cell), rows = Math.ceil(H / cell);
  const cells = new Array(cols * rows).fill(0);
  let mismatch = 0, aa = 0;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const k = (y * W + x) * 4;
    let kind = 0;                                  // 0 same, 1 anti-aliasing, 2 mismatch
    if (!inside(ia, x, y) || !inside(ib, x, y)) kind = 2;
    else if (!same(A, B, k, k)) {
      const d = delta(A, B, k, k, false);
      if (Math.abs(d) > maxDelta) kind = antialiased(A, x, y, B) || antialiased(B, x, y, A) ? 1 : 2;
    }
    if (kind === 2) {
      mismatch++;
      cells[Math.floor(y / cell) * cols + Math.floor(x / cell)]++;
      o[k] = 255; o[k + 1] = 0; o[k + 2] = 60; o[k + 3] = 255;
    }
    else if (kind === 1) { aa++; o[k] = 255; o[k + 1] = 214; o[k + 2] = 0; o[k + 3] = 255; }
    else if (inside(ia, x, y)) {
      const gray = 0.299 * A[k] + 0.587 * A[k + 1] + 0.114 * A[k + 2];
      const faded = 255 + (gray - 255) * 0.12;
      o[k] = o[k + 1] = o[k + 2] = faded; o[k + 3] = 255;
    }
  }
  og.putImageData(outData, 0, 0);
  const blob = await out.convertToBlob({type: "image/png"});
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  const top = cells.map((n, i) => ({x: (i % cols) * cell, y: Math.floor(i / cols) * cell, mismatch: n}))
    .filter((c) => c.mismatch > 0).sort((p, q) => q.mismatch - p.mismatch).slice(0, 8);
  return {width: W, height: H, a: [ia.width, ia.height], b: [ib.width, ib.height], mismatch, aa, top,
          diff: btoa(s)};
}"""


@dataclass
class Diff:
    width: int
    height: int
    reference_size: tuple
    app_size: tuple
    mismatch: int
    aa: int
    top: list = field(default_factory=list)
    png: bytes = b""

    @property
    def percent(self) -> float:
        return 100.0 * self.mismatch / (self.width * self.height)

    @property
    def aa_percent(self) -> float:
        return 100.0 * self.aa / (self.width * self.height)

    @property
    def total_percent(self) -> float:
        """Mismatch plus anti-aliasing: a one-pixel shift of text or a border is mostly anti-aliasing, so a gate that
        looked at the mismatch alone could let it through."""
        return self.percent + self.aa_percent

    @property
    def same_size(self) -> bool:
        return self.reference_size == self.app_size

    def summary(self) -> dict:
        return {"mismatch": self.mismatch, "aa": self.aa, "percent": round(self.percent, 4),
                "aa_percent": round(self.aa_percent, 4), "total_percent": round(self.total_percent, 4),
                "canvas": [self.width, self.height], "reference": list(self.reference_size),
                "app": list(self.app_size), "top_cells": self.top}


def compare(page, reference: bytes, app: bytes, tolerance: float = 0.1) -> Diff:
    """Diff two PNGs in `page` (any page: it is only used to run a script)."""
    got = page.evaluate(DIFF_JS, {"a": base64.b64encode(reference).decode(), "b": base64.b64encode(app).decode(),
                                  "tolerance": tolerance, "cell": CELL})
    return Diff(width=got["width"], height=got["height"], reference_size=tuple(got["a"]), app_size=tuple(got["b"]),
                mismatch=got["mismatch"], aa=got["aa"], top=got["top"], png=base64.b64decode(got["diff"]))
