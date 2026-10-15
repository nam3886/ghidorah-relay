<script setup lang="ts">
import { Moon, Sunny, TopRight } from "@element-plus/icons-vue";
import { computed } from "vue";
import { useRouter } from "vue-router";
import BrandLogo from "@/components/layout/BrandLogo.vue";
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
    <BrandLogo />
    <div class="header-end">
      <div class="scope" :class="{ 'scope--active': scope.provider !== '' }">
        <span class="scope-label">Provider</span>
        <el-select
          v-model="providerChoice"
          class="scope-select"
          aria-label="Provider filter"
          data-testid="provider-filter"
        >
          <el-option label="All" :value="ALL" />
          <el-option v-for="name in scope.providers" :key="name" :label="name" :value="name" />
        </el-select>
      </div>
      <div class="user">
        <span class="whoami" data-testid="whoami">{{ auth.user }}</span>
        <el-button
          :icon="isDark ? Sunny : Moon"
          circle
          :title="isDark ? 'Switch to light theme' : 'Switch to dark theme'"
          :aria-label="isDark ? 'Switch to light theme' : 'Switch to dark theme'"
          data-testid="theme-toggle"
          @click="ui.toggleTheme()"
        />
      </div>
      <el-link href="/reference" target="_blank" rel="noopener" underline="never" :icon="TopRight">API docs</el-link>
      <el-button :loading="busy" data-testid="sign-out" @click="signOut">Sign out</el-button>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 16px;
  padding: 8px 16px;
  background: var(--rl-surface);
  border-bottom: 1px solid var(--rl-line);
}
.user {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.header-end {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
@media (max-width: 639px) {
  .scope-select {
    width: 120px;
  }
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
