<script setup lang="ts">
import { ref } from "vue";
import { useBreakpoint } from "@/composables/use-breakpoint";
import { MAX_BYTES, MAX_IMAGES, type StoredFile } from "@/lib/test-files";
import { sizeText } from "./test-result-model";

// The attached images: a drop zone (images only, nothing is ever uploaded), the NAMES of what is attached and the note
// about what could not be kept. There is no thumbnail on purpose: a preview of a file that is not uploaded is a blob:
// URL, which the page's CSP blocks (it has no img-src for it). The limits and the storage are the parent's
// (lib/test-files.ts); this only reports the choice and shows what the parent kept.
defineProps<{ files: StoredFile[]; note: string }>();
const emit = defineEmits<{ add: [files: File[]]; remove: [index: number] }>();

const { isPhone } = useBreakpoint();
const input = ref<HTMLInputElement | null>(null);
const over = ref(false);

function picked(event: Event): void {
  const target = event.target as HTMLInputElement;
  if (target.files?.length) emit("add", Array.from(target.files));
  // the same file can be chosen again after a Remove
  target.value = "";
}

function dropped(event: DragEvent): void {
  over.value = false;
  const list = event.dataTransfer?.files;
  if (list?.length) emit("add", Array.from(list));
}

const HINT = `Images only · up to ${MAX_IMAGES} files · ${MAX_BYTES / 1048576} MiB`;
</script>

<template>
  <div class="images">
    <span id="attach-label" class="images__label">Image attachments</span>
    <div
      role="group"
      aria-labelledby="attach-label"
      class="images__zone"
      :class="{ 'is-over': over }"
      @dragover.prevent="over = true"
      @dragleave="over = false"
      @drop.prevent="dropped"
    >
      <svg
        width="22"
        height="22"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M12 16V4" />
        <path d="m7 9 5-5 5 5" />
        <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
      </svg>
      <span class="images__text"
        ><span v-if="!isPhone">Drag to upload, or </span
        ><button type="button" class="images__browse" @click="input?.click()">
          {{ isPhone ? "Browse images" : "browse" }}
        </button></span
      >
      <span class="images__hint">{{ HINT }}</span>
      <input
        ref="input"
        type="file"
        accept="image/*"
        multiple
        class="images__input"
        data-testid="attach-input"
        tabindex="-1"
        aria-hidden="true"
        @change="picked"
      />
    </div>
    <ul v-if="files.length" class="images__list" aria-label="Attached images">
      <li v-for="(f, i) in files" :key="`${i}-${f.name}`" data-testid="attachment">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <circle cx="9" cy="9" r="2" />
          <path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21" />
        </svg>
        <!-- the phone stacks the name over the size -->
        <span class="images__meta">
          <span class="images__name">{{ f.name }}</span>
          <span class="images__size">{{ sizeText(f.blob.size) }}</span>
        </span>
        <button type="button" class="images__remove" :aria-label="`Remove ${f.name}`" @click="emit('remove', i)">
          <svg
            :width="isPhone ? 16 : 14"
            :height="isPhone ? 16 : 14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </li>
    </ul>
    <p v-if="note" class="images__note" role="status" data-testid="attach-note">{{ note }}</p>
  </div>
</template>

<style scoped>
.images {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.images__label {
  font-size: 12px;
  font-weight: 500;
  color: var(--rl-muted);
}
.images__zone {
  position: relative;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 16px;
  border: 1px dashed var(--rl-control);
  border-radius: 12px;
  background: var(--rl-raised);
  color: var(--rl-muted);
  text-align: center;
}
.images__zone.is-over {
  border-color: var(--rl-primary);
}
.images__text {
  font-size: 13px;
  color: var(--rl-fg);
}
.images__browse {
  padding: 0;
  border: 0;
  background: none;
  color: var(--rl-link, var(--rl-primary));
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
.images__hint {
  font-size: 12px;
  color: var(--rl-faint);
}
.images__input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
.images__list {
  display: flex;
  flex-direction: column;
  margin: 0;
  padding: 0;
  list-style: none;
}
.images__list li {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  border-bottom: 1px solid var(--rl-line);
  color: var(--rl-muted);
}
/* the name and the size are on the row itself; the phone stacks them */
.images__meta {
  display: contents;
}
.images__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  color: var(--rl-fg);
  white-space: nowrap;
  text-overflow: ellipsis;
}
.images__size {
  font-family: var(--rl-mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--rl-faint);
}
.images__remove {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.images__remove:hover {
  background: var(--rl-raised);
}
.images__note {
  margin: 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-size: 12px;
}

@media (min-width: 768px) and (max-width: 1279px) {
  .images__list li {
    height: 44px;
  }
  .images__remove {
    width: 36px;
    height: 36px;
  }
}
@media (max-width: 767px) {
  .images__browse {
    display: flex;
    align-items: center;
    min-height: 44px;
    font-size: 13px;
  }
  .images__list li {
    min-height: 52px;
    height: auto;
  }
  .images__list svg {
    flex: none;
  }
  .images__meta {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .images__remove {
    width: 44px;
    height: 44px;
    border-radius: 10px;
  }
}
</style>
