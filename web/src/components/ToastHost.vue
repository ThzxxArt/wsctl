<script setup lang="ts">
import { useUiStore } from "@/stores/ui";

const ui = useUiStore();
</script>

<template>
  <div class="ws-toast-stack" aria-live="polite" aria-atomic="false">
    <div
      v-for="toast in ui.toasts"
      :key="toast.id"
      class="ws-toast"
      :class="toast.level"
      role="status"
      @click="ui.dismiss(toast.id)"
    >
      {{ toast.message }}
    </div>
  </div>
</template>
