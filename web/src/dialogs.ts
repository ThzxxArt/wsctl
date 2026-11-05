import { reactive } from "vue";

/**
 * Promise-based replacements for `window.confirm` / `window.prompt`.
 *
 * A single <AppDialog> host renders whatever is queued here. Calls made while
 * another dialog is open wait in FIFO order, matching the blocking behaviour
 * of the native dialogs without freezing the event loop.
 */

export interface ConfirmOptions {
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
}

export interface PromptOptions {
  title?: string;
  message?: string;
  defaultValue?: string;
  placeholder?: string;
  type?: "text" | "password";
  minLength?: number;
  required?: boolean;
  /** Ask for the value a second time; useful for new passwords. */
  confirmValue?: boolean;
  confirmPlaceholder?: string;
  confirmText?: string;
  cancelText?: string;
  validate?: (value: string) => string | null;
}

interface ConfirmRequest extends ConfirmOptions {
  id: number;
  kind: "confirm";
}

export interface PromptRequest extends PromptOptions {
  id: number;
  kind: "prompt";
}

export type DialogRequest = ConfirmRequest | PromptRequest;

type Resolver = (value: boolean | string | null) => void;

const state = reactive<{ current: DialogRequest | null }>({ current: null });
const waiting: Array<{ request: DialogRequest; resolve: Resolver }> = [];
let nextId = 1;
let settleCurrent: Resolver | null = null;

function enqueue(request: DialogRequest, resolve: Resolver): void {
  if (state.current) {
    waiting.push({ request, resolve });
    return;
  }
  state.current = request;
  settleCurrent = resolve;
}

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    enqueue({ ...options, id: nextId++, kind: "confirm" }, (value) => {
      resolve(value === true);
    });
  });
}

export function promptDialog(options: PromptOptions = {}): Promise<string | null> {
  return new Promise<string | null>((resolve) => {
    enqueue(
      {
        defaultValue: "",
        required: true,
        ...options,
        id: nextId++,
        kind: "prompt",
      },
      (value) => {
        resolve(typeof value === "string" ? value : null);
      },
    );
  });
}

/**
 * Settle the visible dialog and reveal the next queued one.
 *
 * The next request is made current *before* resolving the promise so a
 * `.then()` handler can open another dialog without racing the queue.
 */
export function resolveDialog(value: boolean | string | null): void {
  const resolve = settleCurrent;
  const next = waiting.shift();
  state.current = next?.request ?? null;
  settleCurrent = next?.resolve ?? null;
  resolve?.(value);
}

/** Reactive state consumed by <AppDialog>. */
export function dialogState(): { current: DialogRequest | null } {
  return state;
}

/**
 * Cancel every open or queued dialog. Logout uses this so a password prompt
 * cannot stay on screen (and resolve against the next account) after the
 * session it belonged to is gone.
 */
export function resetDialogs(): void {
  const resolve = settleCurrent;
  const pending = waiting.splice(0);
  state.current = null;
  settleCurrent = null;
  resolve?.(false);
  for (const item of pending) item.resolve(false);
}
