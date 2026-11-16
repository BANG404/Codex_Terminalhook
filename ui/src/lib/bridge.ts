// Host-mediated MCP Apps transport. No network or model-message capability.
import type { TaskList } from "./tasks";

interface ToolResult {
  isError?: boolean;
  structuredContent?: unknown;
  content?: { type: string; text?: string }[];
}
function data<T>(result: ToolResult): T {
  const text = result.content?.find((part) => part.type === "text")?.text;
  if (result.isError) throw new Error(text || "Tool failed");
  return (result.structuredContent ?? (text ? JSON.parse(text) : {})) as T;
}
export class HostBridge {
  private origin = "*";
  private nextId = 0;
  private stopped = false;
  private pending = new Map<
    number,
    {
      resolve: (value: any) => void;
      reject: (error: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  constructor(
    private onTasks: (tasks: TaskList) => void,
    private onStop: () => void,
    private onError: (message: string) => void,
  ) {
    window.addEventListener("message", this.receive);
  }
  post(message: object) {
    window.parent.postMessage({ jsonrpc: "2.0", ...message }, this.origin);
  }
  private rpc<T>(method: string, params: object): Promise<T> {
    if (this.stopped) return Promise.reject(new Error("Panel closed"));
    return new Promise((resolve, reject) => {
      const id = ++this.nextId;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("Host response timed out"));
      }, 20000);
      this.pending.set(id, { resolve, reject, timer });
      this.post({ id, method, params });
    });
  }
  async connect() {
    const result = await this.rpc<{ hostContext?: { theme?: string } }>(
      "ui/initialize",
      {
        appInfo: { name: "TerminalHook tasks", version: "2.0.0" },
        appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] },
        protocolVersion: "2026-01-26",
      },
    );
    this.theme(result.hostContext?.theme);
    this.post({ method: "ui/notifications/initialized", params: {} });
  }
  async tool<T>(name: "list_tasks" | "get_task", args: object = {}) {
    return data<T>(
      await this.rpc<ToolResult>("tools/call", { name, arguments: args }),
    );
  }
  private theme(theme?: string) {
    if (theme !== "dark" && theme !== "light") return;
    document.documentElement.dataset.theme = theme;
    document.documentElement.classList.toggle("dark", theme === "dark");
  }
  private receive = (event: MessageEvent) => {
    if (this.stopped || event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== "2.0") return;
    if (this.origin === "*" && event.origin && event.origin !== "null")
      this.origin = event.origin;
    if (this.origin !== "*" && event.origin !== this.origin) return;
    if (message.id != null && !message.method) {
      const entry = this.pending.get(message.id);
      if (!entry) return;
      clearTimeout(entry.timer);
      this.pending.delete(message.id);
      message.error
        ? entry.reject(new Error(message.error.message || "Host error"))
        : entry.resolve(message.result);
    } else if (message.method === "ui/notifications/host-context-changed")
      this.theme(message.params?.theme);
    else if (message.method === "ui/notifications/tool-result") {
      try {
        this.onTasks(data<TaskList>(message.params));
      } catch (error) {
        this.onError(String(error));
      }
    } else if (message.method === "ping")
      this.post({ id: message.id, result: {} });
    else if (message.method === "ui/resource-teardown") {
      this.post({ id: message.id, result: {} });
      this.dispose();
    }
  };
  dispose() {
    if (this.stopped) return;
    this.stopped = true;
    window.removeEventListener("message", this.receive);
    for (const entry of this.pending.values()) {
      clearTimeout(entry.timer);
      entry.reject(new Error("Panel closed"));
    }
    this.pending.clear();
    this.onStop();
  }
}
