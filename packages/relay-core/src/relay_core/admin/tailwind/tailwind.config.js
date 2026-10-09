// Theme of the admin pages (the colours and the slide-up toast of the old dashboard). The scanner reads every class
// written out in full in ../static/*.html and ../static/*.js.
module.exports = {
  content: ["../static/*.html", "../static/*.js"],
  theme: {
    extend: {
      colors: {
        border: "hsl(0 0% 89%)", input: "hsl(0 0% 89%)", ring: "hsl(0 0% 3.9%)",
        background: "hsl(0 0% 100%)", foreground: "hsl(0 0% 3.9%)",
        accent: { DEFAULT: "hsl(0 0% 96.1%)", foreground: "hsl(0 0% 9%)" },
        primary: { DEFAULT: "hsl(0 0% 9%)", foreground: "hsl(0 0% 98%)" },
        secondary: { DEFAULT: "hsl(0 0% 96.1%)", foreground: "hsl(0 0% 9%)" },
        muted: { DEFAULT: "hsl(0 0% 96.1%)", foreground: "hsl(0 0% 45.1%)" },
        destructive: { DEFAULT: "hsl(0 84.2% 60.2%)", foreground: "hsl(0 0% 98%)" },
      },
      keyframes: { "slide-up": { from: { transform: "translateY(100%)", opacity: "0" }, to: { transform: "translateY(0)", opacity: "1" } } },
      animation: { "slide-up": "slide-up .3s ease-out" },
    },
  },
};
