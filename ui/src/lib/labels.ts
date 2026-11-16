export const labels = {
  title: "Background tasks",
  all: "All",
  active: "Active",
  attention: "Attention",
  tasks: "Tasks in this chat",
  details: "Details",
  search: "Find a task…",
  empty: "No tasks yet",
  noMatch: "No matching tasks",
  select: "Select a task",
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
  truncated: "First 100 tasks",
  loading: "Loading…",
};

export function statusLabel(value: string) {
  return value.replaceAll("_", " ");
}
