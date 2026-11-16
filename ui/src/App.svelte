<script lang="ts">
  import { onMount } from "svelte";
  import {
    Terminal,
    RefreshCw,
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
  import { Switch } from "$lib/components/ui/switch";
  import { Skeleton } from "$lib/components/ui/skeleton";
  import * as Tabs from "$lib/components/ui/tabs";
  import * as Collapsible from "$lib/components/ui/collapsible";
  import * as Tooltip from "$lib/components/ui/tooltip";
  import StatusBadge from "$lib/components/StatusBadge.svelte";
  import IconButton from "$lib/components/IconButton.svelte";
  import { HostBridge } from "$lib/bridge";
  import {
    isActive,
    needsAttention,
    output,
    type Task,
    type TaskList,
  } from "$lib/tasks";
  import { words, statusLabel, type Language } from "$lib/i18n";

  let language = $state<Language>(
    navigator.language.startsWith("zh") ? "zh" : "en",
  );
  let tasks = $state<Task[]>([]);
  let selected = $state<string | null>(null);
  let detail = $state<Task | null>(null);
  let filter = $state("all");
  let query = $state("");
  let auto = $state(true);
  let ready = $state(false);
  let loaded = $state(false);
  let busy = $state(false);
  let detailBusy = $state(false);
  let expanded = $state(false);
  let error = $state("");
  let detailError = $state("");
  let updated = $state("");
  let now = $state(Date.now());
  let truncated = $state(false);
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let detailRequest = 0;
  let bridge: HostBridge;
  const standalone = window.parent === window;
  const t = $derived(words[language]);
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

  $effect(() => {
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  });
  function schedule(delay = 3000) {
    clearTimeout(timer);
    if (auto && ready && !stopped && !document.hidden)
      timer = setTimeout(refresh, delay);
  }
  function accept(result: TaskList) {
    if (!Array.isArray(result.tasks)) return;
    tasks = result.tasks;
    truncated = !!result.truncated;
    loaded = true;
    if (!tasks.some((task) => task.task_id === selected)) {
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
  async function refresh() {
    if (!ready || busy || stopped || document.hidden) return;
    clearTimeout(timer);
    busy = true;
    let delay = 3000;
    try {
      const result = await bridge.tool<TaskList>("list_tasks");
      if (stopped) return;
      accept(result);
      await loadDetail();
      error = "";
      updated = new Date().toLocaleTimeString(
        language === "zh" ? "zh-CN" : "en",
        { hour: "2-digit", minute: "2-digit", second: "2-digit" },
      );
    } catch (cause) {
      if (!stopped) error = String(cause);
      delay = 15000;
    } finally {
      busy = false;
      schedule(delay);
    }
  }
  function toggleAuto(value: boolean) {
    auto = value;
    auto ? void refresh() : clearTimeout(timer);
  }
  onMount(() => {
    document.documentElement.classList.toggle(
      "dark",
      matchMedia("(prefers-color-scheme: dark)").matches,
    );
    bridge = new HostBridge(
      (result) => {
        if (!stopped) {
          accept(result);
          void refresh();
        }
      },
      () => {
        stopped = true;
        ready = false;
        clearTimeout(timer);
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
            void refresh();
            requestAnimationFrame(reportSize);
          }
        })
        .catch((cause) => {
          if (!stopped) error = String(cause);
        });
    const visibility = () => {
      document.hidden ? clearTimeout(timer) : auto && void refresh();
    };
    const dispose = () => bridge.dispose();
    document.addEventListener("visibilitychange", visibility);
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
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", dispose);
    };
  });
</script>

<Tooltip.Provider delayDuration={250}>
  <main class="dashboard" aria-label={t.title}>
    <header class="header">
      <div class="brand">
        <span class="brand-icon"><Terminal class="size-4" /></span>
        <h1>{t.title}</h1>
        <span class="total">{tasks.length}</span>
      </div>
      <div class="controls">
        <select id="language" aria-label="Language" bind:value={language}
          ><option value="en">EN</option><option value="zh">中文</option
          ></select
        >
        <span class="control-divider"></span>
        <label class="auto-control" for="auto"
          ><span>{t.auto}</span><Switch
            id="auto"
            size="sm"
            checked={auto}
            onCheckedChange={toggleAuto}
            aria-label={t.auto}
          /></label
        >
        <IconButton
          label={t.refresh}
          disabled={!ready || busy}
          onclick={refresh}
          ><RefreshCw
            class={`size-3.5 ${busy ? "animate-spin" : ""}`}
          /></IconButton
        >
      </div>
    </header>
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
            aria-label={language === "zh" ? "清除搜索" : "Clear search"}
            onclick={() => (query = "")}><X class="size-3" /></Button
          >{/if}
      </div>
    </div>
    <div class="workspace">
      <section class="task-list" aria-label={t.tasks} aria-busy={busy}>
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
                ><StatusBadge value={task.status} {language} />
              </div>
              <div class="task-sub">
                <span class="task-program" title={task.program}
                  >{task.program || "—"}</span
                ><span
                  class="delivery-icon"
                  class:delivery-attention={needsAttention(task)}
                  title={`${t.delivery}: ${statusLabel(task.delivery, language)}`}
                  aria-label={`${t.delivery}: ${statusLabel(task.delivery, language)}`}
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
            ><StatusBadge
              value={detail?.delivery || summary.delivery}
              {language}
            />
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
                    {statusLabel(detail?.status || summary.status, language)}
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
              {detailError}<Button
                variant="ghost"
                size="xs"
                onclick={loadDetail}>{t.retry}</Button
              >
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
          <pre tabindex="0" role="region" aria-label={t.output}>{output(
              detail,
            ) || (detailBusy && !detail ? t.loading : t.nooutput)}</pre>
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
      {#if !auto}<span class="status-extra">{t.paused}</span>{/if}
    </div>
  </main>
</Tooltip.Provider>
