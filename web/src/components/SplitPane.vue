<script setup lang="ts">
import { Splitpanes, Pane } from "splitpanes";
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useWorkspaceStore, type PaneNode } from "@/stores/workspace";
import TerminalPane from "./TerminalPane.vue";

defineOptions({ name: "SplitPane" });

const props = defineProps<{ node: PaneNode; tabId: string; shareOnly?: boolean }>();
const workspace = useWorkspaceStore();
const isNarrow = ref(typeof window !== "undefined" && window.innerWidth < 760);

function updateNarrow(): void {
  isNarrow.value = window.innerWidth < 760;
}

onMounted(() => window.addEventListener("resize", updateNarrow));
onBeforeUnmount(() => window.removeEventListener("resize", updateNarrow));

function onResize(payload: { event?: Event }): void {
  if (props.node.type !== "split") return;
  const target = payload.event?.target as HTMLElement | null;
  const container = target?.closest(".splitpanes") as HTMLElement | null;
  if (!container) return;
  // ``Splitpanes`` is forced horizontal on narrow screens. Measure the axis
  // it is actually laid out on, not the node's stored direction, or a stacked
  // pane reports its height as its width and the ratio jumps.
  const useWidth = isNarrow.value || props.node.dir === "row";
  const panes = container.querySelectorAll<HTMLElement>(":scope > .splitpanes__pane");
  const first = panes[0];
  const total = [...panes].reduce(
    (sum, pane) => sum + (useWidth ? pane.offsetWidth : pane.offsetHeight),
    0,
  );
  if (!first || total <= 0) return;
  const size = useWidth ? first.offsetWidth : first.offsetHeight;
  workspace.setRatio(props.node.id, (size / total) * 100);
}

function onDoubleClick(event: MouseEvent): void {
  if (props.node.type !== "split") return;
  const target = event.target as HTMLElement | null;
  if (target?.classList.contains("splitpanes__splitter")) {
    workspace.setRatio(props.node.id, 50);
  }
}
</script>

<template>
  <TerminalPane
    v-if="node.type === 'leaf'"
    :pane="node"
    :tab-id="tabId"
    :share-only="shareOnly"
    :focused="workspace.activeTab?.focus === node.id"
  />
  <Splitpanes
    v-else
    :horizontal="isNarrow || node.dir === 'col'"
    :class="{
      'maximized-hidden': workspace.maximizedPaneId && workspace.maximizedPaneId !== node.id,
    }"
    @resize="onResize"
    @dblclick="onDoubleClick"
  >
    <Pane :size="node.ratio" :min-size="12">
      <SplitPane :node="node.a" :tab-id="tabId" :share-only="shareOnly" />
    </Pane>
    <Pane :size="100 - node.ratio" :min-size="12">
      <SplitPane :node="node.b" :tab-id="tabId" :share-only="shareOnly" />
    </Pane>
  </Splitpanes>
</template>

<style scoped>
.maximized-hidden {
  visibility: hidden;
  position: absolute;
  inset: 0;
}
</style>
