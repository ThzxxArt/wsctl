<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { EditorState } from "@codemirror/state";
import {
  EditorView,
  keymap,
  highlightActiveLine,
  highlightActiveLineGutter,
  lineNumbers,
  drawSelection,
  dropCursor,
  rectangularSelection,
  crosshairCursor,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { oneDark } from "@codemirror/theme-one-dark";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { python } from "@codemirror/lang-python";
import { markdown } from "@codemirror/lang-markdown";
import { NButton, NModal, NSpace } from "naive-ui";
import { confirmDialog } from "@/dialogs";

const props = defineProps<{ path: string; content: string }>();
const emit = defineEmits<{ close: []; save: [content: string] }>();

const host = ref<HTMLDivElement | null>(null);
const current = ref(props.content);
const dirty = ref(false);
let view: EditorView | null = null;

async function requestClose(): Promise<void> {
  if (
    dirty.value &&
    !(await confirmDialog({
      title: "未保存的修改",
      message: "有未保存的修改，确定关闭？",
      confirmText: "放弃修改",
      cancelText: "继续编辑",
      danger: true,
    }))
  ) {
    return;
  }
  emit("close");
}

function save(): void {
  // Keep ``dirty`` true until the parent closes the dialog: the save may fail
  // (permissions, disk full) and the editor stays open. Clearing it first made
  // a subsequent close skip the unsaved-changes warning and lose the draft.
  emit("save", current.value);
}

function languageFor(path: string) {
  const lower = path.toLowerCase();
  if (lower.endsWith(".json")) return json();
  if (lower.endsWith(".py")) return python();
  if (lower.endsWith(".md") || lower.endsWith(".markdown")) return markdown();
  if (/\.(js|mjs|cjs|ts|tsx|jsx)$/.test(lower)) {
    return javascript({ typescript: /\.tsx?$/.test(lower), jsx: /\.(tsx|jsx)$/.test(lower) });
  }
  return [];
}

onMounted(() => {
  if (!host.value) return;
  const state = EditorState.create({
    doc: props.content,
    extensions: [
      lineNumbers(),
      highlightActiveLineGutter(),
      highlightActiveLine(),
      history(),
      drawSelection(),
      dropCursor(),
      rectangularSelection(),
      crosshairCursor(),
      highlightSelectionMatches(),
      keymap.of([
        {
          key: "Mod-s",
          run: () => {
            save();
            return true;
          },
        },
        indentWithTab,
        ...defaultKeymap,
        ...historyKeymap,
        ...searchKeymap,
      ]),
      languageFor(props.path),
      oneDark,
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          current.value = update.state.doc.toString();
          dirty.value = current.value !== props.content;
        }
      }),
      EditorView.theme({
        "&": { height: "100%" },
        ".cm-scroller": { overflow: "auto" },
      }),
    ],
  });
  view = new EditorView({ state, parent: host.value });
  view.focus();
});

onBeforeUnmount(() => view?.destroy());
</script>

<template>
  <NModal
    :show="true"
    preset="card"
    style="width: min(980px, 96vw); height: 86vh; display: flex; flex-direction: column"
    :title="path"
    @close="requestClose"
  >
    <div ref="host" class="editor" />
    <template #footer>
      <NSpace justify="end">
        <NButton @click="requestClose">取消</NButton>
        <NButton type="primary" @click="save">保存</NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.editor {
  height: calc(86vh - 130px);
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 13px;
}
</style>
