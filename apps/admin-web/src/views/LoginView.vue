<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ApiError } from "@/api/client";
import BrandLogo from "@/components/layout/BrandLogo.vue";
import { IconAlertCircleShort, IconEye, IconMoon } from "@/components/shared/icons";
import { getMe } from "@/api/shell";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { errorText } from "@/lib/errors";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

// The sign-in page as drawn in B-Login and P-Login (specs/login.md): no shell, one card on the page ground, own
// fields (not Element Plus), the error box is absent until a sign-in fails.
const auth = useAuthStore();
const ui = useUiStore();
const router = useRouter();
const { isPhone } = useBreakpoint();

const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");
const reveal = ref(false);

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
      data-testid="theme-toggle"
      @click="ui.toggleTheme()"
    >
      <IconMoon :size="isPhone ? 18 : 16" />
    </button>

    <main class="login-card">
      <div class="login-brand">
        <BrandLogo variant="login" />
        <h1 class="login-title">Sign in to the admin</h1>
      </div>

      <form class="login-form" @submit.prevent="submit">
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
          <div class="field-wrap">
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
              <IconEye :size="isPhone ? 18 : 16" />
            </button>
          </div>
        </div>

        <!-- absent, not hidden, while there is no error -->
        <div v-if="error" id="loginError" class="login-error" role="alert">
          <IconAlertCircleShort class="login-error__icon" :size="16" />
          {{ error }}
        </div>

        <button type="submit" class="submit" :disabled="busy">{{ busy ? "Signing in…" : "Sign in" }}</button>
      </form>
    </main>

    <p class="login-foot">Ghidorah admin · LAN only</p>
  </div>
</template>

<style scoped>
.login-page {
  position: relative;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 20px;
  min-height: 760px;
  padding: 64px 16px;
  background: var(--rl-bg);
  color: var(--rl-fg);
}
.theme-toggle {
  position: absolute;
  top: 20px;
  right: 28px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.theme-toggle:hover,
.reveal:hover {
  background: var(--rl-raised);
}
.login-card {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 24px;
  width: 100%;
  max-width: 400px;
  padding: 32px 28px 28px;
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  background: var(--rl-surface);
}
.login-brand {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.login-brand :deep(.brand) {
  align-self: flex-start;
}
.login-title {
  margin: 0;
  font-size: 20px;
  line-height: 28px;
  font-weight: 600;
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--rl-muted);
}
.field-wrap {
  position: relative;
}
.field-input {
  box-sizing: border-box;
  width: 100%;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-surface);
  color: var(--rl-fg);
  font: inherit;
  font-size: 14px;
}
.field-input--secret {
  padding-right: 44px;
}
.field-input--invalid {
  border-color: var(--rl-bad);
}
.reveal {
  position: absolute;
  top: 3px;
  right: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.login-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--rl-bad-bg);
  color: var(--rl-bad);
  font-size: 13px;
  font-weight: 500;
}
.login-error__icon {
  flex: none;
}
.submit {
  width: 100%;
  height: 40px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  line-height: normal;
  cursor: pointer;
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
}

/* Phone: the card fills the width, 48 px fields at 16 px (no iOS zoom), 44 px touch targets. */
@media (max-width: 767px) {
  .login-page {
    width: 100%;
    padding: 72px 16px 48px;
  }
  .theme-toggle {
    top: 12px;
    right: 16px;
    width: 44px;
    height: 44px;
  }
  .login-card {
    max-width: none;
    padding: 24px 20px 20px;
  }
  .login-title {
    font-size: 22px;
    line-height: 30px;
  }
  .field-input {
    height: 48px;
    font-size: 16px;
  }
  .field-input--secret {
    padding-right: 52px;
  }
  .reveal {
    top: 2px;
    right: 2px;
    width: 44px;
    height: 44px;
  }
  .login-error {
    padding: 12px;
  }
  .submit {
    height: 48px;
  }
}
</style>
