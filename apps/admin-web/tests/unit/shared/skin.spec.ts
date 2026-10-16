import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// happy-dom computes no layout, so the drawn values of the shared skin are pinned in the sheet itself: each rule below
// is a number or a token from specs/shared.md, and the test fails when the sheet drifts from it.
const SKIN = readFileSync(join(process.cwd(), "src/components/shared/skin.css"), "utf8");
const CONFIRM = readFileSync(join(process.cwd(), "src/components/shared/confirm-dialog.css"), "utf8");
const TOAST = readFileSync(join(process.cwd(), "src/lib/notify.css"), "utf8");

// the declarations of the first rule whose selector contains `selector`
function rule(css: string, selector: string): string {
  const at = css.indexOf(selector);
  if (at < 0) throw new Error(`no rule for ${selector}`);
  const open = css.indexOf("{", at);
  return css.slice(open + 1, css.indexOf("}", open));
}

// the declarations of the rule that holds `needle` (a selector list can be long and is reformatted by Prettier)
function block(css: string, needle: string): string {
  const at = css.indexOf(needle);
  if (at < 0) throw new Error(`no declaration ${needle}`);
  return css.slice(css.lastIndexOf("{", at) + 1, css.indexOf("}", at));
}

describe("skin: buttons", () => {
  it("every button is 13 px / 400 with radius 10 and a 14 px pad; a primary one is 16 px and 600", () => {
    const base = rule(SKIN, "html:root .el-button:not(.el-button--small, .el-button--large) {");
    expect(base).toContain("padding: 0 14px");
    expect(base).toContain("border-radius: 10px");
    expect(base).toContain("font-size: 13px");
    expect(base).toContain("font-weight: 400");
    const primary = rule(SKIN, "html:root .el-button--primary:not(.is-plain, .is-text, .is-link) {");
    expect(primary).toContain("--el-button-bg-color: var(--rl-primary)");
    expect(primary).toContain("--el-button-text-color: #ffffff");
    expect(primary).toContain("padding: 0 16px");
    expect(primary).toContain("font-weight: 600");
  });

  it("a secondary button is the surface with a strong hairline and a raised hover", () => {
    const secondary = block(SKIN, "--el-button-border-color: var(--rl-line-strong);");
    expect(secondary).toContain("--el-button-bg-color: var(--rl-surface)");
    expect(secondary).toContain("--el-button-border-color: var(--rl-line-strong)");
    expect(secondary).toContain("--el-button-hover-bg-color: var(--rl-raised)");
    expect(secondary).toContain("--el-button-text-color: var(--rl-fg)");
  });

  it("the danger outline draws no hover: its hover colours are its resting colours", () => {
    const danger = rule(SKIN, "html:root .el-button--danger.is-plain {");
    expect(danger).toContain("--el-button-border-color: var(--rl-bad)");
    expect(danger).toContain("--el-button-bg-color: transparent");
    expect(danger).toContain("--el-button-hover-bg-color: transparent");
    expect(danger).toContain("--el-button-hover-text-color: var(--rl-bad)");
  });

  it("a disabled primary is the purple at 45 % with a not-allowed cursor; a disabled secondary is raised, faint, plain", () => {
    const primary = block(SKIN, "--el-button-disabled-bg-color: var(--rl-primary);");
    expect(primary).toContain("--el-button-disabled-bg-color: var(--rl-primary)");
    expect(primary).toContain("opacity: 0.45");
    expect(primary).toContain("cursor: not-allowed");
    const secondary = block(SKIN, "--el-button-disabled-text-color: var(--rl-faint);");
    expect(secondary).toContain("--el-button-disabled-bg-color: var(--rl-raised)");
    expect(secondary).toContain("--el-button-disabled-text-color: var(--rl-faint)");
    expect(secondary).toContain("--el-button-disabled-border-color: var(--rl-line-strong)");
    expect(secondary).toContain("cursor: default");
  });
});

describe("skin: checkbox", () => {
  it("is the native-look box: 15 px, radius 2, the primary fill; 20 px on a phone", () => {
    const box = rule(SKIN, "html:root .el-checkbox__inner {");
    expect(box).toContain("width: 15px");
    expect(box).toContain("height: 15px");
    expect(box).toContain("border-radius: 2px");
    const vars = rule(SKIN, "html:root .el-checkbox {");
    expect(vars).toContain("--el-checkbox-checked-bg-color: var(--rl-primary)");
    expect(vars).toContain("--el-checkbox-checked-input-border-color: var(--rl-primary)");
    const phone = SKIN.slice(SKIN.indexOf("@media (max-width: 767.98px) {\n  html:root .el-checkbox {"));
    expect(phone).toContain("width: 20px");
    expect(phone).toContain("height: 20px");
  });

  it("an input border is the strong hairline, never --rl-control", () => {
    expect(rule(SKIN, "html:root .el-input,")).toContain("--el-input-border-color: var(--rl-line-strong)");
    expect(SKIN).not.toContain("--rl-control");
  });
});

