import type { FrameInfo, FramesList, FrameVersion } from "@/api/frames";

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export const version = (id: number, text: string | null, by = "owner"): FrameVersion => ({
  id,
  text,
  created_ms: 1_700_000_000_000,
  created_by: by,
  created_by_id: null,
  restored_from: null,
});

// Two kinds: video with an own-frame model m1 and a default-following model m2 (the default differs from the shipped
// one), and image with no model at all.
export const framesList = (): FramesList => ({
  provider: "flow",
  kinds: [
    {
      kind: "video",
      default: version(1, "D {{client_prompt}} {{tracking_code}}"),
      shipped: "S",
      shipped_differs: true,
      models: [
        {
          model: "m1",
          source: "own",
          own: version(2, "own1"),
          effective: version(2, "own1"),
          locked: false,
          lock: null,
        },
        { model: "m2", source: "default", own: null, effective: version(1, "D"), locked: false, lock: null },
      ],
    },
    { kind: "image", default: null, shipped: null, shipped_differs: false, models: [] },
  ],
});

export const frameInfo = (current: string | null, extra: Partial<FrameInfo> = {}): FrameInfo => ({
  current: current === null ? null : version(5, current),
  effective: version(1, "effective text"),
  history: [version(5, current), version(4, "older")],
  ...extra,
});
