<script setup lang="ts">
import { ref } from "vue";
import { MAX_BYTES, MAX_IMAGES, type StoredFile } from "@/lib/test-files";
import { sizeText } from "./test-result-model";

// The attached images (Test.dc.html, Reference images): one tile per file (a picture glyph, the name and the size, a
// round remove button on its corner) and the dashed Add image tile; a file dropped on the block attaches too. Nothing
// is ever uploaded. There is no thumbnail, as the canvas draws none. The limits and the storage are the parent's (lib/test-files.ts);
// this only reports the choice and shows what the parent kept.
defineProps<{ files: StoredFile[]; note: string }>();
const emit = defineEmits<{ add: [files: File[]]; remove: [index: number] }>();

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

const HINT = `Images only · up to ${MAX_IMAGES} files · ${MAX_BYTES / 1048576} MiB in all · makes it image to video`;
</script>

<template>
  <div
    class="images"
    :class="{ 'is-over': over }"
    @dragover.prevent="over = true"
    @dragleave="over = false"
    @drop.prevent="dropped"
  >
    <div class="images__head">
      <span id="attach-label" class="images__label">Reference images</span>
      <span class="images__hint">{{ HINT }}</span>
    </div>
    <ul class="images__list" role="group" aria-labelledby="attach-label">
      <li v-for="(f, i) in files" :key="`${i}-${f.name}`" class="images__tile" data-testid="attachment">
        <span class="images__art" aria-hidden="true">
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <circle cx="9" cy="9" r="2" />
            <path d="m21 15-5-5L5 21" />
          </svg>
        </span>
        <span class="images__name mono" :title="f.name">{{ f.name }}</span>
        <span class="images__size num">{{ sizeText(f.blob.size) }}</span>
        <button type="button" class="images__remove" :aria-label="`Remove ${f.name}`" @click="emit('remove', i)">
          <span class="images__x"
            ><svg
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.6"
              stroke-linecap="round"
              aria-hidden="true"
            >
              <path d="M18 6 6 18M6 6l12 12" /></svg
          ></span>
        </button>
      </li>
      <li class="images__addcell">
        <button type="button" class="images__add" @click="input?.click()">
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            aria-hidden="true"
          >
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add image
        </button>
      </li>
    </ul>
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
    <p v-if="note" class="images__note" role="status" data-testid="attach-note">{{ note }}</p>
  </div>
</template>

<style scoped>
.images {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.images.is-over .images__add {
  border-style: solid;
  background: var(--rl-primary-soft);
}
.images__head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 4px 12px;
}
.images__label {
  font-size: 13px;
  font-weight: 700;
}
.images__hint {
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
.images__list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.images__tile {
  position: relative;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 132px;
  padding: 6px 6px 8px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 12px;
  background: var(--rl-surface);
}
.images__art {
  display: grid;
  place-items: center;
  height: 72px;
  border-radius: 8px;
  background: var(--rl-primary-soft);
  color: var(--rl-brand-ink);
}
.images__name {
  padding: 4px 2px 0;
  overflow: hidden;
  font-size: 11px;
  line-height: 16px;
  color: var(--rl-body);
  white-space: nowrap;
  text-overflow: ellipsis;
}
.images__size {
  padding: 0 2px;
  font-size: 12px;
  line-height: 18px;
  color: var(--rl-faint);
}
/* a 44 px target on the tile's corner, drawn as a 28 px circle */
.images__remove {
  position: absolute;
  top: -18px;
  right: -18px;
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--rl-muted);
  cursor: pointer;
}
.images__x {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--rl-line-strong);
  border-radius: 50%;
  background: var(--rl-surface);
}
.images__remove:hover {
  color: var(--rl-fg);
}
.images__add {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 132px;
  min-height: 128px;
  border: 1.5px dashed #c9b8ff;
  border-radius: 12px;
  background: transparent;
  color: var(--rl-brand-ink);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}
:global(html.dark) .images__add {
  border-color: #4a3a80;
}
.images__input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
.images__note {
  margin: 0;
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--rl-warn-bg);
  color: var(--rl-warn);
  font-size: 12px;
}
/* a phone: two tiles to a row */
@media (max-width: 767px) {
  .images__tile,
  .images__add {
    width: calc(50% - 5px);
  }
  .images__addcell {
    display: contents;
  }
}
</style>
