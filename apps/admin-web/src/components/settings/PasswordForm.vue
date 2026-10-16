<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { changePassword } from "@/api/settings";
import { ApiError } from "@/api/client";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";

// The server ends every session on success, this one included, so the app signs out here and lands on login. A refusal
// that names a field (`old_password`, `new_password`) is shown on that field; anything else is a toast.
const auth = useAuthStore();
const router = useRouter();

const oldPassword = ref("");
const newPassword = ref("");
const fieldErrors = reactive<{ old_password: string; new_password: string }>({ old_password: "", new_password: "" });
const busy = ref(false);
const ready = computed(() => oldPassword.value !== "" && newPassword.value !== "");

async function submit(): Promise<void> {
  if (!ready.value || busy.value) return;
  busy.value = true;
  fieldErrors.old_password = "";
  fieldErrors.new_password = "";
  try {
    await changePassword(oldPassword.value, newPassword.value);
  } catch (err) {
    if (err instanceof ApiError && (err.param === "old_password" || err.param === "new_password")) {
      fieldErrors[err.param] = err.message;
    } else {
      notify.error(errorText(err));
    }
    busy.value = false;
    return;
  }
  oldPassword.value = "";
  newPassword.value = "";
  busy.value = false;
  auth.signOut();
  await router.replace({ name: "login" });
}
</script>

<template>
  <section id="password" class="password" aria-labelledby="password-title">
    <h2 id="password-title">Change password</h2>
    <form class="password__form" @submit.prevent="submit">
      <div class="password__fields">
        <div class="password__field">
          <label for="old-password">Current password</label>
          <input
            id="old-password"
            v-model="oldPassword"
            type="password"
            autocomplete="current-password"
            :aria-invalid="fieldErrors.old_password !== ''"
            :class="{ 'is-invalid': fieldErrors.old_password !== '' }"
          />
          <p v-if="fieldErrors.old_password" class="password__error" role="alert">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v4" />
              <path d="M12 16h.01" />
            </svg>
            {{ fieldErrors.old_password }}
          </p>
        </div>
        <div class="password__field">
          <label for="new-password">New password</label>
          <input
            id="new-password"
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            :aria-invalid="fieldErrors.new_password !== ''"
            :class="{ 'is-invalid': fieldErrors.new_password !== '' }"
          />
          <p v-if="fieldErrors.new_password" class="password__error" role="alert">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="12" r="9" />
              <path d="M12 8v4" />
              <path d="M12 16h.01" />
            </svg>
            {{ fieldErrors.new_password }}
          </p>
        </div>
      </div>
      <div>
        <button type="submit" class="password__save" :disabled="!ready || busy" aria-describedby="password-ends">
          Save
        </button>
        <span id="password-ends" class="sr-only">Every session ends, this one included: you sign in again.</span>
      </div>
    </form>
  </section>
</template>

<style scoped>
.password {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: 16px;
  scroll-margin-top: 16px;
}
.password h2 {
  margin: 0;
  font-size: 15px;
  line-height: 22px;
  font-weight: 600;
}
.password__form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.password__fields {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  max-width: 720px;
}
.password__field {
  flex: 1 1 280px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.password__field label {
  font-size: 13px;
  font-weight: 500;
}
.password__field input {
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
.password__field input.is-invalid {
  border-color: var(--rl-bad);
}
.password__error {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--rl-bad);
}
.password__save {
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: #ffffff;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.password__save:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.password__save:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
@media (min-width: 768px) and (max-width: 1279px) {
  .password__fields {
    align-items: flex-start;
    max-width: none;
  }
}
@media (max-width: 767px) {
  .password {
    padding: 16px;
  }
  .password__fields {
    flex-direction: column;
    max-width: none;
  }
  .password__field {
    flex: none;
  }
  .password__field input {
    height: 44px;
  }
  .password__save {
    width: 100%;
    height: 44px;
    font-size: 14px;
  }
}
</style>
