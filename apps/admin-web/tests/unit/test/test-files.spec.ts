import { describe, expect, it, vi } from "vitest";
import {
  addFiles,
  clearFiles,
  type FileStore,
  MAX_BYTES,
  MAX_IMAGES,
  restoreFiles,
  saveFiles,
  type StoredFile,
  validFiles,
} from "@/lib/test-files";

const MIB = 1024 * 1024;

// A Blob that reports a size without holding the bytes, so the byte limit is tested at its exact edge.
function blob(size: number, type = "image/png"): Blob {
  const b = new Blob(["x"], { type });
  Object.defineProperty(b, "size", { value: size });
  return b;
}
const stored = (name: string, size = 1, type = "image/png"): StoredFile => ({ name, type, blob: blob(size, type) });
const many = (n: number, size = 1): StoredFile[] => Array.from({ length: n }, (_, i) => stored(`f${i}.png`, size));

function memoryStore(initial?: unknown) {
  let value: unknown = initial;
  const store: FileStore & { value: () => unknown } = {
    get: vi.fn(async () => value),
    put: vi.fn(async (files: StoredFile[]) => void (value = files)),
    remove: vi.fn(async () => void (value = undefined)),
    value: () => value,
  };
  return store;
}

describe("the limits", () => {
  it("are 10 files and 12 MiB together", () => {
    expect(MAX_IMAGES).toBe(10);
    expect(MAX_BYTES).toBe(12 * MIB);
  });

  it("keeps 10 files and drops the 11th", () => {
    expect(validFiles(many(10))).toHaveLength(10);
    const kept = validFiles(many(11));
    expect(kept).toHaveLength(10);
    expect(kept.map((f) => f.name)).toEqual(many(10).map((f) => f.name));
  });

  it("keeps 12 MiB exactly and drops what takes the total over it by one byte", () => {
    expect(validFiles([stored("a.png", 12 * MIB)])).toHaveLength(1);
    expect(validFiles([stored("a.png", 12 * MIB + 1)])).toHaveLength(0);
    const kept = validFiles([stored("a.png", 6 * MIB), stored("b.png", 6 * MIB), stored("c.png", 1)]);
    expect(kept.map((f) => f.name)).toEqual(["a.png", "b.png"]);
  });

  it("drops a file that breaks a rule and goes on with the next one", () => {
    const kept = validFiles([stored("huge.png", 13 * MIB), stored("small.png", 5)]);
    expect(kept.map((f) => f.name)).toEqual(["small.png"]);
  });

  it("keeps images only, by the blob's own type", () => {
    const kept = validFiles([
      stored("a.png"),
      stored("note.txt", 1, "text/plain"),
      { name: "liar.png", type: "image/png", blob: blob(1, "application/pdf") },
      stored("b.webp", 1, "image/webp"),
    ]);
    expect(kept.map((f) => f.name)).toEqual(["a.png", "b.webp"]);
    // the kept type is the blob's, not what the entry claimed
    expect(validFiles([{ name: "x", type: "text/plain", blob: blob(1, "image/gif") }])[0]?.type).toBe("image/gif");
  });

  it("drops a value that is not an array whole, and entries that are not files", () => {
    expect(validFiles({ 0: stored("a.png"), length: 1 })).toEqual([]);
    expect(validFiles("x")).toEqual([]);
    expect(validFiles(null)).toEqual([]);
    expect(
      validFiles([null, 7, "x", {}, { name: "n", blob: "not a blob" }, stored("ok.png")]).map((f) => f.name),
    ).toEqual(["ok.png"]);
  });

  it("names a file without a name 'image'", () => {
    expect(validFiles([{ blob: blob(1) }])[0]?.name).toBe("image");
  });
});

