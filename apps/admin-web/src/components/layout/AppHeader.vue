<script setup lang="ts">
import { Moon, Sunny } from "@element-plus/icons-vue";
import { computed } from "vue";
import { useRouter } from "vue-router";
import { useBusy } from "@/composables/use-busy";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";
import { useUiStore } from "@/stores/ui";

const auth = useAuthStore();
const scope = useScopeStore();
const ui = useUiStore();
const router = useRouter();
const { busy, run } = useBusy();

const isDark = computed(() => ui.theme === "dark");

// Element Plus shows the placeholder, not the option label, for a value of "": the select holds a sentinel for All and
// the store keeps its "" (every view reads that as "no provider").
const ALL = "__all__";
const providerChoice = computed({
  get: () => scope.provider || ALL,
  set: (value: string) => {
    scope.provider = value === ALL ? "" : value;
  },
});

async function signOut(): Promise<void> {
  await run(async () => {
    await auth.logout();
    await router.replace({ name: "login" });
  });
}
</script>

<template>
  <header class="app-header">
    <span class="brand">relay</span>
    <div class="header-end">
      <div class="scope" :class="{ 'scope--active': scope.provider !== '' }">
        <span class="scope-label">Provider</span>
        <el-select
          v-model="providerChoice"
          class="scope-select"
          size="small"
          aria-label="Provider filter"
          data-testid="provider-filter"
        >
          <el-option label="All" :value="ALL" />
          <el-option v-for="name in scope.providers" :key="name" :label="name" :value="name" />
        </el-select>
      </div>
      <span class="whoami" data-testid="whoami">{{ auth.user }}</span>
      <el-button
        :icon="isDark ? Sunny : Moon"
        circle
        size="small"
        :aria-label="isDark ? 'Switch to light theme' : 'Switch to dark theme'"
        data-testid="theme-toggle"
        @click="ui.toggleTheme()"
      />
      <el-link href="/reference" target="_blank" rel="noopener" underline="never">API docs</el-link>
      <el-button size="small" :loading="busy" data-testid="sign-out" @click="signOut">Sign out</el-button>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 8px 16px;
  background: var(--rl-surface);
  border-bottom: 1px solid var(--rl-line);
}
.brand {
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.01em;
}
.header-end {
  display: flex;
  align-items: center;
  gap: 12px;
}
.scope {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 6px 2px 8px;
  border: 1px solid transparent;
  border-radius: var(--el-border-radius-base);
}
.scope--active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.scope-label {
  font-size: 12px;
  color: var(--rl-muted);
}
.scope-select {
  width: 150px;
}
.whoami {
  font-size: 13px;
  color: var(--rl-muted);
}
</style>
