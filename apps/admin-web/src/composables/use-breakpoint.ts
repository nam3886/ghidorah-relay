import { onScopeDispose, readonly, ref, type Ref } from "vue";

// The one place the layout breakpoints live (contracts C3). Phone is below 768 px, tablet is 768 to 1279 px, desktop is
// 1280 px and up. Style sheets cannot read this: they use the same two numbers in a media query (theme.css says so).
export const PHONE_QUERY = "(max-width: 767px)";
export const TABLET_QUERY = "(min-width: 768px) and (max-width: 1279px)";

// Reactive isPhone and isTablet on window.matchMedia, removed again when the component (or effect scope) ends. Where
// matchMedia is missing (happy-dom) both are false, which is the desktop layout.
export function useBreakpoint(): { isPhone: Readonly<Ref<boolean>>; isTablet: Readonly<Ref<boolean>> } {
  const isPhone = ref(false);
  const isTablet = ref(false);
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return { isPhone: readonly(isPhone), isTablet: readonly(isTablet) };
  }

  const watch = (query: string, target: Ref<boolean>): void => {
    const list = window.matchMedia(query);
    target.value = list.matches;
    const onChange = (event: { matches: boolean }): void => {
      target.value = event.matches;
    };
    list.addEventListener("change", onChange);
    onScopeDispose(() => list.removeEventListener("change", onChange));
  };
  watch(PHONE_QUERY, isPhone);
  watch(TABLET_QUERY, isTablet);
  return { isPhone: readonly(isPhone), isTablet: readonly(isTablet) };
}
