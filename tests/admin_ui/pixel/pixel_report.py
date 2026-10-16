"""Write the images and numbers of each comparison into a run folder, and the report over everything in it.

`<run>/<board>-<theme>.{ref,app,diff}.png` and `<board>-<theme>.json` per comparison; `report.md` and `report.html` list
every comparison, the worst first, with the three images side by side. Nothing here is committed: the folder is the
run's output (RELAY_SCREENSHOTS_DIR on the box, a temp folder otherwise)."""

import html
import json
from pathlib import Path


def status(total_percent: float, gate) -> str:
    """"report-only" while the entry has no gate, else whether the mismatch plus anti-aliasing is within it."""
    if gate is None:
        return "report-only"
    return "pass" if total_percent <= gate else "FAIL"


def write_result(folder: Path, board: str, theme: str, reference: bytes, app: bytes, diff, gate=None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (board, theme)
    (folder / (stem + ".ref.png")).write_bytes(reference)
    (folder / (stem + ".app.png")).write_bytes(app)
    (folder / (stem + ".diff.png")).write_bytes(diff.png)
    result = {"board": board, "theme": theme, "stem": stem, "gate": gate,
              "status": status(diff.total_percent, gate), **diff.summary()}
    (folder / (stem + ".json")).write_text(json.dumps(result, indent=1), encoding="utf-8")
    build_report(folder)
    return result


def results(folder: Path) -> list[dict]:
    found = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
    return sorted(found, key=lambda r: (-r["total_percent"], r["stem"]))


def build_report(folder: Path) -> None:
    rows = results(folder)
    md = ["# Pixel diff against the canvas", "",
          "Worst first by mismatch + anti-aliasing. `report-only` entries have no gate yet and cannot fail.", "",
          "| board | theme | status | gate % | mismatch % | anti-aliased % | mismatch px | anti-aliased px | reference "
          "| app |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    cards = []
    for r in rows:
        gate = "none" if r["gate"] is None else "%.3f" % r["gate"]
        md.append("| %s | %s | %s | %s | %.3f | %.3f | %d | %d | %dx%d | %dx%d |" % (
            r["board"], r["theme"], r["status"], gate, r["percent"], r["aa_percent"], r["mismatch"], r["aa"],
            *r["reference"], *r["app"]))
        cells = ", ".join("(%d,%d) %d px" % (c["x"], c["y"], c["mismatch"]) for c in r["top_cells"][:5]) or "none"
        cards.append(
            "<section><h2>%s, %s: %.3f %% + %.3f %% anti-aliased (%d px)</h2>"
            "<p>worst 64 px cells (x,y): %s</p><div class=row>"
            "<figure><figcaption>canvas</figcaption><img src=\"%s.ref.png\"></figure>"
            "<figure><figcaption>app</figcaption><img src=\"%s.app.png\"></figure>"
            "<figure><figcaption>diff</figcaption><img src=\"%s.diff.png\"></figure></div></section>" % (
                html.escape(r["board"]), r["theme"], r["percent"], r["aa_percent"], r["mismatch"], html.escape(cells),
                r["stem"], r["stem"], r["stem"]))
    (folder / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (folder / "report.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Pixel diff</title><style>body{font:14px system-ui;margin:24px}"
        ".row{display:flex;gap:12px;align-items:flex-start}figure{margin:0}img{max-width:32vw;border:1px solid #888}"
        "</style><h1>Pixel diff against the canvas</h1>%s" % "".join(cards), encoding="utf-8")
