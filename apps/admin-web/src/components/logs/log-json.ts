// The JSON colouring of the request drawer's code blocks (LogDetail.dc.html): keys, strings, numbers, true/false/null
// and punctuation each get a class; every other character stays as it was. Text that is not JSON is not coloured.
export type JsonKind = "key" | "str" | "num" | "lit" | "pun" | "";
export interface JsonPart {
  text: string;
  kind: JsonKind;
}

const TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(null|true|false)|([{}[\],:])/g;

function isJson(text: string): boolean {
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
}

function lineParts(line: string): JsonPart[] {
  const parts: JsonPart[] = [];
  let at = 0;
  const plain = (to: number) => {
    if (to > at) parts.push({ text: line.slice(at, to), kind: "" });
  };
  for (const m of line.matchAll(TOKEN)) {
    plain(m.index);
    if (m[1] && m[2]) parts.push({ text: m[1], kind: "key" }, { text: m[2], kind: "pun" });
    else if (m[1]) parts.push({ text: m[1], kind: "str" });
    else if (m[3]) parts.push({ text: m[3], kind: "num" });
    else if (m[4]) parts.push({ text: m[4], kind: "lit" });
    else parts.push({ text: m[5] as string, kind: "pun" });
    at = m.index + m[0].length;
  }
  plain(line.length);
  return parts;
}

// One list of parts per line.
export function highlight(text: string): JsonPart[][] {
  const lines = text.split("\n");
  if (!isJson(text)) return lines.map((l) => [{ text: l, kind: "" as const }]);
  return lines.map(lineParts);
}
