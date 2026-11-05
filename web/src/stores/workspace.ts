import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";

export type SplitDirection = "row" | "col";

export interface LeafNode {
  type: "leaf";
  id: string;
  sessionId: string | null;
  title: string;
  share?: string | null;
}

export interface SplitNode {
  type: "split";
  id: string;
  dir: SplitDirection;
  ratio: number;
  a: PaneNode;
  b: PaneNode;
}

export type PaneNode = LeafNode | SplitNode;

export interface Tab {
  id: string;
  name: string;
  root: PaneNode;
  focus: string;
}

interface PersistedLayout {
  version: 2;
  activeTabId: string | null;
  tabs: Tab[];
}

const STORAGE_KEY = "wsctl-layout-v2";
const MAX_TABS = 24;
const MAX_LEAVES = 16;

function uid(prefix: string): string {
  const value =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `${prefix}-${value}`;
}

export function makeLeaf(sessionId: string | null = null, title = "终端"): LeafNode {
  return { type: "leaf", id: uid("pane"), sessionId, title };
}

const MAX_SPLIT_DEPTH = 12;

/**
 * Validate and normalise a persisted or server-supplied layout.
 *
 * LocalStorage and ``PUT /layouts`` both accept arbitrary JSON. A ``split``
 * node without ``a``/``b`` used to reach the renderer and throw inside a
 * computed on every render, which is a white screen that only clearing site
 * data could fix. Validation is recursive, depth-bounded and leaf-bounded,
 * and returns ``null`` rather than a half-usable tree.
 */
export function sanitizeLayout(value: unknown): PersistedLayout | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as { tabs?: unknown; activeTabId?: unknown };
  if (!Array.isArray(candidate.tabs)) return null;
  if (candidate.tabs.length === 0 || candidate.tabs.length > MAX_TABS) return null;
  const seen = new Set<string>();
  const tabs: Tab[] = [];
  for (const raw of candidate.tabs) {
    const tab = sanitizeTab(raw, seen);
    if (!tab) return null;
    tabs.push(tab);
  }
  const wanted = typeof candidate.activeTabId === "string" ? candidate.activeTabId : null;
  const active = tabs.some((tab) => tab.id === wanted) ? wanted : tabs[0].id;
  return { version: 2, activeTabId: active, tabs };
}

function sanitizeTab(value: unknown, seen: Set<string>): Tab | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const id = typeof raw.id === "string" && raw.id ? raw.id : uid("tab");
  if (seen.has(id)) return null;
  seen.add(id);
  const budget = { leaves: 0 };
  const root = sanitizeNode(raw.root, seen, budget, 0);
  if (!root) return null;
  const focus =
    typeof raw.focus === "string" && findLeaf(root, raw.focus) ? raw.focus : firstLeaf(root).id;
  const name =
    typeof raw.name === "string" && raw.name.trim()
      ? raw.name.trim().slice(0, 80)
      : `终端 ${seen.size}`;
  return { id, name, root, focus };
}

function sanitizeNode(
  value: unknown,
  seen: Set<string>,
  budget: { leaves: number },
  depth: number,
): PaneNode | null {
  if (!value || typeof value !== "object" || depth > MAX_SPLIT_DEPTH) return null;
  const raw = value as Record<string, unknown>;
  if (raw.type === "leaf") {
    budget.leaves += 1;
    if (budget.leaves > MAX_LEAVES) return null;
    const id = typeof raw.id === "string" && raw.id ? raw.id : uid("pane");
    if (seen.has(id)) return null;
    seen.add(id);
    const sessionId = typeof raw.sessionId === "string" && raw.sessionId ? raw.sessionId : null;
    const share = typeof raw.share === "string" && raw.share ? raw.share : null;
    const title =
      typeof raw.title === "string" && raw.title.trim() ? raw.title.trim().slice(0, 80) : "终端";
    return { type: "leaf", id, sessionId, title, share };
  }
  if (raw.type !== "split") return null;
  const a = sanitizeNode(raw.a, seen, budget, depth + 1);
  const b = sanitizeNode(raw.b, seen, budget, depth + 1);
  if (!a || !b) return null;
  const id = typeof raw.id === "string" && raw.id ? raw.id : uid("split");
  if (seen.has(id)) return null;
  seen.add(id);
  const ratioNumber = Number(raw.ratio);
  const ratio = Number.isFinite(ratioNumber) ? Math.min(90, Math.max(10, ratioNumber)) : 50;
  const dir: SplitDirection = raw.dir === "col" ? "col" : "row";
  return { type: "split", id, dir, ratio, a, b };
}

