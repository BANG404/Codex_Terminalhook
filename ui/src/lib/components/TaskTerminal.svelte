<script lang="ts">
  import { Terminal } from "@lucide/svelte";
  import * as CodeBlock from "$lib/components/ui/code-block";
  import * as Trace from "$lib/components/ui/trace";
  import { labels as t, statusLabel } from "$lib/labels";
  import { isFailure, output, type Task } from "$lib/tasks";
  import { cn } from "$lib/utils";

  let { task, detailBusy }: { task: Task; detailBusy: boolean } = $props();
  const commandOutput = $derived(output(task));
  const program = $derived(task.program?.split(/[\\/]/).pop() || task.program);
  const failed = $derived(isFailure(task.status));
  const title = $derived.by(() => {
    if (!program) return t.output;
    if (task.status === "running") return `Running ${program}`;
    if (task.status === "completed") return `Ran ${program}`;
    if (task.status === "failed" && task.result?.exit_code != null) {
      return `${program} exited with code ${task.result.exit_code}`;
    }
    return `${program} · ${statusLabel(task.status)}`;
  });
</script>

<Trace.Root class="task-terminal mt-3 max-w-xl">
  <Trace.Collapsible open>
    <Trace.CollapsibleTrigger class="py-0.5 [&>[data-slot=trace-icon]]:size-3 [&>[data-slot=trace-icon]>svg]:size-3">
      <Trace.Line
        class="gap-1.5 text-xs"
        status={failed ? "error" : task.status === "running" ? "running" : undefined}
      >
        <Trace.Icon class="size-3.5"><Terminal class="size-3" /></Trace.Icon>
        <Trace.Title title={title}>{title}</Trace.Title>
      </Trace.Line>
    </Trace.CollapsibleTrigger>
    <Trace.CollapsibleContent class="mt-1.5 pr-0 pl-3.5">
      <CodeBlock.Root size="sm" class="rounded-xl border-0 bg-muted dark:bg-muted">
        <CodeBlock.Content
          code={commandOutput || (detailBusy ? t.loading : t.nooutput)}
          label={t.output}
          class={cn("max-h-38 text-[11px] sm:max-h-44", !commandOutput && "text-muted-foreground")}
        />
      </CodeBlock.Root>
    </Trace.CollapsibleContent>
  </Trace.Collapsible>
</Trace.Root>
