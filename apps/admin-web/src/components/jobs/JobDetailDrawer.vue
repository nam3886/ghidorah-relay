<script setup lang="ts">
import JobDetailDialog from "@/components/jobs/JobDetailDialog.vue";

// The job detail as App.vue mounts it (opened through stores/ui openJob). For now it shows the existing dialog
// unchanged; the Jobs drawer lane replaces this file with the real drawer.
</script>

<template>
  <JobDetailDialog />
</template>
