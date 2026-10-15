<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { UploadFilled } from "@element-plus/icons-vue";
import type { UploadFile, UploadUserFile } from "element-plus";
import { LIMITS_TEXT, MAX_BYTES, MAX_IMAGES, type StoredFile } from "@/lib/test-files";

// The attached images: a drag-to-upload area (images only, nothing is ever uploaded: `auto-upload` is off), the list of
// the attached names and the note about what could not be kept. The limits and the storage are the parent's
// (lib/test-files.ts); this only reports the choice and shows what the parent kept, so a file the parent refused
// never stays in the list.
const props = defineProps<{ files: StoredFile[]; note: string }>();
const emit = defineEmits<{ add: [files: File[]]; remove: [index: number] }>();

// "text" lists names only: a thumbnail of a file that is not uploaded is a blob: URL, which the page's CSP blocks
// (it has no img-src). If the owner allows `img-src 'self' blob:`, "picture" is the one-line switch.
const LIST_TYPE: "text" | "picture" = "text";

const shown = (files: StoredFile[]): UploadUserFile[] =>
  files.map((f, i) => ({ name: `${f.name} (${Math.ceil(f.blob.size / 1024)} KiB)`, uid: i + 1, status: "ready" }));
const list = ref<UploadUserFile[]>(shown(props.files));
watch(
  () => props.files,
  (files) => (list.value = shown(files)),
);

async function onChange(file: UploadFile): Promise<void> {
  if (file.status !== "ready" || !file.raw) return;
  emit("add", [file.raw]);
  // Show what the parent kept, not what was dropped on the area.
  await nextTick();
  list.value = shown(props.files);
}

function onRemove(file: UploadFile): void {
  // the uid of a listed file is its place in the parent's list, counted from 1 (Element Plus replaces a uid of 0)
  const index = file.uid - 1;
  if (index >= 0 && index < props.files.length) emit("remove", index);
}
</script>

<template>
  <div class="test-images">
    <el-upload
      v-model:file-list="list"
      drag
      multiple
      accept="image/*"
      :auto-upload="false"
      :list-type="LIST_TYPE"
      :on-change="onChange"
      :on-remove="onRemove"
      :title="LIMITS_TEXT"
      class="test-images__drop"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">
        Drop images here or <em>click to choose</em> (up to {{ MAX_IMAGES }} files, {{ MAX_BYTES / 1048576 }} MiB
        together; sent as data)
      </div>
    </el-upload>
    <el-alert v-if="note" type="warning" show-icon :closable="false" :title="note" role="status" />
  </div>
</template>

<style scoped>
.test-images {
  display: grid;
  gap: 8px;
}
</style>
