export interface Task {
  task_id: string;
  task_key?: string;
  program?: string;
  cwd?: string;
  timeout_ms?: number | null;
  status: string;
  delivery: string;
  started_at_ms?: number;
  result?: {
    exit_code?: number | null;
    elapsed_ms?: number;
    message?: string;
    output_tail?: Output;
  };
  output_tail?: Output;
  receipt?: { turn?: { id?: string } };
  delivery_error?: string;
  launch_error?: { error?: string };
  error?: string;
}
interface Output {
  stdout?: string;
  stderr?: string;
}
export interface TaskList {
  tasks?: Task[];
  truncated?: boolean;
  revision?: string;
}
export type TaskUpdate = TaskList & Partial<Task>;
export const isActive = (task: Task) =>
  ["queued", "submitted", "running"].includes(task.status);
export const needsAttention = (task: Task) =>
  ["failed", "uncertain", "sending"].includes(task.delivery);
export const isFailure = (value: string) =>
  [
    "failed",
    "launch_failed",
    "worker_failed",
    "timed_out",
    "uncertain",
  ].includes(value);
export function output(task: Task | null) {
  const tail = task?.result?.output_tail || task?.output_tail;
  return [tail?.stdout, tail?.stderr, task?.result?.message]
    .filter(Boolean)
    .join("\n");
}
