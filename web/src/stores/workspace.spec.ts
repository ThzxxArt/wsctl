import { beforeEach, describe, expect, it } from "vitest";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { findLeaf, leavesOf, sanitizeLayout, useWorkspaceStore } from "@/stores/workspace";

describe("workspace tree", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it("creates a tab with one focused leaf", () => {
    const store = useWorkspaceStore();
    const tab = store.createTab("s1", "bash");
    expect(store.tabs).toHaveLength(1);
    expect(tab.root.type).toBe("leaf");
    expect(store.activeTab?.id).toBe(tab.id);
    expect(leavesOf(tab.root)[0].sessionId).toBe("s1");
  });

  it("splits a pane and focuses the new leaf", () => {
    const store = useWorkspaceStore();
    const tab = store.createTab("s1");
    const leaf = store.splitPane(tab.focus, "row", "s2", "zsh");
    expect(leaf).not.toBeNull();
    expect(tab.root.type).toBe("split");
    expect(leavesOf(tab.root)).toHaveLength(2);
    expect(tab.focus).toBe(leaf?.id);
  });

  it("promotes the sibling when a pane closes", () => {
    const store = useWorkspaceStore();
    const tab = store.createTab("s1");
    const paneA = tab.focus;
    const paneB = store.splitPane(paneA, "row", "s2", "zsh");
    expect(paneB).not.toBeNull();
    store.closePane(paneA);
    expect(tab.root.type).toBe("leaf");
    expect((tab.root as { sessionId: string }).sessionId).toBe("s2");
  });

  it("closes the tab when its last pane closes", () => {
    const store = useWorkspaceStore();
    const tab = store.createTab("s1");
    store.closePane(tab.focus);
    expect(store.tabs).toHaveLength(1);
    expect(store.tabs[0].id).not.toBe(tab.id);
  });

  it("persists and rehydrates the layout", async () => {
    const store = useWorkspaceStore();
    store.hydrate();
    store.tabs.splice(0, store.tabs.length);
    const tab = store.createTab("s1", "bash");
    store.splitPane(tab.focus, "col", "s2", "zsh");
    await nextTick();
    const raw = localStorage.getItem("wsctl-layout-v2");
    expect(raw).toBeTruthy();
    setActivePinia(createPinia());
    const restored = useWorkspaceStore();
    restored.hydrate();
    expect(restored.tabs).toHaveLength(1);
    expect(leavesOf(restored.tabs[0].root)).toHaveLength(2);
    expect(restored.activeTabId).toBe(restored.tabs[0].id);
  });

  it("finds a leaf by id", () => {
    const store = useWorkspaceStore();
    const tab = store.createTab("s1");
    const leaf = leavesOf(tab.root)[0];
    expect(findLeaf(tab.root, leaf.id)).toBe(leaf);
    expect(findLeaf(tab.root, "missing")).toBeNull();
  });

  it("rejects a split past the per-tab limit", () => {
    const store = useWorkspaceStore();
    const tab = store.createTab("s1");
    let focus = tab.focus;
    for (let index = 0; index < 15; index += 1) {
      const leaf = store.splitPane(focus, "row", `s${index + 2}`);
      expect(leaf).not.toBeNull();
      focus = leaf!.id;
    }
    expect(leavesOf(tab.root)).toHaveLength(16);
    expect(() => store.splitPane(focus, "row", "overflow")).toThrow();
  });

  it("rejects layouts whose split nodes are incomplete", () => {
    const broken = {
      version: 2,
      activeTabId: "t1",
      tabs: [{ id: "t1", name: "x", root: { type: "split", id: "sp1" }, focus: "p1" }],
    };
    expect(sanitizeLayout(broken)).toBeNull();
    const store = useWorkspaceStore();
    expect(store.replaceLayout(broken as never)).toBe(false);
    expect(store.tabs).toHaveLength(0);
  });

  it("rejects duplicate pane ids and excessive depth", () => {
    const duplicate = {
      tabs: [
        {
          id: "t1",
          name: "x",
          focus: "p1",
          root: {
            type: "split",
            id: "sp1",
            dir: "row",
            ratio: 50,
            a: { type: "leaf", id: "p1", sessionId: "s1", title: "a" },
            b: { type: "leaf", id: "p1", sessionId: "s2", title: "b" },
          },
        },
      ],
    };
    expect(sanitizeLayout(duplicate)).toBeNull();
    let node: Record<string, unknown> = { type: "leaf", id: "p0", sessionId: null, title: "x" };
    for (let index = 1; index <= 14; index += 1) {
      node = {
        type: "split",
        id: `sp${index}`,
        dir: "row",
        ratio: 50,
        a: node,
        b: { type: "leaf", id: `p${index}`, sessionId: null, title: "x" },
      };
    }
    expect(sanitizeLayout({ tabs: [{ id: "t1", name: "x", focus: "p0", root: node }] })).toBeNull();
  });

  it("clamps an out-of-range split ratio instead of trusting it", () => {
    const sanitized = sanitizeLayout({
      tabs: [
        {
          id: "t1",
          name: "x",
          focus: "p1",
          root: {
            type: "split",
            id: "sp1",
            dir: "col",
            ratio: 900,
            a: { type: "leaf", id: "p1", sessionId: null, title: "a" },
            b: { type: "leaf", id: "p2", sessionId: null, title: "b" },
          },
        },
      ],
    });
    expect(sanitized).not.toBeNull();
    const root = sanitized!.tabs[0].root;
    expect(root.type).toBe("split");
    if (root.type === "split") {
      expect(root.ratio).toBe(90);
      expect(root.dir).toBe("col");
    }
  });
});
