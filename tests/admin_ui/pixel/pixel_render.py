"""Reference renders of the design canvas, made by our own renderer (dc-mini.js) in the same Chrome the E2E tests use.

A board is one of our `canvas/*.dc.html` files. `render` loads it into a page served entirely from local files (nothing
remote: the Google Fonts link is dropped, the fonts are the app's own @font-face files from the built dist, the
`/_blob/<id>` logos are the app's brand SVGs), runs the file's own `Component` through dc-mini.js with the theme chosen
by the `dark` prop, and takes a viewport screenshot at the board's width and height from `canvas.json`."""

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CANVAS = HERE / "canvas"
DIST = REPO / "packages" / "relay-core" / "src" / "relay_core" / "admin" / "static" / "app"
BRAND = REPO / "apps" / "admin-web" / "src" / "assets" / "brand"
HOST = "http://pixel.local"

# The three images the canvas files reference by blob id, and the app's own files they stand for.
BLOBS = {
    "d0a9f70a76dc022b6bc0d943aea30824": BRAND / "ghidorah-logo-light.svg",     # class logo-on-light, dark wordmark
    "4ddd6de2d45e1d36a76b14b677eae5e2": BRAND / "ghidorah-logo-dark.svg",      # class logo-on-dark, light wordmark
    "bed81bd12230e7593e4e83acf865b4df": BRAND / "ghidorah-symbol-purple.svg",
}
PREFIXES = ("B-", "T-", "P-")           # the boards of the chosen direction, desktop, tablet and phone
TWO_FRAMES_JS = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"


def boards() -> list[tuple[str, int, int]]:
    """(file name without .dc.html, width, height) of every board that is rendered, from canvas.json."""
    listed = json.loads((CANVAS / "canvas.json").read_text(encoding="utf-8"))["boards"]
    return sorted((name.removesuffix(".dc.html"), spec["w"], spec["h"]) for name, spec in listed.items()
                  if name.startswith(PREFIXES))


def font_css() -> str:
    """The @font-face rules of the app's built CSS, with every url() pointed at the local route for the dist assets.
    Empty when the build has none (the fonts then fall back to the system's)."""
    rules = []
    for sheet in sorted((DIST / "assets").glob("*.css")):
        for rule in re.findall(r"@font-face\s*\{[^}]*\}", sheet.read_text(encoding="utf-8")):
            rules.append(re.sub(r"url\(([^)]*)\)", lambda m: "url(%s/__app/assets/%s)" % (
                HOST, m.group(1).strip("\"' ").rsplit("/", 1)[-1]), rule))
    return "\n".join(rules)


def parts(source: str) -> dict:
    """The pieces of a canvas file: helmet styles, the template markup, the data-props and the component script."""
    root = re.search(r"<x-dc>(.*)</x-dc>", source, re.S)
    script = re.search(r"<script type=\"text/x-dc\" data-dc-script data-props='(.*?)'>(.*?)</script>", source, re.S)
    if not root or not script:
        raise ValueError("not a canvas file: no <x-dc> root or no data-dc-script")
    markup = root.group(1)
    helmet = re.search(r"<helmet>(.*?)</helmet>", markup, re.S)
    styles = "\n".join(re.findall(r"<style>(.*?)</style>", helmet.group(1), re.S)) if helmet else ""
    if helmet:
        markup = markup.replace(helmet.group(0), "")
    # <sc-for>/<sc-if> become <template>s: the HTML parser would otherwise move them out of a table
    markup = re.sub(r"<sc-(for|if)\b", r"<template data-sc-\1", markup)
    markup = re.sub(r"</sc-(for|if)>", "</template>", markup)
    return {"styles": styles, "markup": markup, "props": script.group(1), "script": script.group(2)}


def page_html(source: str) -> str:
    p = parts(source)
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><style>%s</style><style>%s</style></head>"
            "<body><template id=\"dc-src\">%s</template><div id=\"dc-root\"></div>"
            "<script id=\"dc-props\" type=\"application/json\">%s</script>"
            "<script id=\"dc-script\" type=\"text/plain\">%s</script><script src=\"/dc-mini.js\"></script>"
            "</body></html>" % (font_css(), p["styles"], p["markup"], p["props"], p["script"]))


class Renderer:
    """Renders canvas boards (and synthetic sources, for the renderer's own tests) in one page of `context`."""

    def __init__(self, context):
        self.context, self.page = context, context.new_page()
        self.sources: dict[str, str] = {}
        self.shots: dict[tuple, bytes] = {}         # (board, dark, width, height, region) -> PNG of `png`
        self.refused: list[str] = []            # every request that was not answered from a local file
        self.problems: list[str] = []
        context.route("**/*", self._route)
        self.page.on("pageerror", lambda e: self.problems.append("script error: %s" % e))
        self.page.on("console", lambda m: self.problems.append("console: %s" % m.text) if m.type == "error" else None)

    def _route(self, route):
        url = route.request.url
        if not url.startswith(HOST + "/"):
            self.refused.append(url)
            return route.abort()
        path = url[len(HOST):].split("?")[0]
        if path == "/favicon.ico":                  # Chrome asks for it on every page; there is none
            return route.fulfill(status=204, body="")
        if path == "/dc-mini.js":
            return route.fulfill(path=str(HERE / "dc-mini.js"), content_type="text/javascript")
        if path.startswith("/board/"):
            name = path.rsplit("/", 1)[-1]
            source = self.sources.get(name) or (CANVAS / (name + ".dc.html")).read_text(encoding="utf-8")
            return route.fulfill(body=page_html(source), content_type="text/html")
        if path.startswith("/_blob/") and path.rsplit("/", 1)[-1] in BLOBS:
            return route.fulfill(path=str(BLOBS[path.rsplit("/", 1)[-1]]), content_type="image/svg+xml")
        if path.startswith("/__app/assets/") and (DIST / "assets" / path.rsplit("/", 1)[-1]).is_file():
            return route.fulfill(path=str(DIST / "assets" / path.rsplit("/", 1)[-1]))
        self.refused.append(url)
        return route.fulfill(status=404, body="not found")

    def open(self, name: str, dark: bool, width: int, height: int, source: str | None = None) -> None:
        """Load a board and wait until it is drawn, with its fonts, and at rest."""
        if source is not None:
            self.sources[name] = source
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.emulate_media(color_scheme="dark" if dark else "light")     # the app's context does the same
        self.page.goto("%s/board/%s?dark=%d" % (HOST, name, int(dark)))
        self.page.wait_for_function("window.__dcReady === true")
        self.settle()

    def settle(self) -> None:
        self.page.evaluate("document.fonts.ready.then(() => true)")
        self.page.evaluate(TWO_FRAMES_JS)

    def png(self, name: str, dark: bool, width: int, height: int, region: tuple | None = None) -> bytes:
        """The reference render of board `name`: a viewport screenshot of width x height (or the region of it)."""
        self.open(name, dark, width, height)
        clip = dict(zip(("x", "y", "width", "height"), region, strict=True)) if region else None
        shot = self.page.screenshot(clip=clip, caret="hide", animations="disabled")
        self.shots[(name, dark, width, height, region)] = shot
        return shot

    def reference(self, name: str, dark: bool, width: int, height: int, region: tuple | None = None) -> bytes:
        """`png`, from the render already made in this run when there is one (a board is drawn once per theme)."""
        return self.shots.get((name, dark, width, height, region)) or self.png(name, dark, width, height, region)
