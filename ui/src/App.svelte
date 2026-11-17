<script lang="ts">
  import { onMount } from "svelte";
  import { Terminal, ChevronDown, Clock3, Folder } from "@lucide/svelte";
  import * as Collapsible from "$lib/components/ui/collapsible";
  import StatusBadge from "$lib/components/StatusBadge.svelte";
  import { HostBridge, HostResponseTimeout, TaskChatMismatch } from "$lib/bridge";
  import { output, type Task, type TaskUpdate } from "$lib/tasks";
  import { labels as t, statusLabel } from "$lib/labels";

  let task = $state<Task | null>(null);
  let ready = $state(false);
  let detailBusy = $state(false);
  let expanded = $state(false);
  let error = $state("");
  let foreignTask = $state(false);
  let updated = $state("");
  let now = $state(Date.now());
  let stopped = false;
  let generation = 0;
  let taskRevision = "";
  let syncing = false;
  let bridge: HostBridge;
  const standalone = window.parent === window;
  const elapsed = $derived(
    task?.result?.elapsed_ms != null
      ? `${(task.result.elapsed_ms / 1000).toFixed(1)}s`
      : task?.started_at_ms
        ? `${Math.max(0, (now - task.started_at_ms) / 1000).toFixed(1)}s`
        : "—",
  );

  function handleError(cause: unknown) {
    if (stopped) return;
    if (cause instanceof TaskChatMismatch) {
      foreignTask = true;
      error = "";
      // A fork can replay the original tool callbacks. Permanently stop this
      // panel, including pending calls, without changing the original job.
      ++generation;
      bridge.dispose();
    } else {
      error = String(cause);
    }
  }
  function accept(update: TaskUpdate) {
    task = {
      ...task,
      ...update,
      task_id: update.task_id!,
      status: update.status ?? task?.status ?? "submitted",
      delivery: update.delivery ?? task?.delivery ?? "pending",
    };
    taskRevision = update.revision ?? "";
    now = Date.now();
    error = "";
    updated = new Date().toLocaleTimeString("en", {
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  }
  function receiveUpdate(update: TaskUpdate) {
    // Bind this resource to its originating tool's task. Chat snapshots and
    // callbacks for other tasks must never populate or switch this panel.
    if (stopped || Array.isArray(update.tasks) || typeof update.task_id !== "string") return;
    if (task && task.task_id !== update.task_id) return;
    // Hosts may echo app calls; identical snapshots must not restart reads.
    if (update.revision && update.revision === taskRevision) {
      // A direct get callback can carry fresh output with the same saved-state
      // revision. Merge it without starting another read or subscription.
      accept(update);
      void synchronize();
      return;
    }
    ++generation;
    accept(update);
    void synchronize();
  }
  async function synchronize() {
    if (syncing || !ready || stopped || !task) return;
    syncing = true;
    try {
      while (!stopped && ready && task) {
        const before = generation;
        const id = task.task_id;
        const after = taskRevision;
        detailBusy = !after;
        let update: Task;
        try {
          update = after
            ? await bridge.tool<Task>("watch_tasks", { task_id: id, after_revision: after })
            : await bridge.tool<Task>("get_task", { task_id: id });
        } catch (cause) {
          if (cause instanceof TaskChatMismatch) throw cause;
          if (before !== generation && !stopped) continue;
          // Renew only the bounded transport subscription, without a data read.
          if (!stopped && after && cause instanceof HostResponseTimeout) continue;
          throw cause;
        }
        if (stopped) return;
        if (before !== generation) continue;
        if (update.task_id !== id || !update.revision || (after && update.revision === after))
          throw new Error("Invalid task event response");
        accept(update);
        detailBusy = false;
      }
    } catch (cause) {
      handleError(cause);
    } finally {
      detailBusy = false;
      syncing = false;
    }
  }
  onMount(() => {
    document.documentElement.classList.toggle(
      "dark",
      matchMedia("(prefers-color-scheme: dark)").matches,
    );
    document.documentElement.lang = "en";
    bridge = new HostBridge(
      receiveUpdate,
      () => {
        stopped = true;
        ready = false;
      },
      handleError,
    );
    if (!standalone)
      bridge
        .connect()
        .then(() => {
          if (!stopped) {
            ready = true;
            void synchronize();
            requestAnimationFrame(reportSize);
          }
        })
        .catch((cause) => {
          handleError(cause);
        });
    const dispose = () => bridge.dispose();
    window.addEventListener("pagehide", dispose);
    let size = "";
    function reportSize() {
      const height = Math.ceil(document.body.getBoundingClientRect().height),
        width = document.documentElement.clientWidth;
      if (ready && size !== `${width}:${height}`) {
        size = `${width}:${height}`;
        bridge.post({
          method: "ui/notifications/size-changed",
          params: { height, width },
        });
      }
    }
    const observer = new ResizeObserver(reportSize);
    observer.observe(document.body);
    return () => {
      dispose();
      observer.disconnect();
      window.removeEventListener("pagehide", dispose);
    };
  });
</script>

<main class="dashboard" aria-label={t.title}>
    <section
      id="detail"
      class="detail"
      aria-label={t.details}
      aria-busy={detailBusy}
    >
      {#if foreignTask}
        <div class="original-chat" role="status">
          <strong>{t.originalChat}</strong>
          <p>{t.originalChatHelp}</p>
        </div>
      {:else if task}
        <div class="detail-heading">
          <strong title={task.task_key || task.task_id}
            >{task.task_key || task.task_id}</strong
          ><div class="task-badges"><StatusBadge value={task.status} /><span aria-label={`${t.delivery}: ${statusLabel(task.delivery)}`}><StatusBadge value={task.delivery} /></span></div>
        </div>
        <div class="detail-metrics">
          <span title={t.duration}><Clock3 class="size-3" />{elapsed}</span
          ><span>{t.exit} <b>{task?.result?.exit_code ?? "—"}</b></span
          ><Collapsible.Root bind:open={expanded}
            ><Collapsible.Trigger class="details-trigger"
              >{t.details}<ChevronDown
                class={`size-3 transition-transform ${expanded ? "rotate-180" : ""}`}
              /></Collapsible.Trigger
            ><Collapsible.Content class="metadata"
              ><dl>
                <dt>{t.id}</dt>
                <dd>{task.task_id}</dd>
                <dt>{t.status}</dt>
                <dd>
                  {statusLabel(task.status)}
                </dd>
                <dt>{t.deadline}</dt>
                <dd>
                  {task.timeout_ms
                    ? `${task.timeout_ms / 1000}s`
                    : t.unlimited}
                </dd>
                {#if task?.receipt?.turn?.id}<dt>{t.receipt}</dt>
                  <dd>{task.receipt.turn.id}</dd>{/if}
              </dl></Collapsible.Content
            ></Collapsible.Root
          >
        </div>
        <div class="directory" title={task.cwd}>
          <Folder class="size-3" /><span>{task.cwd || "—"}</span>
        </div>
        {#if task?.delivery_error || task?.launch_error?.error || task?.error}<div
            class="inline-error"
            role="alert"
          >
            {task.delivery_error ||
              task.launch_error?.error ||
              task.error}
          </div>{/if}
        <div class="output-label">
          <Terminal class="size-3" />{t.output}{#if detailBusy}<span
              class="loading-dot"
            ></span>{/if}
        </div>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (Scrollable output needs keyboard access.) -->
        <pre tabindex="0" role="region" aria-label={t.output}>{output(task) ||
            (detailBusy ? t.loading : t.nooutput)}</pre>
      {:else}<div class="empty">
          <Terminal class="size-5" /><span>{t.select}</span>
        </div>{/if}
    </section>
  <div
    class="statusbar"
    role="status"
    aria-live="polite"
    class:is-error={!!error}
  >
    <span class="connection-dot" class:connected={ready && !error && !foreignTask}></span>
    <span class="notice" title={error}
      >{foreignTask
        ? t.originalChat
        : error
        ? `${t.failed}: ${error}`
        : standalone
          ? t.standalone
          : !ready
            ? t.connecting
            : updated
              ? `${t.updated} ${updated}`
              : t.select}</span
    >
  </div>
</main>
