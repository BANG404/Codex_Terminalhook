<script lang="ts">
  import { Badge } from "$lib/components/ui/badge";
  import { isFailure } from "$lib/tasks";
  import { statusLabel, type Language } from "$lib/i18n";
  let { value, language }: { value: string; language: Language } = $props();
</script>

<Badge
  variant="secondary"
  data-status={value}
  class={`status-badge ${isFailure(value) ? "status-error" : value === "running" || value === "delivered" ? "status-success" : ""}`}
>
  <span
    class:animate-pulse={value === "running"}
    class="size-1.5 rounded-full bg-current"
    aria-hidden="true"
  ></span>
  {statusLabel(value, language)}
</Badge>
