import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import TabBar from "@/components/TabBar.vue";
import CommandPalette from "@/components/CommandPalette.vue";
import SplitPane from "@/components/SplitPane.vue";
import { i18n } from "@/i18n";
import { useWorkspaceStore } from "@/stores/workspace";

function plugins() {
  return [i18n];
}

describe("workspace components", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
  });

  it("TabBar lists tabs and opens the command palette target", async () => {
    const workspace = useWorkspaceStore();
    workspace.createTab("s1", "bash");
    workspace.createTab("s2", "zsh");
    const wrapper = mount(TabBar, { global: { plugins: plugins() } });
    expect(wrapper.findAll(".tab")).toHaveLength(2);
    expect(wrapper.text()).toContain("bash");
    await wrapper.findAll(".tab")[1].trigger("click");
    expect(workspace.activeTab?.name).toBe("zsh");
  });

  it("TabBar duplicates a tab without reusing pane ids", async () => {
    const workspace = useWorkspaceStore();
    workspace.createTab("s1", "bash");
    const wrapper = mount(TabBar, { global: { plugins: plugins() } });
    await wrapper.find(".dup").trigger("click");
    expect(workspace.tabs).toHaveLength(2);
    const first = workspace.tabs[0];
    const second = workspace.tabs[1];
    const firstIds = JSON.stringify(first.root);
    expect(JSON.stringify(second.root)).not.toBe(firstIds);
  });

  it("CommandPalette filters actions and closes on Escape", async () => {
    const wrapper = mount(CommandPalette, { global: { plugins: plugins() } });
    const input = wrapper.find("input.palette-input");
    expect(input.exists()).toBe(true);
    await input.setValue("设置");
    const rows = wrapper.findAll(".palette-list li");
    expect(rows.some((row) => row.text().includes("设置"))).toBe(true);
    await input.trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("SplitPane renders a terminal pane for a leaf", () => {
    const workspace = useWorkspaceStore();
    const tab = workspace.createTab("s1", "bash");
    const root = tab.root;
    if (root.type !== "leaf") throw new Error("expected a leaf");
    const wrapper = mount(SplitPane, {
      props: { node: root, tabId: tab.id },
      global: {
        plugins: plugins(),
        stubs: { TerminalPane: { template: "<div class='stub-term' />" } },
      },
    });
    expect(wrapper.find(".stub-term").exists()).toBe(true);
  });
});
