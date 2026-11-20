<script lang="ts">
  import { ScrollArea } from "$lib/components/ui/scroll-area";

  let {
    code,
    label,
    class: className,
  }: { code: string; label: string; class?: string } = $props();
</script>

<ScrollArea
  viewportProps={{ class: className, role: "region", "aria-label": label, tabindex: 0 }}
>
  <pre
    data-slot="code-block-content"
    dir="ltr"
    class="min-w-0 px-3 py-2 pr-4 text-start font-mono leading-relaxed wrap-anywhere whitespace-pre-wrap"
  ><code data-slot="code-block-code">{code}</code></pre>
</ScrollArea>
