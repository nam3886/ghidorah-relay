import { describe, expect, it } from "vitest";
import { safeMediaUrl } from "@/lib/safe-media-url";

describe("safeMediaUrl", () => {
  it("accepts a same-origin share link and returns its path and query", () => {
    expect(safeMediaUrl("/m/abc123")).toBe("/m/abc123");
    expect(safeMediaUrl("/m/abc123?sig=1&x=2")).toBe("/m/abc123?sig=1&x=2");
    expect(safeMediaUrl(`${location.origin}/m/abc/0.mp4`)).toBe("/m/abc/0.mp4");
  });

  it("drops the fragment", () => {
    expect(safeMediaUrl("/m/abc#frag")).toBe("/m/abc");
  });

  it("refuses another host, a protocol-relative URL and a look-alike host", () => {
    expect(safeMediaUrl("https://evil.example/m/abc")).toBeNull();
    expect(safeMediaUrl("//evil.example/m/abc")).toBeNull();
    expect(safeMediaUrl(`${location.origin}.evil.example/m/abc`)).toBeNull();
  });

  it("refuses script, data and blob URLs", () => {
    // eslint-disable-next-line no-script-url -- the URL under test is the attack string itself
    expect(safeMediaUrl("javascript:alert(1)")).toBeNull();
    expect(safeMediaUrl("data:text/html,<script>alert(1)</script>")).toBeNull();
    expect(safeMediaUrl(`blob:${location.origin}/1234`)).toBeNull();
  });

  it("refuses a same-origin path outside /m/, including one that climbs out of it", () => {
    expect(safeMediaUrl("/admin/jobs")).toBeNull();
    expect(safeMediaUrl("/m")).toBeNull();
    expect(safeMediaUrl("/media/x")).toBeNull();
    expect(safeMediaUrl("/m/../admin/jobs")).toBeNull();
  });

  it("refuses anything that is not a non-empty string", () => {
    expect(safeMediaUrl("")).toBeNull();
    expect(safeMediaUrl(null)).toBeNull();
    expect(safeMediaUrl(undefined)).toBeNull();
    expect(safeMediaUrl(42)).toBeNull();
    expect(safeMediaUrl({ url: "/m/a" })).toBeNull();
  });
});