describe("skin: menu and tooltip", () => {
  it("the menu is a 12 px radius surface with a hairline; an item is 36 high, radius 8, 13 px", () => {
    const popper = rule(SKIN, "html:root .el-popper.rl-menu {");
    expect(popper).toContain("border: 1px solid var(--rl-line)");
    expect(popper).toContain("border-radius: 12px");
    const list = rule(SKIN, "html:root .el-popper.rl-menu .el-dropdown-menu {");
    expect(list).toContain("min-width: 200px");
    expect(list).toContain("padding: 6px");
    const item = rule(SKIN, "html:root .el-popper.rl-menu .el-dropdown-menu__item {");
    expect(item).toContain("height: 36px");
    expect(item).toContain("border-radius: 8px");
    expect(item).toContain("font-size: 13px");
  });

  it("a danger item is red with a red wash on hover", () => {
    expect(rule(SKIN, "html:root .el-popper.rl-menu .el-dropdown-menu__item.is-danger,")).toContain(
      "color: var(--rl-bad)",
    );
    expect(
      rule(SKIN, "html:root .el-popper.rl-menu .el-dropdown-menu__item.is-danger:not(.is-disabled):hover,"),
    ).toContain("background: var(--rl-bad-bg)");
  });

  it("the tooltip is inverted: the foreground colour as its fill and the surface colour as its text", () => {
    const tip = rule(SKIN, "html:root .el-popper.is-dark {");
    expect(tip).toContain("background: var(--rl-fg)");
    expect(tip).toContain("color: var(--rl-surface)");
    expect(tip).toContain("border-radius: 8px");
  });
});

describe("confirm and toasts", () => {
  it("the confirm box is 440 wide, padded 20 20 16, radius 16, with a hairline and a 20/28 title", () => {
    const box = rule(CONFIRM, ".rl-confirm.el-message-box {");
    expect(box).toContain("width: min(440px, 100%)");
    expect(box).toContain("padding: 20px 20px 16px");
    expect(box).toContain("border-radius: 16px");
    expect(box).toContain("border: 1px solid var(--rl-line)");
    const title = rule(CONFIRM, ".rl-confirm .el-message-box__title {");
    expect(title).toContain("font-size: 20px");
    expect(title).toContain("line-height: 28px");
    expect(rule(CONFIRM, ".rl-confirm .el-message-box__btns {")).toContain("justify-content: flex-end");
    expect(rule(CONFIRM, ".rl-confirm .el-message-box__headerbtn,")).toContain("display: none");
  });

  it("a toast is 260 wide, padded 12 16, radius 12, on the surface with a plain hairline in every tone", () => {
    const toast = rule(TOAST, ".el-message {");
    expect(toast).toContain("min-width: 260px");
    expect(toast).toContain("padding: 12px 16px");
    expect(toast).toContain("border-radius: 12px");
    expect(rule(TOAST, "html:root .el-message {")).toContain("border-color: var(--rl-line)");
    expect(rule(TOAST, "html:root .el-message {")).toContain("background-color: var(--rl-surface)");
    expect(rule(TOAST, ".el-message.is-closable {")).toContain("padding: 12px 8px 12px 16px");
  });

  it("the close button is 28 square with radius 8; the grouping badge is a 20 high pill inside the toast", () => {
    const close = rule(TOAST, ".el-message .el-message__closeBtn {");
    expect(close).toContain("width: 28px");
    expect(close).toContain("height: 28px");
    expect(close).toContain("border-radius: 8px");
    const badge = rule(TOAST, ".el-message .el-message__badge {");
    expect(badge).toContain("position: static");
    expect(badge).toContain("height: 20px");
    expect(badge).toContain("background-color: var(--rl-ok-bg)");
  });
});

