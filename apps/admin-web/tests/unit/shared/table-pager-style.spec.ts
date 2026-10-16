import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

// happy-dom computes no layout, so the drawn values of the table and the pager are pinned in their style blocks: each
// rule below is a number or a token from specs/shared.md sections 6 and 7.
const TABLE = readFileSync(join(process.cwd(), "src/components/shared/DataTable.vue"), "utf8");
const PAGER = readFileSync(join(process.cwd(), "src/components/shared/ListPager.vue"), "utf8");

// the declarations of the first rule whose selector contains `selector`
function rule(css: string, selector: string): string {
  const at = css.indexOf(selector);
  if (at < 0) throw new Error(`no rule for ${selector}`);
  const open = css.indexOf("{", at);
  return css.slice(open + 1, css.indexOf("}", open));
}

describe("table: the drawn values", () => {
  it("a header cell is 38 high, 12 px / 500, muted on the raised fill; a body cell is 56 high with a hairline", () => {
    const th = rule(TABLE, ":deep(.el-table th.el-table__cell) {");
    expect(th).toContain("height: 38px");
    expect(th).toContain("font-size: 12px");
    expect(th).toContain("font-weight: 500");
    expect(th).toContain("color: var(--rl-muted)");
    expect(th).toContain("background: var(--rl-raised)");
    const td = rule(TABLE, ":deep(.el-table td.el-table__cell) {");
    expect(td).toContain("height: 56px");
    expect(td).toContain("border-bottom: 1px solid var(--rl-line)");
  });

  it("cells are padded 0 12 (0 8 on a tablet); the first is 16 from the edge and so is the last", () => {
    expect(rule(TABLE, ":deep(.el-table .cell) {")).toContain("padding: 0 12px");
    expect(rule(TABLE, ".is-tablet :deep(.el-table .cell) {")).toContain("padding: 0 8px");
    expect(rule(TABLE, ":deep(.el-table .el-table__cell:first-child .cell) {")).toContain("padding-left: 16px");
    expect(rule(TABLE, ":deep(.el-table .el-table__cell:last-child .cell) {")).toContain("padding-right: 16px");
  });

  it("only a failed row is tinted: the fill of an unknown, running or done row is empty", () => {
    expect(rule(TABLE, ":deep(.el-table .row-tone-bad),")).toContain("--row-tone-fill: var(--rl-bad-bg)");
    for (const tone of ["warn", "info", "ok"]) {
      expect(rule(TABLE, `:deep(.el-table .row-tone-${tone}),`)).toContain("--row-tone-fill: transparent");
    }
  });

  it("a selected row is the soft primary and a hovered row is the raised fill", () => {
    expect(rule(TABLE, ":deep(.el-table .is-selected > td.el-table__cell) {")).toContain("var(--rl-primary-soft)");
    expect(rule(TABLE, ":deep(.el-table .el-table__row:hover > td.el-table__cell) {")).toContain("var(--rl-raised)");
  });

  it("a day row is 12 px / 600 muted, padded 14 16 6; the empty state is padded 44 16 52", () => {
    expect(rule(TABLE, ".data-table__day-label {")).toContain("font-weight: 600");
    expect(rule(TABLE, ".data-table__day {")).toContain("padding: 14px 16px 6px");
    expect(rule(TABLE, ".data-table__empty {")).toContain("padding: 44px 16px 52px");
  });

  it("the skeleton draws flat 56 px rows with no shimmer", () => {
    expect(rule(TABLE, ".data-table__skeleton td {")).toContain("height: 56px");
    expect(TABLE).not.toContain("animation");
    expect(rule(TABLE, ".skel--time {")).toContain("width: 52px");
  });
});

describe("pager: the drawn values", () => {
  it("a page button is 32 high with radius 10; the current one has the primary fill and white 600 text", () => {
    const step = rule(PAGER, ".pager__step,");
    expect(step).toContain("height: 32px");
    expect(step).toContain("border-radius: 10px");
    const active = rule(PAGER, ".pager__page.is-active {");
    expect(active).toContain("background: var(--rl-primary)");
    expect(active).toContain("color: #ffffff");
    expect(active).toContain("font-weight: 600");
    expect(rule(PAGER, ".pager__page.is-wide {")).toContain("min-width: 40px");
  });

  it("a disabled arrow is faint; the box is padded 14 16 with a hairline on top; a phone is 8 16 with 44 px arrows", () => {
    expect(rule(PAGER, ".pager__step.is-disabled {")).toContain("color: var(--rl-faint)");
    const box = rule(PAGER, ".pager {");
    expect(box).toContain("padding: 14px 16px");
    expect(box).toContain("border-top: 1px solid var(--rl-line)");
    expect(rule(PAGER, '.pager[data-phone="true"] {')).toContain("padding: 8px 16px");
    const arrow = rule(PAGER, '.pager[data-phone="true"] .pager__step {');
    expect(arrow).toContain("min-width: 88px");
    expect(arrow).toContain("height: 44px");
  });
});

describe("the Jobs band ledger", () => {
  // The band variant of LedgerList draws the panel, its padding (16 20, phone 14 16) and its rows. A wrapper with a
  // padding of its own doubled the inset on every Jobs board (T-Jobs showed it as +1 point of mismatch).
  const BAND = readFileSync(join(process.cwd(), "src/components/jobs/HourBand.vue"), "utf8");

  it("is the shared LedgerList itself: nothing wraps it and no rule of the band pads or fills it again", () => {
    expect(BAND).toContain('<LedgerList :items="ledger" :columns="isTablet ? 2 : 1" />');
    expect(BAND).not.toContain("band__ledger");
  });
});
