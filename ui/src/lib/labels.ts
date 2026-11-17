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
  status: "Status",
  id: "Task ID",
  cwd: "Directory",
  receipt: "Delivery turn",
  loading: "Loading…",
};

export function statusLabel(value: string) {
  return value.replaceAll("_", " ");
}
