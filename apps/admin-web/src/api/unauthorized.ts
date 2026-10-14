import type { Router } from "vue-router";
import { setUnauthorizedHandler } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

// A 401 on any call (the client has already cleared the stored session): run the sign-out hooks, go to login.
export function installUnauthorizedHandler(router: Router): void {
  setUnauthorizedHandler(() => {
    useAuthStore().signOut();
    void router.replace({ name: "login" });
  });
}
