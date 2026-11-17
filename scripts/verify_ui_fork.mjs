import assert from "node:assert/strict";

// Forks replay tool history under a new host-provided chat ID. Test both the
// direct response and host callback paths, including older plugin error shapes.
export async function verifyFork(browser, html, denied) {
  const message = "task belongs to a different chat";
  const legacy = { isError: true, content: [{ type: "text", text: JSON.stringify({ status: "error", message }) }] };
  for (const [scenario, failure] of [
    ["get", denied], ["watch", denied], ["callback", denied],
    ["legacy-json", legacy],
    ["legacy-structured", { isError: true, structuredContent: { status: "error", message } }],
    ["legacy-text", { isError: true, content: [{ type: "text", text: message }] }],
    ["other-error", { isError: true, structuredContent: { status: "error", message: "Disk unavailable" } }],
  ]) {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    try {
      await page.setContent('<iframe title="Forked task"></iframe>');
      await page.evaluate(({ scenario, failure }) => {
        window.toolCalls = [];
        window.cancellations = [];
        const task = { task_id: "inherited-task", status: "running", delivery: "pending",
          revision: "original-revision", output_tail: { stdout: "Inherited stale output" } };
        window.addEventListener("message", event => {
          const frame = document.querySelector("iframe");
          if (event.source !== frame.contentWindow) return;
          const request = event.data;
          const post = data => frame.contentWindow.postMessage({ jsonrpc: "2.0", ...data }, "*");
          if (request.method === "notifications/cancelled") window.cancellations.push(request.params.requestId);
          if (request.id == null) return;
          if (request.method === "ui/initialize") {
            post({ method: "ui/notifications/tool-result", params: { structuredContent:
              scenario === "watch" ? task : { task_id: task.task_id } } });
            post({ id: request.id, result: { hostContext: { theme: "light" } } });
          } else if (request.method === "tools/call") {
            window.toolCalls.push(request.params);
            if (scenario !== "callback") post({ id: request.id, result: failure });
            // Callback-only failures must cancel the still-pending direct call.
            post({ method: "ui/notifications/tool-result", params: failure });
          }
        });
        window.replay = () => {
          const frame = document.querySelector("iframe");
          const post = data => frame.contentWindow.postMessage({ jsonrpc: "2.0", ...data }, "*");
          post({ method: "ui/notifications/tool-result", params: { structuredContent: task } });
          post({ method: "ui/notifications/tool-input", params: { arguments: { task_id: task.task_id } } });
          post({ method: "ui/notifications/tool-result", params: failure });
          post({ id: 900, method: "ping", params: {} });
        };
      }, { scenario, failure });
      await page.locator("iframe").evaluate((frame, html) => { frame.srcdoc = html; }, html);
      const frame = page.frameLocator("iframe");
      if (scenario === "other-error") {
        await frame.locator(".statusbar.is-error").filter({ hasText: "Update failed: Error: Disk unavailable" }).waitFor();
        assert.equal(await frame.locator(".original-chat").count(), 0);
      } else {
        await frame.locator(".original-chat").filter({ hasText: "Task belongs to the original chat" }).waitFor();
        assert.equal(await frame.locator("#detail pre, #detail button, .statusbar.is-error").count(), 0);
        assert.match(await frame.locator(".original-chat").innerText(), /Results are still delivered there/);
        assert.deepEqual((await page.evaluate(() => window.toolCalls)).map(c => c.name),
          [scenario === "watch" ? "watch_tasks" : "get_task"], scenario);
        if (scenario === "callback")
          await page.waitForFunction(() => window.cancellations.length === 1);
        await page.evaluate(() => window.replay());
        // A disposed panel ignores even the ping, so use a browser event barrier
        // after the queued postMessage callbacks instead of an idle retry loop.
        await frame.locator("body").evaluate(() => new Promise(resolve => {
          window.addEventListener("message", function barrier(event) {
            if (event.data?.id === 900) {
              window.removeEventListener("message", barrier);
              resolve();
            }
          });
          window.postMessage({ id: 900 }, "*");
        }));
        assert.equal((await page.evaluate(() => window.toolCalls)).length, 1, scenario);
        assert.equal(await frame.locator("#detail pre, .statusbar.is-error").count(), 0);
      }
      assert.deepEqual(errors, [], scenario);
    } finally { await page.close(); }
  }
}