// a rule's declarations as a plain object, so a test can demand exactly these and no others
function decls(css: string, selector: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of rule(css, selector).split(";")) {
    const at = line.indexOf(":");
    if (at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

const LEDGER = readFileSync(join(process.cwd(), "src/components/shared/LedgerList.vue"), "utf8");

describe("confirm position and ledger line height", () => {
  it("the confirm box sits 240 px from the top (30vh on a short page), aligned to the top of its line", () => {
    const box = decls(CONFIRM, ".rl-confirm.el-message-box {");
    expect(box["vertical-align"]).toBe("top");
    expect(box["margin-top"]).toBe("min(240px, 30vh)");
  });

  it("a ledger line is 20 px high, in the one rule that sets it", () => {
    expect(decls(LEDGER, ".ledger {")["line-height"]).toBe("20px");
    expect(LEDGER.match(/line-height:/g)).toHaveLength(1);
  });
});

describe("the HintTip note", () => {
  it("is the menu surface: surface fill, a hairline, radius 12, the overlay shadow, 12/16 type, at most 320 wide", () => {
    expect(decls(SKIN, "html:root .el-popper.rl-tip {")).toEqual({
      "max-width": "320px",
      padding: "8px 12px",
      border: "1px solid var(--rl-line)",
      "border-radius": "12px",
      background: "var(--rl-surface)",
      color: "var(--rl-fg)",
      "font-size": "12px",
      "line-height": "16px",
      "box-shadow": "var(--rl-shadow-pop, 0 12px 32px rgb(10 6 20 / 18%))",
    });
  });
});

describe("skin: exact declarations", () => {
  it("the checkbox box is exactly 15 x 15 with radius 2, and no other size sneaks in", () => {
    expect(decls(SKIN, "html:root .el-checkbox__inner {")).toEqual({
      width: "15px",
      height: "15px",
      "border-radius": "2px",
    });
  });

  it("the tooltip is exactly the inverted pill and has no border", () => {
    expect(decls(SKIN, "html:root .el-popper.is-dark {")).toEqual({
      padding: "6px 10px",
      border: "0",
      "border-radius": "8px",
      background: "var(--rl-fg)",
      color: "var(--rl-surface)",
      "font-size": "12px",
      "line-height": "16px",
      "box-shadow": "var(--rl-shadow-pop, 0 12px 32px rgb(10 6 20 / 18%))",
    });
  });

  it("the menu surface and its list are exactly the drawn box", () => {
    expect(decls(SKIN, "html:root .el-popper.rl-menu {")).toEqual({
      padding: "0",
      border: "1px solid var(--rl-line)",
      "border-radius": "12px",
      background: "var(--rl-surface)",
      "box-shadow": "var(--rl-shadow-pop, 0 12px 32px rgb(10 6 20 / 18%))",
    });
    const list = decls(SKIN, "html:root .el-popper.rl-menu .el-dropdown-menu {");
    expect(list["min-width"]).toBe("200px");
    expect(list.padding).toBe("6px");
  });

  it("the card is the surface with a hairline and radius 16", () => {
    expect(decls(SKIN, ".rl-card {")).toEqual({
      overflow: "hidden",
      border: "1px solid var(--rl-line)",
      "border-radius": "16px",
      background: "var(--rl-surface)",
    });
  });

  it("the danger outline's resting and hover colours are the same, so no hover change is drawn", () => {
    const d = decls(SKIN, "html:root .el-button--danger.is-plain {");
    expect(d["--el-button-hover-border-color"]).toBe(d["--el-button-border-color"]);
    expect(d["--el-button-hover-text-color"]).toBe(d["--el-button-text-color"]);
    expect(d["--el-button-hover-bg-color"]).toBe(d["--el-button-bg-color"]);
    expect(d["--el-button-active-bg-color"]).toBe(d["--el-button-bg-color"]);
  });

  it("a secondary button never takes the primary fill", () => {
    const secondary = decls(SKIN, "--el-button-border-color: var(--rl-line-strong);");
    expect(Object.values(secondary).some((v) => v.includes("--rl-primary"))).toBe(false);
  });

  it("the confirm box and the toast hold exactly their drawn size, with no second width", () => {
    const box = decls(CONFIRM, ".rl-confirm.el-message-box {");
    expect(box.width).toBe("min(440px, 100%)");
    expect(box["border-radius"]).toBe("16px");
    expect(CONFIRM.match(/width: min\(/g)).toHaveLength(1);
    expect(decls(TOAST, ".el-message {")["min-width"]).toBe("260px");
    expect(TOAST.match(/min-width:/g)).toHaveLength(1);
  });

  it("no rule in the skin reads the control token or hard-codes a purple", () => {
    for (const css of [SKIN, CONFIRM, TOAST]) {
      expect(css).not.toContain("--rl-control");
      expect(css).not.toMatch(/#7c3aed/i);
    }
  });
});
