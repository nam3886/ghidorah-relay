<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { ApiError } from "@/api/client";
import BrandLogo from "@/components/layout/BrandLogo.vue";
import { getMe } from "@/api/shell";
import { errorText } from "@/lib/errors";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();

const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");

async function submit(): Promise<void> {
  busy.value = true;
  error.value = "";
  try {
    await auth.login(username.value, password.value);
    await router.replace({ name: "jobs" });
  } catch (err) {
    error.value = err instanceof ApiError && err.status === 401 ? "Wrong username or password" : errorText(err);
  } finally {
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
  <main class="login-page">
    <el-card class="login-card" shadow="never">
      <BrandLogo variant="login" />
      <h1 class="login-brand">Sign in to Ghidorah Relay</h1>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="Username">
          <el-input v-model="username" size="large" name="username" autocomplete="username" :maxlength="64" autofocus />
        </el-form-item>
        <el-form-item label="Password">
          <el-input
            v-model="password"
            size="large"
            name="password"
            type="password"
            autocomplete="current-password"
            :maxlength="256"
            show-password
          />
        </el-form-item>
        <!-- the slot keeps its height, so a failed sign-in does not push the button down -->
        <div class="login-error-slot">
          <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" role="alert" />
        </div>
        <el-button type="primary" size="large" native-type="submit" :loading="busy" class="login-submit">
          {{ busy ? "Signing in..." : "Sign in" }}
        </el-button>
      </el-form>
    </el-card>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}
.login-card {
  width: 360px;
  max-width: 100%;
  border-color: var(--rl-line-strong);
}
.login-brand {
  margin: 0 0 16px;
  font-size: 20px;
  font-weight: 600;
}
.login-error-slot {
  min-height: 44px;
}
.login-submit {
  width: 100%;
}
</style>
