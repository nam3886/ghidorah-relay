import js from "@eslint/js";
import { defineConfig } from "eslint/config";
import prettier from "eslint-config-prettier";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";
import tseslint from "typescript-eslint";

// Besides style, this config enforces the CSP rules for authors: no static style="", no v-html, no HTML strings,
// no ElWatermark, no eval or new Function, no javascript: URLs.
const banned = (selector, message) => ({ selector, message });

export default defineConfig(
  { ignores: ["node_modules/**", "components.d.ts", "auto-imports.d.ts", "dist/**"] },
  js.configs.recommended,
  tseslint.configs.recommended,
  // Correctness rules only: there is no formatter in this repo, so layout rules would only be noise.
  pluginVue.configs["flat/essential"],
  {
    files: ["**/*.vue", "**/*.ts"],
    languageOptions: { globals: globals.browser, parserOptions: { parser: tseslint.parser } },
    rules: {
      "vue/no-v-html": "error",
      "vue/no-static-inline-styles": ["error", { allowBinding: true }],
      "vue/multi-word-component-names": ["error", { ignores: ["App"] }],
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-script-url": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      "no-restricted-syntax": [
        "error",
        banned("Identifier[name=ElWatermark]", "ElWatermark writes a style attribute, which the CSP blocks."),
        banned("Literal[value=el-watermark]", "ElWatermark writes a style attribute, which the CSP blocks."),
        banned("Property[key.name=dangerouslyUseHTMLString]", "Server text is always text: no HTML strings."),
        banned("MemberExpression[property.name=/^(innerHTML|outerHTML)$/]", "No HTML strings: use templates."),
        banned("CallExpression[callee.property.name=insertAdjacentHTML]", "No HTML strings: use templates."),
        banned(
          "CallExpression[callee.property.name=setAttribute][arguments.0.value=style]",
          "The CSP blocks style attributes: use a class or the CSSOM.",
        ),
      ],
    },
  },
  { files: ["vite.config.ts", "eslint.config.js"], languageOptions: { globals: globals.node } },
  { files: ["public/**/*.js"], languageOptions: { globals: globals.browser } },
  // Last: switches off every ESLint rule that Prettier owns.
  prettier,
);
