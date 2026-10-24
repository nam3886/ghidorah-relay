<script setup lang="ts">
// The one on/off switch of the admin (Accounts rows, Keys rows), drawn as the canvas draws it. It only reports the new
// value (`change`); the view asks the server and reads the list again, so the switch shows what the server holds.
//   desktop  a bare 36x22 track, the track is the button (specs/accounts.md)
//   tablet   a 44x32 hit box around a 34x20 track
//   phone    a 52x44 hit box around a 36x22 track
//   md       Settings (B Ledger): 40x24, 48x28 below 1280 px, in a 44 px tall hit box on a phone
//   (none)   the tablet box, and the phone box below 768 px: what a view that does not know its width wants (Keys)
// The off track is the control grey, the on track the primary colour; the knob is white.
const props = defineProps<{
  modelValue: boolean;
  label: string;
  size?: "desktop" | "tablet" | "phone" | "md";
  disabled?: boolean;
  title?: string;
}>();
const emit = defineEmits<{ change: [value: boolean] }>();
</script>

<template>
  <button
    type="button"
    role="switch"
    class="toggle-switch"
    :class="`toggle-switch--${props.size ?? 'auto'}`"
    :aria-checked="props.modelValue"
    :aria-label="props.label"
    :title="props.title"
    :disabled="props.disabled"
    @click="emit('change', !props.modelValue)"
  >
    <span class="toggle-switch__track" :class="{ 'is-on': props.modelValue }"
      ><span class="toggle-switch__knob"
    /></span>
  </button>
</template>

<style scoped>
.toggle-switch {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  box-sizing: border-box;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.toggle-switch:disabled {
  cursor: default;
  opacity: 0.6;
}
.toggle-switch__track {
  position: relative;
  display: block;
  width: 34px;
  height: 20px;
  border-radius: 10px;
  background: var(--rl-control);
}
.toggle-switch__track.is-on {
  background: var(--rl-primary);
}
.toggle-switch__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 8px;
  background: #ffffff;
}
.toggle-switch__track.is-on .toggle-switch__knob {
  left: 16px;
}

/* the tablet box (and the default) */
.toggle-switch--tablet,
.toggle-switch--auto {
  width: 44px;
  height: 32px;
}

/* desktop: the track is the button */
.toggle-switch--desktop {
  width: 36px;
  height: 22px;
  border-radius: 11px;
}
.toggle-switch--desktop .toggle-switch__track,
.toggle-switch--phone .toggle-switch__track {
  width: 36px;
  height: 22px;
  border-radius: 11px;
}
.toggle-switch--desktop .toggle-switch__knob,
.toggle-switch--phone .toggle-switch__knob {
  top: 3px;
  left: 3px;
}
.toggle-switch--desktop .toggle-switch__track.is-on .toggle-switch__knob,
.toggle-switch--phone .toggle-switch__track.is-on .toggle-switch__knob {
  left: 17px;
}

/* md (Settings): a bare 40x24 track with an 18 px knob; 48x28 with a 22 px knob on a tablet, in a 44 px tall hit box on
   a phone */
.toggle-switch--md,
.toggle-switch--md .toggle-switch__track {
  width: 40px;
  height: 24px;
  border-radius: 12px;
}
.toggle-switch--md .toggle-switch__knob {
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 9px;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.2);
}
.toggle-switch--md .toggle-switch__track.is-on .toggle-switch__knob {
  left: 19px;
}
@media (max-width: 1279px) {
  .toggle-switch--md,
  .toggle-switch--md .toggle-switch__track {
    width: 48px;
    height: 28px;
    border-radius: 14px;
  }
  .toggle-switch--md .toggle-switch__knob {
    width: 22px;
    height: 22px;
    border-radius: 11px;
  }
  .toggle-switch--md .toggle-switch__track.is-on .toggle-switch__knob {
    left: 23px;
  }
}
@media (max-width: 767px) {
  .toggle-switch--md {
    height: 44px;
    margin: -8px 0;
  }
}

/* phone: a 52x44 hit box around the 36x22 track */
.toggle-switch--phone {
  width: 52px;
  height: 44px;
}
@media (max-width: 767.98px) {
  .toggle-switch--auto {
    width: 52px;
    height: 44px;
    margin: -10px -8px -10px 0;
  }
  .toggle-switch--auto .toggle-switch__track {
    width: 36px;
    height: 22px;
    border-radius: 11px;
  }
  .toggle-switch--auto .toggle-switch__knob {
    top: 3px;
    left: 3px;
  }
  .toggle-switch--auto .toggle-switch__track.is-on .toggle-switch__knob {
    left: 17px;
  }
}
</style>
