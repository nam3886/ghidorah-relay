import { describe, expect, it } from "vitest";
import { createCursorStack } from "@/components/jobs/cursor-stack";

// A stand-in server with three pages of two jobs: the first has no cursor, each page answers with the next one.
const PAGES: Record<string, { items: string[]; next: string | null }> = {
  start: { items: ["j1", "j2"], next: "c-2" },
  "c-2": { items: ["j3", "j4"], next: "c-3" },
  "c-3": { items: ["j5"], next: null },
};

// What the Jobs view does: load page `at` with the cursor the stack holds for it, then report what came back.
function viewOver(stack: ReturnType<typeof createCursorStack>) {
  const asked: (string | null)[] = [];
  const load = (at: number): string[] => {
    const cursor = stack.cursorAt(at);
    asked.push(cursor);
    const page = PAGES[cursor ?? "start"]!;
    stack.arrive(at, page.next);
    return page.items;
  };
  return { asked, load };
}

describe("cursor stack", () => {
  it("starts on page 1 with no cursor and nothing after it", () => {
    const stack = createCursorStack();
    expect(stack.page.value).toBe(1);
    expect(stack.cursorAt(0)).toBeNull();
    expect(stack.hasNext.value).toBe(false);
    expect(stack.prevIndex()).toBe(0);
    expect(stack.nextIndex()).toBe(1);
  });

  it("remembers where the next page starts once a page arrives with a cursor", () => {
    const stack = createCursorStack();
    stack.arrive(0, "c-2");
    expect(stack.hasNext.value).toBe(true);
    expect(stack.cursorAt(1)).toBe("c-2");
    expect(stack.page.value).toBe(1);
  });

  it("forward and back give the same pages, asking for the same cursors", () => {
    const stack = createCursorStack();
    const { asked, load } = viewOver(stack);
    const seen = [load(0)];
    seen.push(load(stack.nextIndex()));
    seen.push(load(stack.nextIndex()));
    expect(stack.page.value).toBe(3);
    expect(stack.hasNext.value).toBe(false);
    seen.push(load(stack.prevIndex()));
    seen.push(load(stack.prevIndex()));
    expect(stack.page.value).toBe(1);
    seen.push(load(stack.nextIndex()));
    expect(seen).toEqual([["j1", "j2"], ["j3", "j4"], ["j5"], ["j3", "j4"], ["j1", "j2"], ["j3", "j4"]]);
    expect(asked).toEqual([null, "c-2", "c-3", "c-2", null, "c-2"]);
  });

  it("Prev never goes below the first page", () => {
    const stack = createCursorStack();
    expect(stack.prevIndex()).toBe(0);
    stack.arrive(0, "c-2");
    stack.arrive(1, null);
    expect(stack.prevIndex()).toBe(0);
    expect(stack.nextIndex()).toBe(2);
  });

  it("a reload of an earlier page cuts the pages after it and takes its new cursor", () => {
    const stack = createCursorStack();
    stack.arrive(0, "c-2");
    stack.arrive(1, "c-3");
    stack.arrive(2, null);
    stack.arrive(0, "fresh-2");
    expect(stack.cursorAt(1)).toBe("fresh-2");
    expect(stack.cursorAt(2)).toBeNull();
    expect(stack.hasNext.value).toBe(true);
    stack.arrive(0, null);
    expect(stack.hasNext.value).toBe(false);
    expect(stack.cursorAt(1)).toBeNull();
  });

  it("the last page (no cursor) has no Next", () => {
    const stack = createCursorStack();
    stack.arrive(0, "c-2");
    stack.arrive(1, null);
    expect(stack.page.value).toBe(2);
    expect(stack.hasNext.value).toBe(false);
  });

  it("reset goes back to page 1 and forgets every cursor", () => {
    const stack = createCursorStack();
    stack.arrive(0, "c-2");
    stack.arrive(1, "c-3");
    stack.reset();
    expect(stack.page.value).toBe(1);
    expect(stack.hasNext.value).toBe(false);
    expect(stack.cursorAt(1)).toBeNull();
  });
});
