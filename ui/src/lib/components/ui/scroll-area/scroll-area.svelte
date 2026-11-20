<script lang="ts">
  import { ScrollArea as ScrollAreaPrimitive } from "bits-ui";
  import { cn } from "$lib/utils";

  let {
    class: className,
    type = "auto",
    viewportProps = {},
    children,
    ...restProps
  }: ScrollAreaPrimitive.RootProps & {
    viewportProps?: Omit<ScrollAreaPrimitive.ViewportProps, "children" | "ref">;
  } = $props();
</script>

<ScrollAreaPrimitive.Root
  data-slot="scroll-area"
  class={cn("relative overflow-hidden", className)}
  {type}
  {...restProps}
>
  <ScrollAreaPrimitive.Viewport
    {...viewportProps}
    data-slot="scroll-area-viewport"
    class={cn(
      "w-full rounded-[inherit] focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
      viewportProps.class,
    )}
  >
    {@render children?.()}
  </ScrollAreaPrimitive.Viewport>
  <ScrollAreaPrimitive.Scrollbar
    data-slot="scroll-area-scrollbar"
    orientation="vertical"
    class="flex w-2.5 touch-none p-0.5 select-none"
  >
    <ScrollAreaPrimitive.Thumb
      data-slot="scroll-area-thumb"
      class="relative flex-1 rounded-full bg-[var(--scrollbar-thumb,var(--muted-foreground))]"
    />
  </ScrollAreaPrimitive.Scrollbar>
</ScrollAreaPrimitive.Root>
