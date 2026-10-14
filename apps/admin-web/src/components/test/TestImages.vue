<script setup lang="ts">
import { LIMITS_TEXT, MAX_BYTES, MAX_IMAGES, type StoredFile } from "@/lib/test-files";

// The attached images: a native multi-file input (images only), the list with a Remove each, and the note about what
// could not be kept. The limits and the storage are the parent's (lib/test-files.ts); this only reports the choice.
defineProps<{ files: StoredFile[]; note: string }>();
const emit = defineEmits<{ add: [files: File[]]; remove: [index: number] }>();

function onPick(event: Event) {
  const el = event.target as HTMLInputElement;
  emit("add", Array.from(el.files ?? []));
  // The same file can be picked again after a Remove.
  el.value = "";
}
</script>

<template>
  <div class="test-images">
    <label class="test-images__label" for="test-image-files">
      Attach images (up to {{ MAX_IMAGES }} files, {{ MAX_BYTES / 1048576 }} MiB together; sent as data)
    </label>
    <input id="test-image-files" type="file" accept="image/*" multiple :title="LIMITS_TEXT" @change="onPick" />
    <ul v-if="files.length" class="test-images__list">
      <li v-for="(f, i) in files" :key="`${i}-${f.name}`">
        <span class="test-images__name">{{ f.name }}</span>
        <span class="test-images__size">{{ Math.ceil(f.blob.size / 1024) }} KiB</span>
        <el-button size="small" text type="danger" :aria-label="`Remove ${f.name}`" @click="emit('remove', i)"
          >Remove</el-button
        >
      </li>
    </ul>
    <p v-if="note" class="test-images__note" role="status">{{ note }}</p>
  </div>
</template>

<style scoped>
.test-images {
  display: grid;
  gap: 6px;
}
.test-images__label {
  color: var(--el-text-color-regular);
  font-size: 13px;
}
.test-images__list {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.test-images__list li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.test-images__name {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.test-images__size {
  color: var(--rl-muted);
  font-variant-numeric: tabular-nums;
}
.test-images__note {
  margin: 0;
  color: var(--rl-warn);
  font-size: 12px;
}
</style>
