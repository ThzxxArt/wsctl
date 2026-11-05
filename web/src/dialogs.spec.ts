import { beforeEach, describe, expect, it } from "vitest";
import { confirmDialog, dialogState, promptDialog, resetDialogs, resolveDialog } from "@/dialogs";

function drain(): void {
  while (dialogState().current) resolveDialog(null);
}

describe("dialog queue", () => {
  beforeEach(drain);

  it("resolves a confirm with a boolean", async () => {
    const answer = confirmDialog({ message: "继续？" });
    expect(dialogState().current?.kind).toBe("confirm");
    resolveDialog(true);
    await expect(answer).resolves.toBe(true);
    expect(dialogState().current).toBeNull();
  });

  it("returns false when a confirm is cancelled", async () => {
    const answer = confirmDialog({ message: "继续？" });
    resolveDialog(false);
    await expect(answer).resolves.toBe(false);
  });

  it("returns null when a prompt is cancelled", async () => {
    const answer = promptDialog({ message: "名称" });
    resolveDialog(null);
    await expect(answer).resolves.toBeNull();
  });

  it("queues a second dialog until the first settles", async () => {
    const first = confirmDialog({ message: "first" });
    const second = promptDialog({ message: "second", defaultValue: "x" });
    expect(dialogState().current?.message).toBe("first");

    resolveDialog(false);
    await expect(first).resolves.toBe(false);
    expect(dialogState().current?.kind).toBe("prompt");
    expect(dialogState().current?.message).toBe("second");

    resolveDialog("typed");
    await expect(second).resolves.toBe("typed");
    expect(dialogState().current).toBeNull();
  });

  it("cancels the current and queued dialogs on logout", async () => {
    const first = promptDialog({ message: "密码", type: "password" });
    const second = confirmDialog({ message: "删除？" });
    resetDialogs();
    await expect(first).resolves.toBeNull();
    await expect(second).resolves.toBe(false);
    expect(dialogState().current).toBeNull();
  });

  it("shows a dialog opened from a resolution handler without losing it", async () => {
    const first = confirmDialog({ message: "first" });
    const chained = first.then(() => confirmDialog({ message: "second" }));

    resolveDialog(true);
    await expect(first).resolves.toBe(true);
    expect(dialogState().current?.message).toBe("second");

    resolveDialog(false);
    await expect(chained).resolves.toBe(false);
    expect(dialogState().current).toBeNull();
  });
});
