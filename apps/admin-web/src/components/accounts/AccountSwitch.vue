<script setup lang="ts">
// The enable switch of a row, drawn per width (specs/accounts.md sections 3 to 5): a bare 36x22 track on the desktop,
// a 44x32 hit box around a 34x20 track on the tablet, a 52x44 hit box around a 36x22 track on the phone.
const props = defineProps<{ on: boolean; label: string; size: "desktop" | "tablet" | "phone" }>();
const emit = defineEmits<{ change: [on: boolean] }>();
</script>

<template>
  <button
    type="button"
    role="switch"
    class="acct-switch"
    :class="`acct-switch--${props.size}`"
    :aria-checked="props.on"
    :aria-label="props.label"
    @click="emit('change', !props.on)"
  >
    <span class="acct-switch__track" :class="{ 'is-on': props.on }"><span class="acct-switch__knob" /></span>
  </button>
</template>

<style scoped>
.acct-switch {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.acct-switch__track {
  position: relative;
  display: block;
  background: var(--rl-control);
}
.acct-switch__track.is-on {
  background: var(--rl-primary);
}
.acct-switch__knob {
  position: absolute;
  width: 16px;
  height: 16px;
  border-radius: 8px;
  background: #fff;
}
/* desktop: the track is the button */
.acct-switch--desktop {
  width: 36px;
  height: 22px;
  border-radius: 11px;
}
.acct-switch--desktop .acct-switch__track {
  width: 36px;
  height: 22px;
  border-radius: 11px;
}
.acct-switch--desktop .acct-switch__knob {
  top: 3px;
  left: 3px;
}
.acct-switch--desktop .is-on .acct-switch__knob {
  left: 17px;
}
.acct-switch--tablet {
  width: 44px;
  height: 32px;
}
.acct-switch--tablet .acct-switch__track {
  width: 34px;
  height: 20px;
  border-radius: 10px;
}
.acct-switch--tablet .acct-switch__knob {
  top: 2px;
  left: 2px;
}
.acct-switch--tablet .is-on .acct-switch__knob {
  left: 16px;
}
.acct-switch--phone {
  width: 52px;
  height: 44px;
}
.acct-switch--phone .acct-switch__track {
  width: 36px;
  height: 22px;
  border-radius: 11px;
}
.acct-switch--phone .acct-switch__knob {
  top: 3px;
  left: 3px;
}
.acct-switch--phone .is-on .acct-switch__knob {
  left: 17px;
}
</style>
