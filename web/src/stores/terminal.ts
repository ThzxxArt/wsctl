import { defineStore } from "pinia";
import { ref } from "vue";
import type { CommandBlock } from "@/api/types";
import type { ConnectionState } from "@/protocol/client";

interface TerminalMeta {
  state: ConnectionState;
  detail?: string;
  cols: number;
  rows: number;
  command?: string;
  cwd?: string | null;
  //: The session process's exit code (only meaningful once ``state`` is
  //: ``exited``). A command block's code is a different fact and must not
  //: overwrite this.
  exitCode?: number | null;
  lastExitCode?: number | null;
  durationMs?: number | null;
  bell?: number;
}

export const useTerminalStore = defineStore("terminal", () => {
  const meta = ref<Record<string, TerminalMeta>>({});
  const commands = ref<Record<string, CommandBlock[]>>({});

  function ensure(session: string): TerminalMeta {
    if (!meta.value[session]) {
      meta.value[session] = { state: "idle", cols: 80, rows: 24 };
    }
    return meta.value[session];
  }

  function setState(session: string, state: ConnectionState, detail?: string): void {
    const entry = ensure(session);
    entry.state = state;
    entry.detail = detail;
  }

  function setSize(session: string, cols: number, rows: number): void {
    const entry = ensure(session);
    entry.cols = cols;
    entry.rows = rows;
  }

  function setCommand(session: string, command: string, cwd?: string | null): void {
    const entry = ensure(session);
    entry.command = command;
    if (cwd !== undefined) entry.cwd = cwd;
  }

  function setExit(session: string, code: number | null): void {
    const entry = ensure(session);
    entry.exitCode = code;
    entry.state = "exited";
  }

  function ring(session: string): void {
    ensure(session).bell = Date.now();
  }

  function addCommand(session: string, block: CommandBlock): void {
    const list = commands.value[session] ?? [];
    const index = list.findIndex((item) => item.id === block.id);
    if (index >= 0) list[index] = block;
    else list.push(block);
    if (list.length > 500) list.splice(0, list.length - 500);
    commands.value[session] = [...list];
    const entry = ensure(session);
    entry.command = block.command;
    if (block.cwd) entry.cwd = block.cwd;
    entry.durationMs = block.duration_ms;
    if (block.status !== "running") entry.lastExitCode = block.exit_code;
  }

  return { meta, commands, ensure, setState, setSize, setCommand, setExit, ring, addCommand };
});
