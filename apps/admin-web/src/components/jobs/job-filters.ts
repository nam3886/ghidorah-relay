import { ref } from "vue";

// The status filter lives in memory across visits to the Jobs tab (a module-level ref outlives the view; nothing is
// stored). The cursor stack and the key filter start fresh on every visit.
export const rememberedStatus = ref("");

// Element Plus reads a value of "" as "nothing chosen" and shows the placeholder instead of the "All ..." label. The
// selects therefore hold this non-empty stand-in for "all"; the filters themselves keep "" (the query drops it).
export const ALL = "__all__";
export const toSelect = (value: string): string => value || ALL;
export const fromSelect = (value: string): string => (value === ALL ? "" : value);
