// The Test tab's form: what is kept in localStorage (relay_test_form), and the request body built from it. The typed
// API key is NOT part of the form and never reaches localStorage (it lives in sessionStorage, see test-key.ts).
import type { PublicModel } from "@/api/test";

export const FORM_KEY = "relay_test_form";
export const FIELDS = ["prompt", "duration", "ratio", "count", "style", "images", "title", "options"] as const;
export type FormField = (typeof FIELDS)[number];
export type FormFields = Record<FormField, string>;
export interface TestForm extends FormFields {
  model: string;
}

export function emptyForm(): TestForm {
  return { model: "", prompt: "", duration: "", ratio: "", count: "", style: "", images: "", title: "", options: "" };
}

// Anything damaged or not a string is ignored, field by field, so a damaged value never breaks the tab. `ok` is false
// when the store itself could not be read (blocked): the tab then says the form is not kept in this browser.
export function readForm(): { form: TestForm; ok: boolean } {
  const form = emptyForm();
  let raw: string | null;
  try {
    raw = localStorage.getItem(FORM_KEY);
  } catch {
    return { form, ok: false };
  }
  let saved: unknown;
  try {
    saved = JSON.parse(raw ?? "null");
  } catch {
    return { form, ok: true };
  }
  if (!saved || typeof saved !== "object" || Array.isArray(saved)) return { form, ok: true };
  const values = saved as Record<string, unknown>;
  for (const field of ["model", ...FIELDS] as const) {
    if (typeof values[field] === "string") form[field] = values[field];
  }
  return { form, ok: true };
}

// Written on every input. False when a blocked or full store refused it: the form then lasts until reload.
export function saveForm(form: TestForm): boolean {
  const out: Record<string, string> = { model: form.model };
  for (const field of FIELDS) out[field] = form[field];
  try {
    localStorage.setItem(FORM_KEY, JSON.stringify(out));
    return true;
  } catch {
    return false;
  }
}

export function clearSavedForm(): boolean {
  try {
    localStorage.removeItem(FORM_KEY);
    return true;
  } catch {
    return false;
  }
}

// The label of a provider option (a ratio or a style): the first of these fields that holds text.
export function optionLabel(option: Record<string, unknown>): string {
  for (const key of ["name", "label", "title", "value", "id"]) {
    const value = option[key];
    if (typeof value === "string" && value) return value;
  }
  return "";
}

// What the request carries for a provider option (a ratio or a style): its `value` ("16:9", "oil_painting") when it has
// one, which is all the provider takes; else its label. dola labels a ratio with a sentence around the value and a
// style in words, and refuses either. A ratio is also shown by its value, as drawn; a style by its label.
export function optionValue(option: Record<string, unknown>): string {
  return typeof option.value === "string" && option.value ? option.value : optionLabel(option);
}

// The generation request. Duration is for video, style for image; a field left empty is not sent. `images` are the
// https URLs (one per line) followed by the attached files as data URIs. Options must be JSON: invalid text throws a
// SyntaxError, which the tab shows in its status line.
export function buildBody(model: PublicModel, form: FormFields, dataUris: string[]): Record<string, unknown> {
  const body: Record<string, unknown> = { model: model.id, prompt: form.prompt };
  if (model.kind === "video" && form.duration.trim()) body.duration = Number(form.duration);
  const ratio = form.ratio.trim();
  if (ratio) body.ratio = ratio;
  if (form.count.trim()) body.n = Number(form.count);
  const style = form.style.trim();
  if (model.kind === "image" && style) body.style = style;
  const title = form.title.trim();
  if (title) body.title = title;
  const images = [
    ...form.images
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean),
    ...dataUris,
  ];
  if (images.length) body.images = images;
  if (form.options.trim()) body.options = JSON.parse(form.options);
  return body;
}

export function blobToDataUri(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Could not read an attached image"));
    reader.readAsDataURL(blob);
  });
}
