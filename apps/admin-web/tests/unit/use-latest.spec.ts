import { describe, expect, it } from "vitest";
import { useLatest } from "@/composables/use-latest";

describe("useLatest", () => {
  it("only the newest ticket is current", () => {
    const latest = useLatest();
    const first = latest.next();
    expect(latest.isCurrent(first)).toBe(true);
    const second = latest.next();
    expect(second).toBe(first + 1);
    expect(latest.isCurrent(first)).toBe(false);
    expect(latest.isCurrent(second)).toBe(true);
  });

  it("invalidate makes every ticket so far stale without taking a new one", () => {
    const latest = useLatest();
    const ticket = latest.next();
    latest.invalidate();
    expect(latest.isCurrent(ticket)).toBe(false);
  });

  it("keeps separate counters per instance", () => {
    const a = useLatest();
    const b = useLatest();
    const ticketA = a.next();
    b.next();
    b.next();
    expect(a.isCurrent(ticketA)).toBe(true);
  });

  it("drops a slow first answer that arrives after the second", async () => {
    const latest = useLatest();
    let shown = "";
    const load = async (value: string, delayMs: number) => {
      const ticket = latest.next();
      await new Promise((resolve) => setTimeout(resolve, delayMs));
      if (!latest.isCurrent(ticket)) return;
      shown = value;
    };
    await Promise.all([load("slow first", 20), load("fast second", 1)]);
    expect(shown).toBe("fast second");
  });
});
