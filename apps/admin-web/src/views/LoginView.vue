<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ApiError } from "@/api/client";
import { IconAlertCircleShort, IconEye, IconMoon } from "@/components/shared/icons";
import { getMe } from "@/api/shell";
import { errorText } from "@/lib/errors";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

// The sign-in page (Login, LoginTablet, LoginPhone): no shell, one card centred on the page ground: the mark in its
// tinted square, "Sign in to Ghidorah" and "Relay admin", the error box (absent until a sign-in fails), Username,
// Password with its eye button, Sign in, and the host under the card. Own fields, not Element Plus.
const auth = useAuthStore();
const ui = useUiStore();
const router = useRouter();

const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
const reveal = ref(false);
const host = location.host;

async function submit(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    await auth.login(username.value, password.value);
    await router.replace({ name: "jobs" });
  } catch (err) {
    error.value = err instanceof ApiError && err.status === 401 ? "Wrong username or password" : errorText(err);
  } finally {
    // the password never outlives the request, whatever the answer: not in reactive state, not in the field
    password.value = "";
    reveal.value = false;
    busy.value = false;
  }
}

// Already signed in: go straight to the dashboard. A dead token is dropped instead.
onMounted(async () => {
  if (!auth.token) return;
  try {
    await getMe(true);
    await router.replace({ name: "jobs" });
  } catch {
    auth.signOut();
  }
});
</script>

<template>
  <div class="login-page">
    <button
      type="button"
      class="theme-toggle"
      aria-label="Switch theme"
      title="Switch theme"
      data-testid="theme-toggle"
      @click="ui.toggleTheme()"
    >
      <IconMoon :size="16" />
    </button>

    <main class="login-wrap">
      <form class="login-card" aria-labelledby="login-title" @submit.prevent="submit">
        <div class="login-brand">
          <span class="login-mark">
            <svg viewBox="50 20 510 520" width="30" height="30" role="img" aria-label="Ghidorah">
              <g>
                <path
                  d="M369 64L353 24L406 49Q437 61 453 81L500 104L490 131L447 123L406 125Q391 125 382 113C247 98 123 169 104 287C98 327 108 364 128 390C61 361 49 296 67 233C105 110 239 52 369 64Z"
                />
                <path
                  d="M392 204L378 173L431 198Q459 209 475 230L519 251L509 278L470 272L430 274Q414 274 404 263H294C219 263 175 295 172 341C167 394 213 432 275 432C309 432 341 421 368 402L346 386H301L264 346H391L377 316L435 343Q464 354 479 375L548 407L538 434L500 431L471 436C428 492 366 523 286 523C174 523 96 458 96 366C96 268 176 204 287 204Z"
                />
              </g>
            </svg>
          </span>
          <div class="login-heading">
            <h1 id="login-title" class="login-title">Sign in to Ghidorah</h1>
            <span class="login-sub">Relay admin</span>
          </div>
        </div>

        <!-- absent, not hidden, while there is no error -->
        <div v-if="error" id="loginError" class="login-error" role="alert">
          <IconAlertCircleShort class="login-error__icon" :size="18" />
          <b>{{ error }}</b>
        </div>

        <div class="field">
          <label for="login-user">Username</label>
          <input
            id="login-user"
            v-model="username"
            class="field-input"
            type="text"
            name="username"
            autocomplete="username"
            maxlength="64"
            autofocus
          />
        </div>

        <div class="field">
          <label for="login-pass">Password</label>
          <span class="field-wrap" :class="{ 'is-invalid': error }">
            <input
              id="login-pass"
              v-model="password"
              class="field-input field-input--secret"
              :class="{ 'field-input--invalid': error }"
              :type="reveal ? 'text' : 'password'"
              name="password"
              autocomplete="current-password"
              maxlength="256"
              :aria-invalid="error ? 'true' : undefined"
              :aria-describedby="error ? 'loginError' : undefined"
            />
            <button
              type="button"
              class="reveal"
              :aria-label="reveal ? 'Hide password' : 'Show password'"
              :aria-pressed="reveal"
              @click="reveal = !reveal"
            >
              <IconEye :size="18" />
            </button>
          </span>
          <span v-if="error" class="field-error">Check the password and try again.</span>
        </div>

        <button type="submit" class="submit" :disabled="busy">{{ busy ? "Signing in…" : "Sign in" }}</button>
      </form>
      <p class="login-foot mono">{{ host }}</p>
    </main>
  </div>
