// The session lives in sessionStorage under the same key names the old admin used, so stored data survives the deploy.
export const TOKEN_KEY = "relay_admin_token";
export const USER_KEY = "relay_admin_user";
export const TEST_KEY = "relay_test_key";
export const THEME_KEY = "relay_admin_theme";
export const OPERATIONS_KEY = "regenerateOperations";

export function getToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function getUser(): string {
  try {
    return sessionStorage.getItem(USER_KEY) || "";
  } catch {
    return "";
  }
}

export function setSession(token: string, user: string): void {
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USER_KEY, user);
}

// Token, user and the Test tab's typed key all go together.
export function clearSession(): void {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(USER_KEY);
    sessionStorage.removeItem(TEST_KEY);
  } catch {
    /* storage blocked: nothing was stored */
  }
}
