<script lang="ts">
  import { onMount } from "svelte";
  import {
    Terminal,
    Search,
    ChevronDown,
    CircleCheck,
    CircleAlert,
    Clock3,
    Folder,
    Inbox,
    X,
  } from "@lucide/svelte";
  import { Button } from "$lib/components/ui/button";
  import { Input } from "$lib/components/ui/input";
  import { Skeleton } from "$lib/components/ui/skeleton";
  import * as Tabs from "$lib/components/ui/tabs";
  import * as Collapsible from "$lib/components/ui/collapsible";
  import StatusBadge from "$lib/components/StatusBadge.svelte";
  import { HostBridge } from "$lib/bridge";
  import {
    isActive,
    needsAttention,
    output,
    type Task,
    type TaskList,
    type TaskUpdate,
  } from "$lib/tasks";
  import { labels as t, statusLabel } from "$lib/labels";

  let tasks = $state<Task[]>([]);
  let selected = $state<string | null>(null);
  let detail = $state<Task | null>(null);
  let filter = $state("all");
  let query = $state("");
  let ready = $state(false);
  let loaded = $state(false);
  let detailBusy = $state(false);
  let expanded = $state(false);
  let error = $state("");
  let detailError = $state("");
  let updated = $state("");
  let now = $state(Date.now());
  let truncated = $state(false);
  let stopped = false;
  let revision = 0;
  let detailRequest = 0;
  let bridge: HostBridge;
  const standalone = window.parent === window;
  const active = $derived(tasks.filter(isActive).length);
  const attention = $derived(tasks.filter(needsAttention).length);
  const visible = $derived(
    tasks.filter(
      (task) =>
        (filter === "all" ||
          (filter === "active" ? isActive(task) : needsAttention(task))) &&
        [task.task_key, task.program, task.task_id]
          .join(" ")
          .toLowerCase()
          .includes(query.toLowerCase().trim()),
    ),
  );
  const summary = $derived(tasks.find((task) => task.task_id === selected));
  const elapsed = $derived(
    detail?.result?.elapsed_ms != null
      ? `${(detail.result.elapsed_ms / 1000).toFixed(1)}s`
      : detail?.started_at_ms
        ? `${Math.max(0, (now - detail.started_at_ms) / 1000).toFixed(1)}s`
        : "—",
  );

  function accept(result: TaskList) {
    if (!Array.isArray(result.tasks)) return;
    tasks = result.tasks;
    truncated = !!result.truncated;
    loaded = true;
    if (!tasks.some((task) => task.task_id === selected)) {
      ++detailRequest;
      detailBusy = false;
      selected = tasks[0]?.task_id ?? null;
      detail = null;
      detailError = "";
      expanded = false;
    }
  }
  async function loadDetail() {
    if (!selected || !ready || stopped) return;
    const id = selected,
      request = ++detailRequest;
    detailBusy = true;
    try {
      const result = await bridge.tool<Task>("get_task", { task_id: id });
      if (stopped || request !== detailRequest || selected !== id) return;
      detail = result;
      now = Date.now();
      detailError = "";
    } catch (cause) {
      if (!stopped && request === detailRequest && selected === id)
        detailError = String(cause);
    } finally {
      if (request === detailRequest) detailBusy = false;
    }
  }
  async function choose(id: string) {
    if (selected === id) return;
    selected = id;
    detail = null;
    detailError = "";
    expanded = false;
    await loadDetail();
  }
  function markUpdated() {
    error = "";
    updated = new Date().toLocaleTimeString("en", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }
  function receiveUpdate(update: TaskUpdate) {
    if (stopped) return;
    if (Array.isArray(update.tasks)) {
      ++revision;
      accept(update);
      markUpdated();
      void loadDetail();
    } else if (
      typeof update.task_id === "string" &&
      typeof update.status === "string"
    ) {
      ++revision;
      const existing = tasks.find((task) => task.task_id === update.task_id);
      const task: Task = {
        ...existing,
        ...update,
        task_id: update.task_id,
        status: update.status,
        delivery: update.delivery ?? existing?.delivery ?? "pending",
      };
      if (!existing && tasks.length >= 100) truncated = true;
      tasks = existing
        ? tasks.map((item) => (item.task_id === task.task_id ? task : item))
        : [task, ...tasks].slice(0, 100);
      if (!selected) selected = task.task_id;
      if (selected === task.task_id) {
        ++detailRequest;
        detailBusy = false;
        detail = { ...detail, ...task };
        detailError = "";
        now = Date.now();
      }
      loaded = true;
      markUpdated();
    }
  }
  async function initialize() {
    if (!loaded) {
      // A callback can carry newer data while the initial read is in flight.
      const initialRevision = revision;
      try {
        const result = await bridge.tool<TaskList>("list_tasks");
        if (stopped || revision !== initialRevision) return;
        accept(result);
        markUpdated();
      } catch (cause) {
        if (!stopped && revision === initialRevision) error = String(cause);
      }
    }
    await loadDetail();
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
      (message) => {
        error = message;
      },
    );
    if (!standalone)
      bridge
        .connect()
        .then(() => {
          if (!stopped) {
            ready = true;
            void initialize();
            requestAnimationFrame(reportSize);
          }
        })
        .catch((cause) => {
          if (!stopped) error = String(cause);
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
  <div class="filterbar">
    <Tabs.Root bind:value={filter}>
      <Tabs.List aria-label={t.tasks}>
        <Tabs.Trigger value="all"
          >{t.all}<span class="tab-count">{tasks.length}</span></Tabs.Trigger
        >
        <Tabs.Trigger value="active"
          >{t.active}<span class="tab-count">{active}</span></Tabs.Trigger
        >
        <Tabs.Trigger value="attention"
          >{t.attention}<span
            class:has-attention={attention > 0}
            class="tab-count">{attention}</span
          ></Tabs.Trigger
        >
      </Tabs.List>
    </Tabs.Root>
    <div class="search">
      <Search class="size-3.5" /><Input
        aria-label={t.search}
        placeholder={t.search}
        bind:value={query}
        class="h-7 pl-7 pr-7 text-xs"
      />{#if query}<Button
          variant="ghost"
          size="icon-xs"
          aria-label="Clear search"
          onclick={() => (query = "")}><X class="size-3" /></Button
        >{/if}
    </div>
  </div>
  <div class="workspace">
    <section class="task-list" aria-label={t.tasks}>
      {#if !loaded && !standalone && !error}
        <div class="loading" aria-label={t.loading}>
          {#each [1, 2, 3] as n (n)}<Skeleton class="h-11 w-full" />{/each}
        </div>
      {:else if !visible.length}
        <div class="empty">
          <Inbox class="size-5" /><span
            >{query || filter !== "all" ? t.noMatch : t.empty}</span
          >
        </div>
      {:else}
        {#each visible as task (task.task_id)}
          <button
            class="task-row"
            aria-pressed={selected === task.task_id}
            onclick={() => choose(task.task_id)}
          >
            <div class="task-title">
              <span class="task-name" title={task.task_key || task.task_id}
                >{task.task_key || task.task_id}</span
              ><StatusBadge value={task.status} />
            </div>
            <div class="task-sub">
              <span class="task-program" title={task.program}
                >{task.program || "—"}</span
              ><span
                class="delivery-icon"
                class:delivery-attention={needsAttention(task)}
                title={`${t.delivery}: ${statusLabel(task.delivery)}`}
                aria-label={`${t.delivery}: ${statusLabel(task.delivery)}`}
                >{#if task.delivery === "delivered"}<CircleCheck
                    class="size-3.5"
                  />{:else if needsAttention(task)}<CircleAlert
                    class="size-3.5"
                  />{:else}<Clock3 class="size-3.5" />{/if}</span
              >
            </div>
          </button>
        {/each}
      {/if}
    </section>
    <section
      id="detail"
      class="detail"
      aria-label={t.details}
      aria-busy={detailBusy}
    >
      {#if selected && summary}
        <div class="detail-heading">
          <strong title={summary.task_key || selected}
            >{summary.task_key || selected}</strong
          ><StatusBadge value={detail?.delivery || summary.delivery} />
        </div>
        <div class="detail-metrics">
          <span title={t.duration}><Clock3 class="size-3" />{elapsed}</span
          ><span>{t.exit} <b>{detail?.result?.exit_code ?? "—"}</b></span
          ><Collapsible.Root bind:open={expanded}
            ><Collapsible.Trigger class="details-trigger"
              >{t.details}<ChevronDown
                class={`size-3 transition-transform ${expanded ? "rotate-180" : ""}`}
              /></Collapsible.Trigger
            ><Collapsible.Content class="metadata"
              ><dl>
                <dt>{t.id}</dt>
                <dd>{selected}</dd>
                <dt>{t.status}</dt>
                <dd>
                  {statusLabel(detail?.status || summary.status)}
                </dd>
                <dt>{t.deadline}</dt>
                <dd>
                  {summary.timeout_ms
                    ? `${summary.timeout_ms / 1000}s`
                    : t.unlimited}
                </dd>
                {#if detail?.receipt?.turn?.id}<dt>{t.receipt}</dt>
                  <dd>{detail.receipt.turn.id}</dd>{/if}
              </dl></Collapsible.Content
            ></Collapsible.Root
          >
        </div>
        <div class="directory" title={summary.cwd}>
          <Folder class="size-3" /><span>{summary.cwd || "—"}</span>
        </div>
        {#if detailError}<div class="inline-error" role="alert">
            {detailError}
          </div>{/if}
        {#if detail?.delivery_error || detail?.launch_error?.error || detail?.error}<div
            class="inline-error"
            role="alert"
          >
            {detail.delivery_error ||
              detail.launch_error?.error ||
              detail.error}
          </div>{/if}
        <div class="output-label">
          <Terminal class="size-3" />{t.output}{#if detailBusy}<span
              class="loading-dot"
            ></span>{/if}
        </div>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (Scrollable output needs keyboard access.) -->
        <pre tabindex="0" role="region" aria-label={t.output}>{output(detail) ||
            (detailBusy && !detail ? t.loading : t.nooutput)}</pre>
      {:else}<div class="empty">
          <Terminal class="size-5" /><span>{t.select}</span>
        </div>{/if}
    </section>
  </div>
  <div
    class="statusbar"
    role="status"
    aria-live="polite"
    class:is-error={!!error}
  >
    <span class="connection-dot" class:connected={ready && !error}></span>
    <span class="notice" title={error}
      >{error
        ? `${t.failed}: ${error}`
        : standalone
          ? t.standalone
          : !ready
            ? t.connecting
            : updated
              ? `${t.updated} ${updated}`
              : t.loading}</span
    >
    {#if truncated}<span class="status-extra">{t.truncated}</span>{/if}
  </div>
</main>
