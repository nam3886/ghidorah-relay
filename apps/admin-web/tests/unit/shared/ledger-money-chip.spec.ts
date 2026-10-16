import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import LedgerList from "../../../src/components/shared/LedgerList.vue";
import MoneyCell from "../../../src/components/shared/MoneyCell.vue";
import StatusChip from "../../../src/components/shared/StatusChip.vue";

const PLANTED = "<img src=x onerror=alert(1)><b>x</b>";

describe("LedgerList", () => {
  const items = [
    { label: "Completed", value: 1284 },
    { label: "Failed", value: "3", tone: "bad" as const },
    { label: "Job", value: "3b9e1f0a", mono: true },
    { label: "Quiet", value: 0, tone: "neutral" as const },
  ];

  it("is a dl with one label and one value per item, numbers as text", () => {
    const w = mount(LedgerList, { props: { items } });
    expect(w.element.tagName).toBe("DL");
    expect(w.findAll("dt").map((n) => n.text())).toEqual(["Completed", "Failed", "Job", "Quiet"]);
    expect(w.findAll("dd").map((n) => n.text())).toEqual(["1284", "3", "3b9e1f0a", "0"]);
    expect(w.findAll(".ledger__leader")).toHaveLength(4);
    expect(w.findAll(".ledger__leader").every((n) => n.attributes("aria-hidden") === "true")).toBe(true);
  });

  it("one column by default, two when asked", () => {
    expect(mount(LedgerList, { props: { items } }).classes()).toContain("ledger--1");
    expect(mount(LedgerList, { props: { items, columns: 2 } }).classes()).toContain("ledger--2");
    expect(mount(LedgerList, { props: { items, columns: 2 } }).classes()).not.toContain("ledger--1");
  });

  it("a tone colours the value; neutral and none do not; mono is for the value only", () => {
    const dd = mount(LedgerList, { props: { items } }).findAll("dd");
    expect(dd[1]!.classes()).toContain("tone-bad");
    expect(dd[0]!.classes().some((c) => c.startsWith("tone-"))).toBe(false);
    expect(dd[3]!.classes().some((c) => c.startsWith("tone-"))).toBe(false);
    expect(dd[2]!.classes()).toContain("is-mono");
    expect(dd[0]!.classes()).not.toContain("is-mono");
  });

  it("renders planted markup in a label or a value as text", () => {
    const w = mount(LedgerList, { props: { items: [{ label: PLANTED, value: PLANTED }] } });
    expect(w.find("img").exists()).toBe(false);
    expect(w.find("b").exists()).toBe(false);
    expect(w.find("dt").text()).toBe(PLANTED);
    expect(w.find("dd").text()).toBe(PLANTED);
  });
});

describe("MoneyCell", () => {
  it("a refund shows the struck charge, the refund icon and the net", () => {
    const w = mount(MoneyCell, { props: { price: 1, net: 0 } });
    expect(w.attributes("data-refunded")).toBe("true");
    expect(w.find("s").text()).toBe("1");
    expect(w.find("[aria-label='refunded']").exists()).toBe(true);
    expect(w.find(".money__net").text()).toBe("0");
  });

  it("a part refund counts too (net below price)", () => {
    const w = mount(MoneyCell, { props: { price: 5, net: 2 } });
    expect(w.find("s").text()).toBe("5");
    expect(w.find(".money__net").text()).toBe("2");
  });

  it("a negative net is a refund even when the price is 0", () => {
    const w = mount(MoneyCell, { props: { price: 0, net: -1 } });
    expect(w.attributes("data-refunded")).toBe("true");
    expect(w.find("s").text()).toBe("0");
    expect(w.find(".money__net").text()).toBe("-1");
  });

  it("no refund when the net equals the price or exceeds it", () => {
    for (const [price, net] of [
      [2, 2],
      [1, 3],
      [0, 0],
    ] as const) {
      const w = mount(MoneyCell, { props: { price, net } });
      expect(w.attributes("data-refunded")).toBe("false");
      expect(w.find("s").exists()).toBe(false);
      expect(w.find("[aria-label='refunded']").exists()).toBe(false);
      expect(w.find(".money__net").text()).toBe(String(net));
    }
  });

  it("a net that is not known yet is a dash and never a refund", () => {
    for (const props of [
      { price: 2, net: null } as { price: number | null | undefined; net: number | null | undefined },
      { price: null, net: 0 },
      { price: undefined, net: undefined },
    ]) {
      const w = mount(MoneyCell, { props });
      expect(w.attributes("data-refunded")).toBe("false");
      expect(w.find("s").exists()).toBe(false);
    }
    expect(
      mount(MoneyCell, { props: { price: 2, net: null } })
        .find(".money__net")
        .text(),
    ).toBe("-");
  });
});

describe("StatusChip step and pill", () => {
  it("a step replaces the dot and shows beside the word", () => {
    const w = mount(StatusChip, { props: { status: "waiting", step: "2/4" } });
    expect(w.find(".step").text()).toBe("2/4");
    expect(w.find("i.dot").exists()).toBe(false);
    expect(w.text()).toBe("2/4waiting");
  });

  it("without a step it shows the dot and no step", () => {
    const w = mount(StatusChip, { props: { status: "waiting" } });
    expect(w.find(".step").exists()).toBe(false);
    expect(w.find("i.dot").exists()).toBe(true);
  });

  it("is a round tag", () => {
    expect(mount(StatusChip, { props: { status: "done" } }).classes()).toContain("is-round");
  });

  it("markup in the step is text", () => {
    const w = mount(StatusChip, { props: { status: "waiting", step: PLANTED } });
    expect(w.find("img").exists()).toBe(false);
    expect(w.find(".step").text()).toBe(PLANTED);
  });
});
