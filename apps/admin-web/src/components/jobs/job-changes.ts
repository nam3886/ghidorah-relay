import { ref } from "vue";

// The job detail is mounted once in the shell and the list lives in the Jobs view: when an action in the detail
// changes a job (rename, re-check, regenerate, fetch title) it announces it here and the list reloads itself.
export const jobChanges = ref(0);
export const announceJobChange = (): void => {
  jobChanges.value++;
};
