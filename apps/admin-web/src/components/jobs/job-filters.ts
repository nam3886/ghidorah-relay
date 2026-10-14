import { ref } from "vue";

// The status filter lives in memory across visits to the Jobs tab (a module-level ref outlives the view; nothing is
// stored). The cursor stack and the key filter start fresh on every visit.
export const rememberedStatus = ref("");
