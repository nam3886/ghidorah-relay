import { defineStore } from "pinia";
import { computed, ref } from "vue";
import { getMe, login as apiLogin, logout as apiLogout } from "@/api/shell";
import { clearSession, getToken, getUser, setSession } from "@/lib/session";

export const useAuthStore = defineStore("auth", () => {
  const token = ref(getToken());
  const user = ref(getUser());
  const isSignedIn = computed(() => token.value !== "");

  // Views and stores that keep session-bound state (the Test tab's key, the one-time key callout, the catalog) register
  // here and are told on every sign-out, whether the user asked for it or a 401 forced it.
  const hooks = new Set<() => void>();
  function onSignOut(hook: () => void): () => void {
    hooks.add(hook);
    return () => hooks.delete(hook);
  }

  // Idempotent: parallel calls that all answer 401 end here together.
  function signOut(): void {
    clearSession();
    token.value = "";
    user.value = "";
    for (const hook of [...hooks]) {
      try {
        hook();
      } catch (err) {
        console.error("sign-out hook failed", err);
      }
    }
  }

  async function login(username: string, password: string): Promise<void> {
    const session = await apiLogin(username, password);
    setSession(session.token, username);
    token.value = session.token;
    user.value = username;
  }

  // The session may be gone on the server already, so a failed call does not stop the local sign-out.
  async function logout(): Promise<void> {
    try {
      await apiLogout();
    } catch {
      /* signed out locally anyway */
    }
    signOut();
  }

  async function loadUser(): Promise<void> {
    user.value = (await getMe()).username;
  }

  return { token, user, isSignedIn, onSignOut, signOut, login, logout, loadUser };
});
