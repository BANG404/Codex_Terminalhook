import assert from "node:assert/strict";

// Exercise host callback orderings without loading any chat-wide task list.
export async function verifyStartup(browser, html) {
  for (const scenario of ["before-ready", "full-before-ready", "after-ready", "input-before-ready", "callback-during-get", "failed-get-after-callback", "list-only"]) {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    try {
      await page.setContent('<iframe title="Startup ordering"></iframe>');
      await page.evaluate((scenario) => {
        window.toolCalls = [];
        window.cancellations = [];
        const task = {
          task_id: "startup-task", task_key: "startup-recovery", program: "build",
          cwd: "/startup", status: "completed", delivery: "delivered", revision: "0000000000000002",
          result: { exit_code: 0, output_tail: { stdout: "Startup recovered" } },
        };
        window.addEventListener("message", (event) => {
          const frame = document.querySelector("iframe");
          if (event.source !== frame.contentWindow) return;
          const message = event.data;
          const post = (data) => frame.contentWindow.postMessage({ jsonrpc: "2.0", ...data }, "*");
          const notify = (data) => post({ method: "ui/notifications/tool-result", params: { structuredContent: data } });
          if (message.method === "notifications/cancelled") window.cancellations.push(message.params.requestId);
          if (message.method === "ui/notifications/initialized") {
            window.initialized = true;
            if (scenario === "after-ready") notify({ task_id: task.task_id, status: "submitted" });
          }
          if (message.id == null) return;
          let result;
          if (message.method === "ui/initialize") {
            if (scenario === "full-before-ready") notify(task);
            else if (scenario === "list-only") notify({ tasks: [task], revision: task.revision });
            else if (scenario === "input-before-ready") post({ method: "ui/notifications/tool-input", params: { arguments: { task_id: task.task_id } } });
            else if (scenario !== "after-ready") notify({ task_id: task.task_id, status: "submitted" });
            result = { hostContext: { theme: "light" } };
          } else if (message.method === "tools/call") {
            const { name, arguments: args } = message.params;
            window.toolCalls.push({ name, args });
            if (name === "watch_tasks") {
              window.watchRequest = message.id;
              window.watchRevision = args.after_revision;
              return;
            }
            if (name !== "get_task") throw new Error("Unexpected tool: " + name);
            let data = task;
            if (scenario === "callback-during-get" || scenario === "failed-get-after-callback") {
              notify(task);
              // The initial read must not overwrite the newer callback.
              data = { task_id: task.task_id, status: "running", revision: "0000000000000001" };
            }
            if (scenario === "failed-get-after-callback") {
              post({ id: message.id, error: { code: -32603, message: "Stale read failed" } });
              return;
            }
            result = { structuredContent: data };
          } else return;
          post({ id: message.id, result });
        });
      }, scenario);
      await page.locator("iframe").evaluate((frame, html) => { frame.srcdoc = html; }, html);
      const frame = page.frameLocator("iframe");
      await page.waitForFunction(() => window.initialized, null, { timeout: 5000 });
      if (scenario === "list-only") {
        await frame.getByText("Waiting for this tool’s task…", { exact: true }).first().waitFor();
        assert.deepEqual(await page.evaluate(() => window.toolCalls), []);
      } else {
        await frame.locator("#detail pre").filter({ hasText: "Startup recovered" }).waitFor({ timeout: 5000 });
        await page.waitForFunction(() => window.watchRevision === "0000000000000002", null, { timeout: 5000 });
        const calls = await page.evaluate(() => window.toolCalls);
        assert.deepEqual(calls.map(c => c.name), scenario === "full-before-ready" ? ["watch_tasks"] : ["get_task", "watch_tasks"], scenario);
        assert.ok(calls.every(c => c.args.task_id === "startup-task"), scenario);
        // Unrelated single-task and chat snapshots cannot switch or erase a card.
        await page.evaluate(() => {
          const post = data => document.querySelector("iframe").contentWindow.postMessage({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: data } }, "*");
          post({ task_id: "unrelated", status: "failed" });
          post({ tasks: [], revision: "0000000000000003" });
        });
        await frame.locator("#detail pre").filter({ hasText: "Startup recovered" }).waitFor();
        await page.evaluate(() => {
          document.querySelector("iframe").contentWindow.postMessage({ jsonrpc: "2.0", id: 999, method: "ui/resource-teardown", params: {} }, "*");
        });
        await page.waitForFunction(() => window.cancellations.includes(window.watchRequest));
      }
      assert.deepEqual(errors, [], scenario);
    } finally {
      await page.close();
    }
  }
}
