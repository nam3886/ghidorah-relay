<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { changePassword } from "@/api/settings";
import { IconAlertCircleShort } from "@/components/shared/icons";
import SettingsCard from "./SettingsCard.vue";
import { newPasswordProblem } from "./settings-value";
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
  fieldErrors.old_password = "";
  // the length rule is checked here, before anything is sent
  fieldErrors.new_password = newPasswordProblem(newPassword.value);
  if (fieldErrors.new_password) return;
  busy.value = true;
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
  <SettingsCard
    id="password"
    title="Change password"
    note="Every session ends, this one included: you sign in again."
    pad
  >
    <form class="password__form" @submit.prevent="submit">
      <div class="password__field">
        <label for="old-password">Current password</label>
        <input
          id="old-password"
          v-model="oldPassword"
          type="password"
          autocomplete="current-password"
          :aria-invalid="fieldErrors.old_password !== ''"
          :aria-describedby="fieldErrors.old_password ? 'old-password-error' : undefined"
          :class="{ 'is-invalid': fieldErrors.old_password !== '' }"
        />
        <span v-if="fieldErrors.old_password" id="old-password-error" class="password__error" role="alert"
          ><IconAlertCircleShort :size="14" />{{ fieldErrors.old_password }}</span
        >
      </div>
      <div class="password__field">
        <label for="new-password">New password</label>
        <input
          id="new-password"
          v-model="newPassword"
          type="password"
          autocomplete="new-password"
          :aria-invalid="fieldErrors.new_password !== ''"
          :aria-describedby="fieldErrors.new_password ? 'new-password-error' : undefined"
          :class="{ 'is-invalid': fieldErrors.new_password !== '' }"
        />
        <span v-if="fieldErrors.new_password" id="new-password-error" class="password__error" role="alert"
          ><IconAlertCircleShort :size="14" />{{ fieldErrors.new_password }}</span
        >
      </div>
      <div class="password__act">
        <button type="submit" class="password__save" :disabled="!ready || busy" aria-describedby="password-ends">
          Save
        </button>
        <span id="password-ends" class="sr-only">Every session ends, this one included: you sign in again.</span>
      </div>
    </form>
  </SettingsCard>
</template>

<style scoped>
.password__form {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr));
  align-items: start;
  gap: 16px;
  margin: 0;
}
.password__field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.password__field label {
  font-size: 13px;
  font-weight: 700;
}
.password__field input {
  box-sizing: border-box;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: var(--rl-input);
  color: var(--rl-fg);
  font: inherit;
  font-size: 13px;
}
.password__field input:focus-visible {
  outline: none;
  border-color: var(--rl-brand);
  box-shadow: 0 0 0 3px var(--rl-focus-halo);
}
.password__field input.is-invalid {
  border-color: var(--rl-bad);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--rl-bad) 12%, transparent);
}
.password__error {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: 18px;
  font-weight: 600;
  color: var(--rl-bad);
}
/* the button sits level with the inputs on their row, under their labels; alone on a row of its own it has no gap */
.password__act {
  align-self: end;
}
.password__save {
  height: 40px;
  padding: 0 20px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
.password__save:hover:not(:disabled) {
  background: var(--rl-primary-hover);
}
.password__save:disabled {
  background: var(--rl-track);
  color: var(--rl-faint);
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
@media (max-width: 767px) {
  .password__field input,
  .password__save {
    height: 44px;
  }
  .password__save {
    width: 100%;
  }
}
</style>
