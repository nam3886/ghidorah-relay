// Element Plus base and dark variables first, then our theme (it overrides them).
import "element-plus/theme-chalk/dark/css-vars.css";
import "@/styles/theme.css";
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
