import { computed, ref } from "vue";

// The server pages by keyset: each page answers with the cursor of the next one and knows nothing of "back". The
// stack keeps the cursor each known page starts at ([null] is page 1), so Prev reloads exactly the page that was left.
//
// `arrive(at, next)` is called once page `at` (0-based) has loaded: the stack is cut after it (pages beyond are stale)
// and `next`, when the server gave one, is pushed as the start of the following page.
export function createCursorStack() {
  const cursors = ref<(string | null)[]>([null]);
  const index = ref(0);

  return {
    index,
    page: computed(() => index.value + 1),
    hasNext: computed(() => cursors.value.length > index.value + 1),
    cursorAt: (at: number): string | null => cursors.value[at] ?? null,
    prevIndex: (): number => Math.max(0, index.value - 1),
    nextIndex: (): number => index.value + 1,
    arrive(at: number, next: string | null): void {
      index.value = at;
      cursors.value = cursors.value.slice(0, at + 1);
      if (next) cursors.value.push(next);
    },
    reset(): void {
      cursors.value = [null];
      index.value = 0;
    },
  };
}

export type CursorStack = ReturnType<typeof createCursorStack>;
