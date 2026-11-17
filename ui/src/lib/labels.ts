export const labels = {
  title: "Background task",
  details: "Details",
  select: "Waiting for this tool’s task…",
  connecting: "Connecting…",
  standalone: "Open in an MCP Apps host",
  updated: "Updated",
  unlimited: "No deadline",
  deadline: "Deadline",
  delivery: "Delivery",
  exit: "Exit",
  duration: "Duration",
  output: "Output",
  nooutput: "Waiting for output…",
  failed: "Update failed",
  originalChat: "Task belongs to the original chat",
  originalChatHelp: "Forking a chat does not transfer its background tasks. Open the original chat to view this task’s current state. Results are still delivered there. Do not resubmit the command to recover its status.",
  status: "Status",
  id: "Task ID",
  cwd: "Directory",
  receipt: "Delivery turn",
  loading: "Loading…",
};

export function statusLabel(value: string) {
  return value.replaceAll("_", " ");
}
