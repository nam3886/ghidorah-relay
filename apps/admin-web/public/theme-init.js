// Runs before the bundle so the page never flashes the wrong theme. Same origin file: the CSP forbids inline script.
// Keep in step with src/stores/ui.ts (same storage key and the same rule: a stored choice wins, else the OS setting).
(function () {
  var theme;
  try {
    theme = localStorage.getItem("relay_admin_theme");
  } catch {
    theme = null;
  }
  if (theme !== "dark" && theme !== "light") {
    theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  document.documentElement.classList.toggle("dark", theme === "dark");
})();
