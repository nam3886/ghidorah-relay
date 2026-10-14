import { fileURLToPath, URL } from "node:url";
import vue from "@vitejs/plugin-vue";
import AutoImport from "unplugin-auto-import/vite";
import { ElementPlusResolver } from "unplugin-vue-components/resolvers";
import Components from "unplugin-vue-components/vite";
import { defineConfig } from "vitest/config";

// The built app is served by the api under /ui/app/ (pages.py). Nothing is fetched from another host.
export default defineConfig({
  base: "/ui/app/",
  plugins: [
    vue(),
    // Element Plus on demand: the resolvers import only the components and service styles that are used. Our own
    // components are imported explicitly (dirs: []), so only Element Plus names are resolved.
    AutoImport({ imports: [], resolvers: [ElementPlusResolver()], dts: "auto-imports.d.ts" }),
    Components({ dirs: [], resolvers: [ElementPlusResolver()], dts: "components.d.ts" }),
  ],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: {
    outDir: fileURLToPath(new URL("../../packages/relay-core/src/relay_core/admin/static/app", import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
    // The CSP has no img-src (it falls back to 'self'), so a data: URI would be blocked: ship every asset as a file.
    assetsInlineLimit: 0,
  },
  test: {
    environment: "happy-dom",
    include: ["tests/unit/**/*.spec.ts"],
    // Element Plus's on-demand style imports are .css files: let Vite handle them instead of Node.
    server: { deps: { inline: ["element-plus"] } },
    // The first import of Element Plus in a worker is slow on the box.
    testTimeout: 30000,
    restoreMocks: true,
    unstubGlobals: true,
  },
});
