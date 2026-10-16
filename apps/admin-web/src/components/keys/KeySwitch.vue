<script setup lang="ts">
// The on/off switch of a key row, drawn as the canvas draws it: a 34x20 track in a 44x32 hit box (36x22 in a 52x44
// box on a phone), primary when on, the control grey when off. It only reports the new value; the view asks the
// server and reads the list again, so the switch shows what the server holds.
defineProps<{ modelValue: boolean; label: string; disabled?: boolean }>();
const emit = defineEmits<{ change: [value: boolean] }>();
</script>

<template>
  <button
    type="button"
    role="switch"
    class="key-switch"
    :class="{ 'is-on': modelValue }"
    :aria-checked="modelValue"
    :aria-label="label"
    :title="modelValue ? 'Disable key' : 'Enable key'"
    :disabled="disabled"
    @click="emit('change', !modelValue)"
  >
    <span class="key-switch__track"><span class="key-switch__knob" /></span>
  </button>
</template>

<style scoped>
.key-switch {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  box-sizing: border-box;
  width: 44px;
  height: 32px;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
}
.key-switch:disabled {
  cursor: default;
  opacity: 0.6;
}
.key-switch__track {
  position: relative;
  width: 34px;
  height: 20px;
  border-radius: 10px;
  background: var(--rl-control);
}
.key-switch.is-on .key-switch__track {
  background: var(--rl-primary);
}
.key-switch__knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #ffffff;
}
.key-switch.is-on .key-switch__knob {
  left: 16px;
}
@media (max-width: 767px) {
  .key-switch {
    width: 52px;
    height: 44px;
    margin: -10px -8px -10px 0;
  }
  .key-switch__track {
    width: 36px;
    height: 22px;
    border-radius: 11px;
  }
  .key-switch__knob {
    top: 3px;
    left: 3px;
  }
  .key-switch.is-on .key-switch__knob {
    left: 17px;
  }
}
</style>
