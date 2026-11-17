import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";
import { fileURLToPath } from "node:url";

export default defineConfig({
  root: fileURLToPath(new URL("./ui", import.meta.url)),
  resolve: {
    alias: { $lib: fileURLToPath(new URL("./ui/src/lib", import.meta.url)) },
  },
  plugins: [tailwindcss(), svelte(), viteSingleFile()],
  build: { target: "es2022" },
});
