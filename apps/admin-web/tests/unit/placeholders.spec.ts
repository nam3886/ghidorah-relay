import { describe, expect, it } from "vitest";

// A view that is not written yet carries this marker. Every view and the job detail are real now, so the marker must
// not appear anywhere in src/: this guard always runs (no release switch), in `web.sh check` and in every gate.
export const MARKER = "relay-admin-placeholder";

const files = import.meta.glob("/src/**/*.{vue,ts}", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;

export function filesWithMarker(sources: Record<string, string>): string[] {
  return Object.entries(sources)
    .filter(([, source]) => source.includes(MARKER))
    .map(([path]) => path.replace("/src/", ""))
    .sort();
}

describe("placeholder marker", () => {
  it("is in no file of src/", () => {
    expect(Object.keys(files).length).toBeGreaterThan(20);
    expect(filesWithMarker(files)).toEqual([]);
  });

  // The control for the check above: the detector does see a marker, in a .vue and in a .ts file.
  it("is found when a file carries it", () => {
    const planted = {
      "/src/views/A.vue": `<template><p>${MARKER}</p></template>`,
      "/src/lib/b.ts": `// ${MARKER}`,
      "/src/lib/clean.ts": "export const x = 1;",
    };
    expect(filesWithMarker(planted)).toEqual(["lib/b.ts", "views/A.vue"]);
  });
});