export function leavesOf(node: PaneNode): LeafNode[] {
  if (node.type === "leaf") return [node];
  return [...leavesOf(node.a), ...leavesOf(node.b)];
}

export function findLeaf(node: PaneNode, paneId: string): LeafNode | null {
  if (node.type === "leaf") return node.id === paneId ? node : null;
  return findLeaf(node.a, paneId) ?? findLeaf(node.b, paneId);
}

function replaceNode(node: PaneNode, paneId: string, next: PaneNode): PaneNode {
  if (node.type === "leaf") return node.id === paneId ? next : node;
  return {
    ...node,
    a: replaceNode(node.a, paneId, next),
    b: replaceNode(node.b, paneId, next),
  };
}

function removeLeaf(node: PaneNode, paneId: string): PaneNode | null {
  if (node.type === "leaf") return node.id === paneId ? null : node;
  const a = removeLeaf(node.a, paneId);
  const b = removeLeaf(node.b, paneId);
  if (a && b) return { ...node, a, b };
  return a ?? b;
}

function firstLeaf(node: PaneNode): LeafNode {
  return node.type === "leaf" ? node : firstLeaf(node.a);
}

export const useWorkspaceStore = defineStore("workspace", () => {
  const tabs = ref<Tab[]>([]);
  const activeTabId = ref<string | null>(null);
  const maximizedPaneId = ref<string | null>(null);
  const hydrated = ref(false);

  const activeTab = computed<Tab | null>(
    () => tabs.value.find((tab) => tab.id === activeTabId.value) ?? tabs.value[0] ?? null,
  );
  const allLeaves = computed(() => tabs.value.flatMap((tab) => leavesOf(tab.root)));

  function withoutShare(node: PaneNode): PaneNode {
    if (node.type === "leaf") return { ...node, share: null };
    return { ...node, a: withoutShare(node.a), b: withoutShare(node.b) };
  }

  function snapshot(): PersistedLayout {
    return {
      version: 2,
      activeTabId: activeTabId.value,
      // A share token is a bearer credential; persisting it in localStorage
      // (or uploading it with a server layout) would outlive the tab.
      tabs: tabs.value.map((tab) => ({ ...tab, root: withoutShare(tab.root) })),
    };
  }

  function persist(): void {
    if (!hydrated.value) return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot()));
    } catch {
      /* ignore quota errors */
    }
  }

  watch([tabs, activeTabId], persist, { deep: true });

  function createTab(
    sessionId: string | null = null,
    name?: string,
    activate = true,
    share: string | null = null,
  ): Tab {
    if (tabs.value.length >= MAX_TABS) {
      throw new Error(`最多同时打开 ${MAX_TABS} 个标签`);
    }
    const leaf = makeLeaf(sessionId, name ?? "终端");
    leaf.share = share;
    const tab: Tab = {
      id: uid("tab"),
      name: name ?? `终端 ${tabs.value.length + 1}`,
      root: leaf,
      focus: leaf.id,
    };
    tabs.value.push(tab);
    if (activate) activeTabId.value = tab.id;
    return tab;
  }

  function closeTab(tabId: string): void {
    const index = tabs.value.findIndex((tab) => tab.id === tabId);
    if (index < 0) return;
    tabs.value.splice(index, 1);
    if (activeTabId.value === tabId) {
      const next = tabs.value[Math.min(index, tabs.value.length - 1)];
      activeTabId.value = next?.id ?? null;
    }
    if (tabs.value.length === 0) createTab(null, "终端");
  }

  function activateTab(tabId: string): void {
    if (tabs.value.some((tab) => tab.id === tabId)) {
      activeTabId.value = tabId;
      maximizedPaneId.value = null;
    }
  }

  function tabForPane(paneId: string): Tab | null {
    return tabs.value.find((tab) => findLeaf(tab.root, paneId) !== null) ?? null;
  }

  function splitPane(
    paneId: string,
    dir: SplitDirection,
    sessionId: string | null = null,
    title = "终端",
  ): LeafNode | null {
    const tab = tabForPane(paneId);
    if (!tab) return null;
    if (leavesOf(tab.root).length >= MAX_LEAVES) {
      throw new Error(`单个标签最多 ${MAX_LEAVES} 个分屏`);
    }
    const node = findLeaf(tab.root, paneId);
    if (!node) return null;
    const leaf = makeLeaf(sessionId, title);
    tab.root = replaceNode(tab.root, paneId, {
      type: "split",
      id: uid("split"),
      dir,
      ratio: 50,
      a: node,
      b: leaf,
    });
    tab.focus = leaf.id;
    maximizedPaneId.value = null;
    return leaf;
  }

  function closePane(paneId: string): void {
    const tab = tabForPane(paneId);
    if (!tab) return;
    if (tab.root.type === "leaf") {
      closeTab(tab.id);
      return;
    }
    const next = removeLeaf(tab.root, paneId);
    if (next) {
      tab.root = next;
      tab.focus = firstLeaf(next).id;
    }
    if (maximizedPaneId.value === paneId) maximizedPaneId.value = null;
  }

  function focusPane(paneId: string): void {
    const tab = tabForPane(paneId);
    if (tab) {
      tab.focus = paneId;
      if (activeTabId.value !== tab.id) maximizedPaneId.value = null;
      activeTabId.value = tab.id;
    }
  }

  function setRatio(nodeId: string, ratio: number): void {
    const clamped = Math.min(90, Math.max(10, ratio));
    function walk(node: PaneNode): void {
      if (node.type === "leaf") return;
      if (node.id === nodeId) node.ratio = clamped;
      walk(node.a);
      walk(node.b);
    }
    for (const tab of tabs.value) walk(tab.root);
  }

  function parentSplitOf(node: PaneNode, paneId: string): SplitNode | null {
    if (node.type === "leaf") return null;
    if (node.a.type === "leaf" && node.a.id === paneId) return node;
    if (node.b.type === "leaf" && node.b.id === paneId) return node;
    return parentSplitOf(node.a, paneId) ?? parentSplitOf(node.b, paneId);
  }

  function nudgeRatio(paneId: string, delta: number): void {
    const tab = tabForPane(paneId);
    if (!tab) return;
    const parent = parentSplitOf(tab.root, paneId);
    if (!parent) return;
    // The focused pane may be the second child; growing it means shrinking the
    // split ratio, because ``ratio`` is the share of the first child.
    const direction = parent.a.type === "leaf" && parent.a.id === paneId ? 1 : -1;
    setRatio(parent.id, parent.ratio + direction * delta);
  }

  function duplicateTab(tabId: string): Tab | null {
    const source = tabs.value.find((tab) => tab.id === tabId);
    if (!source) return null;
    if (tabs.value.length >= MAX_TABS) {
      throw new Error(`最多同时打开 ${MAX_TABS} 个标签`);
    }
    const clone = (node: PaneNode): PaneNode =>
      node.type === "leaf"
        ? { ...node, id: uid("pane") }
        : { ...node, id: uid("split"), a: clone(node.a), b: clone(node.b) };
    const root = clone(source.root);
    const tab: Tab = {
      id: uid("tab"),
      name: `${source.name} 副本`,
      root,
      focus: firstLeaf(root).id,
    };
    tabs.value.push(tab);
    activeTabId.value = tab.id;
    maximizedPaneId.value = null;
    return tab;
  }

  function swapPanes(firstPaneId: string, secondPaneId: string): boolean {
    const tab = tabForPane(firstPaneId);
    if (!tab || tabForPane(secondPaneId)?.id !== tab.id) return false;
    const first = findLeaf(tab.root, firstPaneId);
    const second = findLeaf(tab.root, secondPaneId);
    if (!first || !second || first.id === second.id) return false;
    [first.sessionId, second.sessionId] = [second.sessionId, first.sessionId];
    [first.title, second.title] = [second.title, first.title];
    [first.share, second.share] = [second.share, first.share];
    return true;
  }

  function renameTab(tabId: string, name: string): void {
    const tab = tabs.value.find((item) => item.id === tabId);
    const cleaned = name.trim();
    if (tab && cleaned) tab.name = cleaned.slice(0, 80);
  }

  function renamePane(paneId: string, title: string): void {
    const tab = tabForPane(paneId);
    if (!tab) return;
    const leaf = findLeaf(tab.root, paneId);
    if (leaf) leaf.title = title.trim().slice(0, 80) || "终端";
  }

  function setPaneSession(
    paneId: string,
    sessionId: string | null,
    title?: string,
    share?: string | null,
  ): void {
    const tab = tabForPane(paneId);
    if (!tab) return;
    const leaf = findLeaf(tab.root, paneId);
    if (leaf) {
      leaf.sessionId = sessionId;
      leaf.share = share ?? null;
      if (title) leaf.title = title;
    }
  }

  function moveTab(from: number, to: number): void {
    if (from === to || from < 0 || to < 0 || from >= tabs.value.length || to >= tabs.value.length)
      return;
    const [tab] = tabs.value.splice(from, 1);
    tabs.value.splice(to, 0, tab);
  }

  function movePaneToTab(paneId: string, targetTabId: string): boolean {
    const source = tabForPane(paneId);
    const target = tabs.value.find((tab) => tab.id === targetTabId);
    if (!source || !target || source.id === target.id) return false;
    const leaf = findLeaf(source.root, paneId);
    if (!leaf) return false;
    if (leavesOf(target.root).length >= MAX_LEAVES) {
      throw new Error(`单个标签最多 ${MAX_LEAVES} 个分屏`);
    }
    const copy: LeafNode = { ...leaf, id: uid("pane") };
    target.root = {
      type: "split",
      id: uid("split"),
      dir: "row",
      ratio: 50,
      a: target.root,
      b: copy,
    };
    target.focus = copy.id;
    closePane(paneId);
    return true;
  }

  function toggleMaximize(paneId: string): void {
    maximizedPaneId.value = maximizedPaneId.value === paneId ? null : paneId;
  }

  function applyPreset(preset: "single" | "columns" | "rows" | "2x2"): void {
    const tab = activeTab.value;
    if (!tab) return;
    const existing = leavesOf(tab.root);
    const first = existing[0] ?? makeLeaf(null);
    if (preset === "single") {
      tab.root = { ...first };
      tab.focus = first.id;
      return;
    }
    const make = (index: number): LeafNode => {
      const found = existing[index];
      return found ? { ...found } : makeLeaf(null);
    };
    const split = (a: PaneNode, b: PaneNode, dir: SplitDirection): SplitNode => ({
      type: "split",
      id: uid("split"),
      dir,
      ratio: 50,
      a,
      b,
    });
    if (preset === "columns") {
      tab.root = split(make(0), split(make(1), make(2), "row"), "row");
    } else if (preset === "rows") {
      tab.root = split(make(0), split(make(1), make(2), "col"), "col");
    } else {
      tab.root = split(split(make(0), make(1), "row"), split(make(2), make(3), "row"), "col");
    }
    tab.focus = firstLeaf(tab.root).id;
  }

  function hydrate(): void {
    if (hydrated.value) return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const sanitized = sanitizeLayout(JSON.parse(raw));
        if (sanitized) {
          tabs.value = sanitized.tabs;
          activeTabId.value = sanitized.activeTabId;
          hydrated.value = true;
          return;
        }
      }
      const legacy = localStorage.getItem("wsctl-layout");
      if (legacy) {
        const parsed = JSON.parse(legacy);
        if (parsed?.version === 1 && Array.isArray(parsed.groups)) {
          const migrated: Tab[] = parsed.groups.map((group: any) => {
            const root = convertLegacyNode(group.root);
            return {
              id: uid("tab"),
              name: String(group.name ?? "终端"),
              root,
              focus: firstLeaf(root).id,
            };
          });
          if (migrated.length > 0) {
            tabs.value = migrated;
            activeTabId.value = migrated[0].id;
          }
        }
      }
    } catch {
      tabs.value = [];
    } finally {
      hydrated.value = true;
      if (tabs.value.length === 0) createTab(null, "终端");
      const active = activeTab.value;
      if (active) activeTabId.value = active.id;
    }
  }

  function convertLegacyNode(node: any): PaneNode {
    if (!node || node.type !== "split") {
      return makeLeaf(node?.sessionId ?? null, "终端");
    }
    return {
      type: "split",
      id: uid("split"),
      dir: node.dir === "col" ? "col" : "row",
      ratio: Number(node.ratio) || 50,
      a: convertLegacyNode(node.a),
      b: convertLegacyNode(node.b),
    };
  }

  function replaceLayout(payload: { tabs?: Tab[]; activeTabId?: string | null }): boolean {
    const sanitized = sanitizeLayout(payload);
    if (!sanitized) return false;
    tabs.value = sanitized.tabs;
    activeTabId.value = sanitized.activeTabId ?? sanitized.tabs[0].id;
    maximizedPaneId.value = null;
    hydrated.value = true;
    persist();
    return true;
  }

  function reset(): void {
    tabs.value = [];
    activeTabId.value = null;
    maximizedPaneId.value = null;
    hydrated.value = false;
    try {
      // The legacy key must go too: hydrate() migrates it on every login, so
      // leaving it behind resurrected the previous account's workspace on the
      // next sign-in after an upgrade.
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem("wsctl-layout");
    } catch {
      /* storage disabled */
    }
  }

  return {
    tabs,
    activeTabId,
    maximizedPaneId,
    hydrated,
    activeTab,
    allLeaves,
    createTab,
    closeTab,
    activateTab,
    splitPane,
    closePane,
    focusPane,
    setRatio,
    nudgeRatio,
    duplicateTab,
    swapPanes,
    renameTab,
    renamePane,
    setPaneSession,
    moveTab,
    movePaneToTab,
    snapshot,
    toggleMaximize,
    applyPreset,
    hydrate,
    replaceLayout,
    reset,
    tabForPane,
  };
});
