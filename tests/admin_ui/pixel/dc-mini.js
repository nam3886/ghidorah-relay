// A mini renderer for the dialect of our canvas files (*.dc.html). Written for this repo; it is not the design tool's
// runtime. It runs inside the page that pixel_render.py builds:
//   #dc-props   JSON of the file's data-props (each prop {default: ...}); ?dark=1|0 in the URL overrides `dark`
//   #dc-script  the source of the file's `class Component extends DCLogic`
//   #dc-src     <template> holding the markup of <x-dc>, with <sc-for>/<sc-if> already turned into
//               <template data-sc-for list=".." as=".."> / <template data-sc-if value="..">
//               (so the HTML parser keeps them in place inside tables)
//   #dc-root    where the result is drawn
// The dialect: {{path}} in text and attributes (a path into renderVals() and the loop variables, no expressions),
// sc-for, sc-if, and onClick/onChange/onSubmit="{{fn}}". A single {{path}} attribute whose value is null, undefined or
// false is left out; a function in an on* attribute becomes an event listener. setState() draws everything again.
(() => {
  "use strict";
  const TOKEN = /\{\{\s*([^}]*?)\s*\}\}/g;
  const WHOLE = /^\s*\{\{\s*([^}]*?)\s*\}\}\s*$/;
  const BOOLEAN_ATTRS = new Set(["disabled", "checked", "selected", "hidden", "readonly", "open", "required"]);

  class DCLogic {
    constructor(props) {
      this.props = props;
      this.state = {};
    }

    setState(patch) {
      const next = typeof patch === "function" ? patch(this.state, this.props) : patch;
      this.state = Object.assign({}, this.state, next);
      draw();
    }
  }

  const specs = JSON.parse(document.getElementById("dc-props").textContent);
  const props = {};
  for (const [name, spec] of Object.entries(specs)) {
    if (!name.startsWith("$") && spec && typeof spec === "object" && "default" in spec) props[name] = spec.default;
  }
  const query = new URLSearchParams(location.search);
  if (query.has("dark")) props.dark = query.get("dark") === "1";

  const Component = new Function("DCLogic", document.getElementById("dc-script").textContent + "\nreturn Component;")(
    DCLogic);
  const instance = new Component(props);
  const source = document.getElementById("dc-src").content;
  const root = document.getElementById("dc-root");

  function lookup(path, scope) {
    if (path === "true") return true;
    if (path === "false") return false;
    if (path === "null") return null;
    const parts = path.split(".");
    let value = scope[parts[0]];
    for (let i = 1; i < parts.length; i++) value = value == null ? undefined : value[parts[i]];
    return value;
  }

  function text(raw, scope) {
    return raw.replace(TOKEN, (_, path) => {
      const value = lookup(path, scope);
      if (value === undefined) (window.__dcUndefined = window.__dcUndefined || []).push(path);   // a typo, not data
      return value == null || typeof value === "boolean" ? "" : String(value);
    });
  }

  function attribute(el, name, raw, scope) {
    if (/^on[a-z]+$/i.test(name)) {
      const whole = WHOLE.exec(raw);
      const fn = whole && lookup(whole[1], scope);
      if (typeof fn === "function") el.addEventListener(name.slice(2).toLowerCase(), (event) => fn(event));
      return;
    }
    const whole = WHOLE.exec(raw);
    if (whole) {
      const value = lookup(whole[1], scope);
      if (value == null || value === false) return;
      el.setAttribute(name, value === true && BOOLEAN_ATTRS.has(name) ? "" : String(value));
      return;
    }
    el.setAttribute(name, text(raw, scope));
  }

  function children(node, scope, into) {
    for (const child of node.childNodes) build(child, scope, into);
  }

  function build(node, scope, into) {
    if (node.nodeType === Node.TEXT_NODE) {
      into.appendChild(document.createTextNode(text(node.nodeValue, scope)));
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.hasAttribute("data-sc-if")) {
      if (lookup(WHOLE.exec(node.getAttribute("value"))[1], scope)) children(node.content, scope, into);
      return;
    }
    if (node.hasAttribute("data-sc-for")) {
      const list = lookup(WHOLE.exec(node.getAttribute("list"))[1], scope) || [];
      const name = node.getAttribute("as");
      list.forEach((item) => {
        const inner = Object.create(scope);
        inner[name] = item;
        children(node.content, inner, into);
      });
      return;
    }
    const el = document.createElementNS(node.namespaceURI, node.localName);
    for (const attr of node.attributes) attribute(el, attr.name, attr.value, scope);
    children(node.localName === "template" ? node.content : node, scope, el);
    into.appendChild(el);
  }

  function draw() {
    const scope = instance.renderVals();
    window.__dcUndefined = [];
    root.replaceChildren();
    children(source, scope, root);
    window.__dcDraws = (window.__dcDraws || 0) + 1;
    window.__dcReady = true;
  }

  draw();
})();
