const API = "/api/v2";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function onUnauthorized(handler: () => void): void {
  unauthorizedHandler = handler;
}

export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = await response.json();
    if (body && typeof body.detail === "string") detail = body.detail;
    else if (body && body.detail) detail = JSON.stringify(body.detail);
  } catch {
    /* not JSON */
  }
  return new ApiError(response.status, detail);
}

export async function upload(
  path: string,
  file: File,
  options: {
    overwrite?: boolean;
    dir?: string;
    onProgress?: (ratio: number) => void;
    signal?: AbortSignal;
  } = {},
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file, file.name);
    if (options.overwrite) form.append("overwrite", "true");
    if (options.dir) form.append("path", options.dir);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}${path}`);
    xhr.withCredentials = true;
    if (options.signal) {
      if (options.signal.aborted) {
        reject(new ApiError(0, "上传已取消"));
        return;
      }
      options.signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && options.onProgress) {
        options.onProgress(event.loaded / event.total);
      }
    };
    xhr.onabort = () => reject(new ApiError(0, "上传已取消"));
    xhr.onload = () => {
      if (xhr.status === 401) notifyUnauthorized();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText || "{}"));
        } catch {
          resolve({});
        }
      } else {
        let detail = `HTTP ${xhr.status}`;
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail;
        } catch {
          /* ignore */
        }
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "上传失败：网络错误"));
    xhr.send(form);
  });
}

export async function download(path: string, filename?: string): Promise<void> {
  const response = await fetch(`${API}${path}`, { credentials: "same-origin" });
  if (response.status === 401) notifyUnauthorized();
  if (!response.ok) throw await parseError(response);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename ?? "";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
