import { describe, expect, it } from "vitest";
import { STATUS_TONES, httpTone, labelOf, toneOf } from "../../../src/components/shared/status-tones";

// The chip mapping. That the registry covers every status the backend can return is checked on the Python side
// (tests/admin_ui/test_status_tones.py), which can read the backend.

describe("the registry data", () => {
  it("gives every entry a known tone and a non-empty label", () => {
    for (const [status, entry] of Object.entries(STATUS_TONES)) {
      expect(["ok", "info", "warn", "bad", "neutral"], status).toContain(entry.tone);
      expect(entry.label.length, status).toBeGreaterThan(0);
    }
  });

  it("has no env entry: the environment is no longer a layer of a setting", () => {
    expect(STATUS_TONES).not.toHaveProperty("env");
  });

  it("shows a readable label and falls back to the word itself", () => {
    expect(labelOf("unclear_reply")).toBe("unclear reply");
    expect(labelOf("something-new")).toBe("something-new");
    expect(labelOf(503)).toBe("503");
  });
});

describe("the tones", () => {
  it.each([
    ["done", "ok"],
    ["ok", "ok"],
    ["available", "ok"],
    ["queued", "info"],
    ["submitting", "info"],
    ["waiting", "info"],
    ["fetching", "info"],
    ["in use", "info"],
    ["unknown", "warn"],
    ["model differs", "warn"],
    ["drift", "warn"],
    ["cooldown", "warn"],
    ["failed", "bad"],
    ["dead", "bad"],
    ["disabled", "neutral"],
    ["not checked", "neutral"],
    ["enabled", "neutral"],
    ["refunded", "neutral"],
    ["any model", "neutral"],
    ["db", "neutral"],
    ["stale", "warn"],
    ["own", "neutral"],
    ["default", "neutral"],
  ])("%s is %s", (status, tone) => {
    expect(toneOf(status)).toBe(tone);
  });

  it("unknown is not waiting: a job that needs a human stands out", () => {
    expect(toneOf("unknown")).not.toBe(toneOf("waiting"));
  });

  it("ignores case and surrounding spaces, and falls back to neutral for a word it does not know", () => {
    expect(toneOf(" FAILED ")).toBe("bad");
    expect(toneOf("something-new")).toBe("neutral");
  });

  it("tolerates a missing status: neutral and a dash, never a throw", () => {
    expect(toneOf(undefined)).toBe("neutral");
    expect(toneOf(null)).toBe("neutral");
    expect(labelOf(undefined)).toBe("-");
    expect(labelOf(null)).toBe("-");
  });

  it("maps an HTTP code by its class, on the exact boundaries", () => {
    expect(httpTone(200)).toBe("ok");
    expect(httpTone(399)).toBe("ok");
    expect(httpTone(400)).toBe("warn");
    expect(httpTone(499)).toBe("warn");
    expect(httpTone(500)).toBe("bad");
    expect(toneOf(429)).toBe("warn");
    expect(toneOf(502)).toBe("bad");
  });
});
