<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from "vue";

const props = defineProps<{ x: number; y: number }>();
const emit = defineEmits<{ action: [action: string]; close: [] }>();

const style = computed(() => ({
  left: `${Math.max(8, Math.min(props.x, window.innerWidth - 168))}px`,
  top: `${Math.max(8, Math.min(props.y, window.innerHeight - 250))}px`,
}));

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    emit("close");
  }
}

onMounted(() => window.addEventListener("keydown", onKeydown, true));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown, true));

const items = [
  { key: "copy", label: "复制" },
  { key: "paste", label: "粘贴" },
  { key: "clear", label: "清屏" },
  { key: "repaint", label: "强制重绘" },
  { key: "split", label: "向右分屏" },
  { key: "search", label: "搜索" },
];
</script>

<template>
  <div class="context-backdrop" @mousedown.self="emit('close')" @contextmenu.prevent>
    <div class="context-menu" role="menu" :style="style">
      <button
        v-for="item in items"
        :key="item.key"
        role="menuitem"
        @click="emit('action', item.key)"
      >
        {{ item.label }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.context-backdrop {
  position: fixed;
  inset: 0;
  z-index: 40;
}
.context-menu {
  position: fixed;
  display: flex;
  flex-direction: column;
  min-width: 148px;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-elevated);
  box-shadow: 0 10px 30px rgb(0 0 0 / 35%);
}
.context-menu button {
  padding: 6px 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--text);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.context-menu button:hover,
.context-menu button:focus-visible {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  outline: none;
}
</style>
