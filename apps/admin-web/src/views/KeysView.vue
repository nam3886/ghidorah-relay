<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, useTemplateRef } from "vue";
import {
  type ApiKey,
  createKey,
  deleteKey,
  type KeyBody,
  listKeys,
  patchKey,
  setKeyEnabled,
  type WalletBody,
} from "@/api/keys";
import type { CatalogModel } from "@/api/shell";
import KeyFormDialog from "@/components/keys/KeyFormDialog.vue";
import KeySecretModal from "@/components/keys/KeySecretModal.vue";
import KeysList from "@/components/keys/KeysList.vue";
import KeyWalletAdjust from "@/components/keys/KeyWalletAdjust.vue";
import {
  type CreateError,
  createError,
  deleteText,
  deleteToast,
  inFilter,
  inSearch,
  KEY_FILTERS,
  type KeyFilter,
  keyName,
  loadFailedText,
  summaryText,
} from "@/components/keys/key-model";
import ArrowScroller from "@/components/shared/ArrowScroller.vue";
import IconButton from "@/components/shared/IconButton.vue";
import { IconSearch } from "@/components/shared/icons";
import { confirmDialog } from "@/components/shared/confirm-dialog";
import { useRouteLeaveGuard } from "@/components/shared/use-leave-guard";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { useBusy } from "@/composables/use-busy";
import { useLatest } from "@/composables/use-latest";
import { errorText } from "@/lib/errors";
import { notify } from "@/lib/notify";
import { useAuthStore } from "@/stores/auth";
import { useScopeStore } from "@/stores/scope";

// API keys (Keys, KeysTablet, KeysPhone, KeysStates). The list answers whole (a deployment has a handful of keys), so
// there is no pager: the Show chips and the search filter it here, in the page. The page has no provider filter (Keys
// draws no provider strip), so a provider picked on another tab does not narrow it.
const scope = useScopeStore();
const auth = useAuthStore();
const { busy, run } = useBusy();
const latest = useLatest();
const { isPhone, isTablet } = useBreakpoint();
const compact = computed(() => isPhone.value || isTablet.value);

