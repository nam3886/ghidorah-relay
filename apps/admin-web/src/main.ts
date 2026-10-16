// Fonts: only the latin and vietnamese subsets, self-hosted from the packages (the CSP allows 'self' only).
import "@fontsource/be-vietnam-pro/latin-400.css";
import "@fontsource/be-vietnam-pro/latin-500.css";
import "@fontsource/be-vietnam-pro/latin-600.css";
import "@fontsource/be-vietnam-pro/latin-700.css";
import "@fontsource/be-vietnam-pro/vietnamese-400.css";
import "@fontsource/be-vietnam-pro/vietnamese-500.css";
import "@fontsource/be-vietnam-pro/vietnamese-600.css";
import "@fontsource/be-vietnam-pro/vietnamese-700.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource/ibm-plex-mono/vietnamese-400.css";
import "@fontsource/ibm-plex-mono/vietnamese-500.css";
// Element Plus base and dark variables first, then our theme (it overrides them).
import "element-plus/theme-chalk/dark/css-vars.css";
import "@/styles/theme.css";
import "@/components/shared/skin.css";
import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "@/App.vue";
import { installUnauthorizedHandler } from "@/api/unauthorized";
import { createAppRouter } from "@/router";

const app = createApp(App);
app.use(createPinia());
const router = createAppRouter();
installUnauthorizedHandler(router);
app.use(router);
app.mount("#app");
