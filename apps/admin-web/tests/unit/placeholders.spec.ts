import { describe, expect, it } from "vitest";

// Every view and the job detail start as a placeholder carrying this marker; the round that owns a file removes it.
const MARKER = "relay-admin-placeholder";
const files = import.meta.glob("/src/**/*.vue", { query: "?raw", import: "default", eager: true }) as Record<
  string,
  string
>;
const withMarker = Object.entries(files)
  .filter(([, source]) => source.includes(MARKER))
  .map(([path]) => path.replace("/src/", ""))
  .sort();

const PLACEHOLDERS = [
  "components/jobs/JobDetailDialog.vue",
  "views/AccountsView.vue",
  "views/AlertsView.vue",
  "views/FramesView.vue",
  "views/JobsView.vue",
  "views/KeysView.vue",
  "views/LogsView.vue",
  "views/SettingsView.vue",
  "views/TestView.vue",
];

describe("placeholder marker", () => {
  it("appears only in the files that are meant to be filled in later", () => {
    expect(withMarker.filter((path) => !PLACEHOLDERS.includes(path))).toEqual([]);
  });

  // The release gate sets RELAY_ADMIN_RELEASE=1: no placeholder may be left when the old admin is replaced.
  it.skipIf(process.env.RELAY_ADMIN_RELEASE !== "1")("no placeholder is left in a release", () => {
    expect(withMarker).toEqual([]);
  });
});