const items = shallowRef<ApiKey[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

const formOpen = ref(false);
const editing = shallowRef<ApiKey | null>(null);
const catalog = shallowRef<CatalogModel[]>([]);
const formError = ref<CreateError | null>(null);
// something typed in the open form that a leave would lose
const formDirty = ref(false);
// a create in flight keeps the page: its one-time key must still be shown
const creating = ref(false);
useRouteLeaveGuard(
  () => formOpen.value && formDirty.value,
  () => creating.value,
);

// The raw key of a key just created. It lives only here: not in a store, not in storage, not in the address, not in a
// log. It goes on Done, when the operator leaves the tab (this view unmounts) and on sign-out. The id and the label
// beside it are not secret: they tint the new row and name the modal.
const rawKey = ref<string | null>(null);
const createdId = ref<string | null>(null);
const createdLabel = ref("");
const secret = useTemplateRef<InstanceType<typeof KeySecretModal>>("secret");
// set by a create that worked: once the drawer is gone the modal's field takes focus (focus would otherwise go back
// to the button that opened the form)
let revealSecret = false;
// `disposed`: the operator left or signed out, so a create that is still in flight must not bring the key back.
// `signedOut`: the session is gone, which says so itself: a create that fails after it adds no toast.
let disposed = false;
let signedOut = false;
const forgetSignOut = auth.onSignOut(() => {
  disposed = true;
  signedOut = true;
  rawKey.value = null;
  createdId.value = null;
});
onBeforeUnmount(() => {
  disposed = true;
  rawKey.value = null;
  forgetSignOut();
  latest.invalidate();
});

// The catalog names each model's provider and whether it is paused (one read, shared with the form).
scope.loadCatalog().catch((err) => notify.error(errorText(err)));
const known = computed(() => scope.models ?? []);

// ---- the filters: the Show chips and the search ----------------------------------------------------------------
const show = ref<KeyFilter>("all");
const q = ref("");
const shown = computed(() => items.value.filter((k) => inFilter(k, show.value) && inSearch(k, q.value)));
const counts = computed(() =>
  Object.fromEntries(KEY_FILTERS.map((f) => [f.value, items.value.filter((k) => inFilter(k, f.value)).length])),
);
const filtered = computed(() => items.value.length > 0);
const emptyText = "No key matches these filters";
function clearFilters(): void {
  show.value = "all";
  q.value = "";
}
// A tablet or phone folds the search behind a button of the head.
const searchOpen = ref(false);
const showSearch = computed(() => !compact.value || searchOpen.value || !!q.value.trim());
async function toggleSearch(): Promise<void> {
  searchOpen.value = !searchOpen.value;
  if (!searchOpen.value) return;
  await nextTick();
  document.getElementById("keys-q")?.focus();
}

// "6 keys, 4 used today." in bold, the rest after it
const summary = computed(() => {
  const text = summaryText(items.value, isPhone.value);
  const cut = text.indexOf(". ");
  return cut < 0 ? { head: text, rest: "" } : { head: text.slice(0, cut + 1), rest: text.slice(cut + 2) };
});

async function load(): Promise<void> {
  const ticket = latest.next();
  loading.value = true;
  try {
    const out = await listKeys();
    if (!latest.isCurrent(ticket)) return;
    items.value = out.items;
    error.value = null;
  } catch (err) {
    if (!latest.isCurrent(ticket)) return;
    error.value = loadFailedText(err, errorText(err));
  } finally {
    if (latest.isCurrent(ticket)) loading.value = false;
  }
}
void load();

// The catalog (one read, shared) feeds the model list and the wallet rows of the form.
async function openForm(key: ApiKey | null) {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  walletKey.value = null;
  await run(async () => {
    catalog.value = await scope.loadCatalog();
    editing.value = key;
    formError.value = null;
    formOpen.value = true;
  });
}

async function submit(body: KeyBody) {
  const key = editing.value;
  creating.value = key === null;
  await run(async () => {
    formError.value = null;
    if (key) {
      await patchKey(key.id, body);
    } else {
      // Only the key text, its id, its prefix and its label are kept from the answer; the rest is the list row the
      // reload brings.
      let created: { key: string; id: string; label: string; prefix: string };
      try {
        const out = await createKey(body);
        created = { key: out.key, id: out.id, label: out.label, prefix: out.prefix };
        creating.value = false;
      } catch (err) {
        creating.value = false;
        // Shown from a fixed table, never from the server's message (see createError): inside the form while it is
        // open, as a toast when the form is already gone (the operator left the page meanwhile).
        const shown = createError(err);
        if (formOpen.value && !disposed) formError.value = shown;
        else if (!signedOut) notify.error(shown.text);
        return;
      }
      if (!disposed) {
        rawKey.value = created.key;
        createdId.value = created.id;
        createdLabel.value = keyName(created);
        revealSecret = true;
      }
    }
    formOpen.value = false;
    await load();
  });
}

// The list is read again even when the change failed, so the row goes back to what the server holds.
async function toggle(k: ApiKey, enabled: boolean) {
  await run(async () => {
    try {
      await setKeyEnabled(k.id, enabled);
    } finally {
      await load();
    }
  });
}

async function remove(k: ApiKey) {
  const ok = await confirmDialog({
    title: `Delete key ${keyName(k)}?`,
    message: deleteText(k),
    confirmText: "Delete",
    danger: true,
  });
  if (!ok) return;
  await run(async () => {
    const out = await deleteKey(k.id);
    await load();
    notify.info(deleteToast(out.result));
  });
}

// ---- Adjust wallet --------------------------------------------------------------------------------------------
const walletKey = shallowRef<ApiKey | null>(null);
const walletAnchor = shallowRef<HTMLElement | null>(null);
function openWallet(k: ApiKey, anchor: HTMLElement): void {
  if (walletKey.value?.id === k.id) {
    walletKey.value = null;
    return;
  }
  walletAnchor.value = anchor;
  walletKey.value = k;
}
// The change is the points wallet alone; the list is read again whatever the answer, so the figure is the server's.
async function adjust(body: WalletBody): Promise<void> {
  const k = walletKey.value;
  if (!k) return;
  await run(async () => {
    try {
      await patchKey(k.id, { wallets: [body] });
      walletKey.value = null;
      notify.success(`Wallet of ${keyName(k)} changed`);
    } finally {
      await load();
    }
  });
}

// The drawer is gone and the button that opened it has the focus back. After a create the modal's field takes it
// instead; and when that button no longer exists (an edited row is drawn again by the reload) focus goes to the
// page's primary button, else to the page's scroll area, so it is never left on the document.
let opener: HTMLElement | null = null;
const newKeyButton = useTemplateRef<HTMLButtonElement>("newKey");
function formClosed(): void {
  if (revealSecret) {
    revealSecret = false;
    secret.value?.reveal();
    return;
  }
  if (opener?.isConnected && opener !== document.body) return;
  const primary = newKeyButton.value;
  if (primary && !primary.disabled) primary.focus();
  else document.querySelector<HTMLElement>(".app-scroll__wrap")?.focus({ preventScroll: true });
}
</script>

<template>
  <section class="keys-view" :class="{ 'is-compact': compact, 'is-phone': isPhone }">
    <div class="kv-head">
      <div v-if="!compact" class="kv-head__text">
        <h1>API keys</h1>
        <p class="kv-sum" data-testid="keys-summary">
          <b>{{ summary.head }}</b> {{ summary.rest }}
        </p>
      </div>
      <h1 v-else class="sr-only">API keys</h1>
      <span class="kv-head__actions">
        <IconButton v-if="compact" icon="search" label="Search keys" placement="bottom" @click="toggleSearch" />
        <IconButton
          v-if="isPhone"
          icon="plus"
          label="New key"
          placement="bottom-end"
          :disabled="busy"
          @click="openForm(null)"
        />
        <button v-else ref="newKey" type="button" class="kv-primary" :disabled="busy" @click="openForm(null)">
          {{ isTablet ? "New key" : "Create key" }}
        </button>
      </span>
    </div>
    <p v-if="compact" class="kv-sum kv-sum--compact" data-testid="keys-summary">
      {{ summary.head }} {{ summary.rest }}
    </p>

    <ArrowScroller v-if="isPhone" class="kv-chips" label="Show" :count="KEY_FILTERS.length" :active-key="show">
      <button
        v-for="f in KEY_FILTERS"
        :key="f.value"
        type="button"
        class="kchip"
        :class="{ 'is-on': show === f.value }"
        :aria-pressed="show === f.value"
        @click="show = f.value"
      >
        {{ f.label }}<span class="num kchip__n">{{ compact ? " · " : " " }}{{ counts[f.value] }}</span>
      </button>
    </ArrowScroller>
    <label v-if="isPhone && showSearch" class="kv-search is-row">
      <IconSearch :size="15" />
      <input id="keys-q" v-model="q" type="search" placeholder="Search name or key prefix" aria-label="Search keys" />
    </label>

    <section class="kv-card" aria-label="Key list">
      <div v-if="!isPhone" class="kv-toolbar">
        <h2 v-if="isTablet" class="kv-cardtitle">Keys</h2>
        <ArrowScroller class="kv-chips" label="Show" :count="KEY_FILTERS.length" :active-key="show">
          <button
            v-for="f in KEY_FILTERS"
            :key="f.value"
            type="button"
            class="kchip"
            :class="{ 'is-on': show === f.value }"
            :aria-pressed="show === f.value"
            @click="show = f.value"
          >
            {{ f.label }}<span class="num kchip__n">{{ compact ? " · " : " " }}{{ counts[f.value] }}</span>
          </button>
        </ArrowScroller>
        <label v-if="showSearch" class="kv-search" :class="{ 'is-row': compact }">
          <IconSearch :size="15" />
          <input
            id="keys-q"
            v-model="q"
            type="search"
            placeholder="Search name or key prefix"
            aria-label="Search keys"
          />
        </label>
      </div>
      <KeysList
        :rows="shown"
        :loading="loading"
        :error="error"
        :filtered="filtered"
        :empty-text="emptyText"
        :catalog="known"
        only=""
        :created-id="createdId"
        :wallet-id="walletKey?.id ?? null"
        :busy="busy"
        @edit="openForm"
        @wallet="openWallet"
        @toggle="toggle"
        @remove="remove"
        @retry="load"
        @clear-filters="clearFilters"
        @create="openForm(null)"
      />
      <p v-if="!compact && shown.length" class="kv-note">
        A chip = the key may use that provider · figure = today's points against its daily cap · amber = 80%+, red =
        full.
      </p>
    </section>
    <p v-if="compact && shown.length" class="kv-note kv-note--out">
      A chip = the key may use that provider · figure = today's points against its daily cap · amber = 80%+, red = full.
    </p>

    <KeyFormDialog
      v-model="formOpen"
      :editing="editing"
      :models="catalog"
      :busy="busy"
      :error="formError"
      @submit="submit"
      @update:dirty="(d: boolean) => (formDirty = d)"
      @closed="formClosed"
    />
    <KeyWalletAdjust
      :key-row="walletKey"
      :anchor="walletAnchor"
      :busy="busy"
      @submit="adjust"
      @close="walletKey = null"
    />
    <KeySecretModal
      v-if="rawKey !== null"
      ref="secret"
      :raw-key="rawKey"
      :label="createdLabel"
      @dismiss="rawKey = null"
    />
  </section>
</template>

<style scoped>
.keys-view {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.keys-view.is-compact {
  gap: 14px;
}
.num {
  font-variant-numeric: tabular-nums;
}

/* the head: the title and one sentence, the primary at the right */
.kv-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px 16px;
}
.kv-head__text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.kv-head h1 {
  margin: 0;
  font-size: 28px;
  line-height: 36px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.kv-sum {
  margin: 0;
  color: var(--rl-muted);
}
.kv-sum b {
  color: var(--rl-fg);
  font-weight: 700;
}
.kv-sum--compact {
  font-size: 13px;
  line-height: 1.55;
}
.kv-head__actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.is-compact .kv-head {
  justify-content: flex-end;
  gap: 0;
}
.is-compact .kv-head__actions {
  margin-bottom: -4px;
}
.kv-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  box-sizing: border-box;
  height: 36px;
  padding: 0 16px;
  border: 0;
  border-radius: 10px;
  background: var(--rl-primary);
  color: var(--rl-on-primary);
  font: inherit;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  box-shadow:
    0 1px 2px rgba(16, 24, 40, 0.12),
    inset 0 -1px 0 rgba(0, 0, 0, 0.12);
}
.kv-primary:hover {
  background: var(--rl-primary-hover);
}
.kv-primary:disabled {
  opacity: 0.5;
  cursor: default;
}
.kv-primary:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.is-compact .kv-primary {
  height: 40px;
  font-weight: 700;
}

/* the card: its toolbar (the Show chips and the search), the list, the note */
.kv-card {
  min-width: 0;
  overflow: hidden;
  border-radius: 16px;
  background: var(--rl-surface);
  box-shadow: var(--rl-shadow-card);
}
.kv-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--rl-rule);
}
.kv-cardtitle {
  margin: 0 0 4px;
  font-size: 15px;
  line-height: 22px;
  font-weight: 700;
}
.kv-toolbar .kv-chips {
  flex: 0 1 auto;
  min-width: 0;
}
.is-compact .kv-toolbar {
  flex-direction: column;
  align-items: stretch;
  gap: 4px;
  padding: 12px 16px 8px;
}
.kchip {
  display: inline-flex;
  flex: none;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--rl-line);
  border-radius: 999px;
  background: var(--rl-surface);
  color: var(--rl-muted);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
}
.kchip.is-on {
  border-color: var(--rl-brand);
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.kchip:focus-visible {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.kchip__n {
  opacity: 0.7;
}
.is-compact .kchip {
  height: 40px;
  padding: 0 14px;
}
.is-phone .kchip {
  height: 44px;
}
.kv-search {
  display: flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  width: 240px;
  max-width: 100%;
  height: 34px;
  margin-left: auto;
  padding: 0 12px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 10px;
  background: transparent;
  color: var(--rl-faint);
}
.kv-search:focus-within {
  outline: 2px solid var(--rl-focus);
  outline-offset: 2px;
}
.kv-search input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  background: transparent;
  color: var(--rl-fg);
  font: inherit;
}
.kv-search input::placeholder {
  color: var(--rl-faint);
}
.kv-search.is-row {
  width: auto;
  height: 44px;
  margin: 4px 0;
}
.kv-note {
  margin: 0;
  padding: 12px 20px;
  border-top: 1px solid var(--rl-rule);
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.kv-note--out {
  padding: 0;
  border: 0;
  color: var(--rl-muted);
}
</style>