describe("attaching", () => {
  const file = (name: string, size: number, type = "image/png") => {
    const f = new File(["x"], name, { type });
    Object.defineProperty(f, "size", { value: size });
    return f;
  };

  it("adds after the files already attached and names those that do not fit", () => {
    const out = addFiles(many(9), [file("ten.png", 1), file("eleven.png", 1)]);
    expect(out.files).toHaveLength(10);
    expect(out.skipped).toEqual(["eleven.png"]);
  });

  it("skips a non-image and a file that takes the total over 12 MiB", () => {
    const out = addFiles(
      [stored("a.png", 11 * MIB)],
      [file("b.txt", 1, "text/plain"), file("big.png", MIB + 1), file("ok.png", MIB)],
    );
    expect(out.files.map((f) => f.name)).toEqual(["a.png", "ok.png"]);
    expect(out.skipped).toEqual(["b.txt", "big.png"]);
  });
});

describe("writing", () => {
  it("checks the list before every write: the store never receives more than the limits", async () => {
    const store = memoryStore();
    const out = await saveFiles([...many(11), stored("text.txt", 1, "text/plain")], store);
    expect(out.saved).toBe(true);
    expect(out.files).toHaveLength(10);
    expect(vi.mocked(store.put).mock.calls[0]?.[0]).toHaveLength(10);
  });

  it("deletes the key when nothing is left", async () => {
    const store = memoryStore();
    const out = await saveFiles([stored("text.txt", 1, "text/plain")], store);
    expect(out).toEqual({ files: [], saved: true });
    expect(store.put).not.toHaveBeenCalled();
    expect(store.remove).toHaveBeenCalledTimes(1);
  });

  it("says when the store did not take it, and still returns the checked list", async () => {
    const store = memoryStore();
    vi.mocked(store.put).mockRejectedValue(new Error("quota"));
    const out = await saveFiles(many(2), store);
    expect(out.saved).toBe(false);
    expect(out.files).toHaveLength(2);
  });

  it("clearFiles deletes the key and swallows a blocked store", async () => {
    const store = memoryStore(many(1));
    await clearFiles(store);
    expect(store.remove).toHaveBeenCalledTimes(1);
    vi.mocked(store.remove).mockRejectedValue(new Error("blocked"));
    await expect(clearFiles(store)).resolves.toBeUndefined();
  });
});

describe("restoring", () => {
  it("gives back a valid list untouched, with no rewrite and no notice", async () => {
    const store = memoryStore(many(3));
    const out = await restoreFiles(store);
    expect(out.dropped).toBe(false);
    expect(out.files).toHaveLength(3);
    expect(store.put).not.toHaveBeenCalled();
    expect(store.remove).not.toHaveBeenCalled();
  });

  it("drops what breaks a limit, rewrites the store with what is left and says so", async () => {
    const store = memoryStore([...many(10), stored("11.png"), stored("doc.txt", 1, "text/plain")]);
    const out = await restoreFiles(store);
    expect(out.dropped).toBe(true);
    expect(out.files).toHaveLength(10);
    expect(vi.mocked(store.put).mock.calls[0]?.[0]).toHaveLength(10);
    expect(store.value()).toHaveLength(10);
  });

  it("drops a total over 12 MiB the same way", async () => {
    const store = memoryStore([stored("a.png", 12 * MIB), stored("b.png", 1)]);
    const out = await restoreFiles(store);
    expect(out.dropped).toBe(true);
    expect(out.files.map((f) => f.name)).toEqual(["a.png"]);
  });

  it("drops a value that is not an array whole: the store is cleared and the notice is due", async () => {
    const store = memoryStore({ name: "a.png", blob: blob(1) });
    const out = await restoreFiles(store);
    expect(out).toEqual({ files: [], dropped: true, failed: false });
    expect(store.remove).toHaveBeenCalledTimes(1);
    expect(store.value()).toBeUndefined();
  });

  it("an empty store gives an empty list and no notice", async () => {
    expect(await restoreFiles(memoryStore())).toEqual({ files: [], dropped: false, failed: false });
    expect(await restoreFiles(memoryStore(null))).toEqual({ files: [], dropped: false, failed: false });
  });

  it("a store that cannot be read is reported as a storage failure, with an empty list and no write", async () => {
    const store = memoryStore();
    vi.mocked(store.get).mockRejectedValue(new Error("blocked"));
    expect(await restoreFiles(store)).toEqual({ files: [], dropped: false, failed: true });
    expect(store.put).not.toHaveBeenCalled();
    expect(store.remove).not.toHaveBeenCalled();
  });
});
