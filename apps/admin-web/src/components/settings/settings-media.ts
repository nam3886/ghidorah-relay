import { onScopeDispose, readonly, ref, type Ref } from "vue";

// The Settings page stacks below 968 px (SettingsTablet, SettingsPhone): the label column gives way and each section's
// title goes inside its card. SettingsCard's style sheet uses the same number.
export const STACKED_QUERY = "(max-width: 967px)";

// Reactive, on window.matchMedia; false where matchMedia is missing (happy-dom), which is the desktop layout.
export function useStacked(): Readonly<Ref<boolean>> {
  const stacked = ref(false);
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return readonly(stacked);
  const list = window.matchMedia(STACKED_QUERY);
  stacked.value = list.matches;
  const onChange = (event: { matches: boolean }): void => {
    stacked.value = event.matches;
  };
  list.addEventListener("change", onChange);
  onScopeDispose(() => list.removeEventListener("change", onChange));
  return readonly(stacked);
}
