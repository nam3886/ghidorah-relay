<script setup lang="ts">
import dola from "@/assets/providers/dola.png";
import flow from "@/assets/providers/flow.png";
import gemini from "@/assets/providers/gemini.png";
import gpt from "@/assets/providers/gpt.png";
import grok from "@/assets/providers/grok.png";
import meta from "@/assets/providers/meta.png";
import muse from "@/assets/providers/muse.png";
import qwen from "@/assets/providers/qwen.png";
import vibes from "@/assets/providers/vibes.png";
import { providerClass } from "@/lib/providers";

// A provider's mark: its own site's favicon (64 px files bundled with the app, served from 'self'), in full colour. A
// provider with no favicon here is a square tile in its tone with the first letter of its name, drawn by CSS so a
// chip's text stays the name. Decorative: the name is always written beside it. A status is never drawn this way (that
// is StatusChip's pill with a dot).
const ICONS: Record<string, string> = { dola, meta, vibes, flow, gemini, qwen, muse, gpt, grok };
withDefaults(defineProps<{ provider: string; size?: number }>(), { size: 20 });
</script>

<template>
  <img
    v-if="ICONS[provider]"
    class="ptile"
    :class="providerClass(provider)"
    :src="ICONS[provider]"
    :width="size"
    :height="size"
    alt=""
    aria-hidden="true"
    draggable="false"
  />
  <span
    v-else
    class="ptile"
    :class="[providerClass(provider), `ptile--${size}`]"
    :data-letter="provider.charAt(0).toUpperCase()"
    aria-hidden="true"
  />
</template>

<style scoped>
.ptile {
  flex: none;
}
img.ptile {
  display: inline-block;
  border-radius: 22%;
  vertical-align: middle;
}
span.ptile {
  display: inline-grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 6px;
  background: var(--pc);
  color: var(--rl-on-primary);
  font: 700 11px/1 var(--rl-font);
}
/* the letter is drawn, not written: the tile adds no text to the name beside it */
span.ptile::before {
  content: attr(data-letter);
}
span.ptile--14,
span.ptile--16 {
  width: 14px;
  height: 14px;
  border-radius: 4px;
  font-size: 9px;
}
span.ptile--16 {
  width: 16px;
  height: 16px;
}
</style>
