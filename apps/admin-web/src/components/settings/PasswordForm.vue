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
  <section class="password-form">
    <h3>Change password</h3>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="Current password" :error="fieldErrors.old_password">
        <el-input v-model="oldPassword" type="password" autocomplete="current-password" />
      </el-form-item>
      <el-form-item label="New password" :error="fieldErrors.new_password">
        <el-input v-model="newPassword" type="password" autocomplete="new-password" />
      </el-form-item>
      <el-button type="primary" native-type="submit" :loading="busy" :disabled="!ready">Change password</el-button>
    </el-form>
    <p class="password-form__note">Every session ends, this one included: you sign in again.</p>
  </section>
</template>

<style scoped>
.password-form {
  max-width: 420px;
  padding: 16px;
  background: var(--rl-surface);
  border: 1px solid var(--rl-line);
  border-radius: var(--el-border-radius-base, 6px);
}
.password-form h3 {
  margin: 0 0 12px;
  font-size: 16px;
}
.password-form__note {
  margin: 12px 0 0;
  color: var(--rl-muted);
  font-size: 12px;
}
</style>