</template>

<style scoped>
.login-page {
  position: relative;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--rl-bg);
  color: var(--rl-fg);
}
/* not in the canvas: the page's own theme switch, a ghost icon button in the corner */
.theme-toggle {
  position: absolute;
  top: 16px;
  right: 16px;
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.theme-toggle:hover {
  background: var(--rl-track);
  color: var(--rl-fg);
}
@media (pointer: coarse) {
  .theme-toggle {
    width: 44px;
    height: 44px;
    border-radius: 10px;
  }
}
.login-wrap {
  flex: 1;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  padding: 48px 16px;
}
.login-card {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 18px;
  width: 100%;
  max-width: 400px;
  margin: 0;
  padding: 32px;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow:
    0 0 0 1px var(--rl-line),
    0 1px 2px rgba(16, 24, 40, 0.05),
    0 24px 48px rgba(16, 24, 40, 0.08);
}
:global(html.dark) .login-card {
  box-shadow:
    0 0 0 1px var(--rl-line),
    0 24px 48px rgba(0, 0, 0, 0.45);
}
.login-brand {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  text-align: center;
}
.login-mark {
  display: grid;
  place-items: center;
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: var(--rl-primary-soft);
}
.login-mark svg {
  display: block;
  fill: var(--rl-brand);
}
.login-heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.login-title {
  margin: 0;
  font-size: 22px;
  line-height: 30px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.login-sub {
  color: var(--rl-muted);
}
.login-error {
  display: flex;
  gap: 10px;
  padding: 12px 14px;
  border-radius: 10px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
  font-size: 13px;
  line-height: 19px;
  overflow-wrap: anywhere;
}
.login-error__icon {
  flex: none;
  margin-top: 1px;
}
.login-error b {
  font-weight: 700;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field label {
  font-size: 13px;
  font-weight: 700;
}
.field-input {
  box-sizing: border-box;
  width: 100%;
  height: 44px;
  padding: 0 14px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
  font-size: 14px;
}
.field-input:focus-visible {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
/* the password field: one box holding the input and the eye button */
.field-wrap {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  height: 44px;
  overflow: hidden;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
}
.field-wrap:focus-within {
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.field-wrap.is-invalid {
  border-color: var(--rl-bad);
  box-shadow: 0 0 0 3px rgba(180, 35, 24, 0.1);
}
:global(html.dark) .field-wrap.is-invalid {
  box-shadow: 0 0 0 3px rgba(255, 156, 146, 0.18);
}
.field-wrap .field-input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: 0;
  border-radius: 0;
  background: transparent;
  letter-spacing: 0.1em;
}
.field-wrap .field-input:focus-visible {
  box-shadow: none;
}
.reveal {
  display: grid;
  place-items: center;
  flex: none;
  width: 44px;
  height: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.reveal:hover {
  color: var(--rl-fg);
}
.reveal:focus-visible {
  outline-offset: -2px;
}
.field-error {
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--rl-bad);
}
.submit {
  height: 44px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  box-shadow:
    0 1px 2px rgba(16, 24, 40, 0.12),
    inset 0 -1px 0 rgba(0, 0, 0, 0.12);
}
.submit:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.submit:disabled {
  cursor: progress;
  opacity: 0.7;
}
.login-foot {
  margin: 0;
  font-size: 12px;
  color: var(--rl-faint);
  text-align: center;
  overflow-wrap: anywhere;
}

/* tablet and phone (LoginTablet, LoginPhone): the card's side padding narrows; a phone field takes 16 px text so iOS
   does not zoom into it */
@media (max-width: 1279px) {
  .login-card {
    padding: 28px 32px;
  }
}
@media (max-width: 767px) {
  .login-card {
    padding: 28px 20px;
  }
  .field-input {
    font-size: 16px;
  }
}
</style>
