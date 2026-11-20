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

<Trace.Root class="output-terminal">
  <Trace.Collapsible open>
    <Trace.CollapsibleTrigger class="output-trigger w-full justify-between px-3 py-1.5">
      <Trace.Line
        class="gap-1.5 font-mono text-xs"
        status={failed ? "error" : task.status === "running" ? "running" : undefined}
      >
        <Trace.Icon class="size-3.5"><Terminal class="size-3.5" /></Trace.Icon>
        <Trace.Title title={title}>{title}</Trace.Title>
      </Trace.Line>
    </Trace.CollapsibleTrigger>
    <Trace.CollapsibleContent class="m-0 border-0 p-0">
      <CodeBlock.Root size="sm" class="rounded-none border-0 dark:bg-transparent">
        <CodeBlock.Content
          code={commandOutput || (detailBusy ? t.loading : t.nooutput)}
          label={t.output}
          class={cn("max-h-30", !commandOutput && "text-muted-foreground")}
        />
      </CodeBlock.Root>
    </Trace.CollapsibleContent>
  </Trace.Collapsible>
</Trace.Root>
