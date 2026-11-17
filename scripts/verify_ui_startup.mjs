import assert from "node:assert/strict";

// Deterministic host orderings complement the real submit_task lifecycle test.
export async function verifyStartup(browser, html) {
  for (const scenario of ["task-during-list", "snapshot-before-ready", "snapshot-during-list"]) {
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
          cwd: "/startup", status: "completed", delivery: "delivered",
          result: { exit_code: 0, output_tail: { stdout: "Startup recovered" } },
        };
        const snapshot = { tasks: [task], revision: "0000000000000002" };
        window.addEventListener("message", (event) => {
          const frame = document.querySelector("iframe");
          if (event.source !== frame.contentWindow) return;
          const message = event.data;
          const post = (data) => frame.contentWindow.postMessage({ jsonrpc: "2.0", ...data }, "*");
          const notify = (data) => post({ method: "ui/notifications/tool-result", params: { structuredContent: data } });
          if (message.method === "notifications/cancelled") window.cancellations.push(message.params.requestId);
          if (message.id == null) return;
          let result;
          if (message.method === "ui/initialize") {
            if (scenario === "snapshot-before-ready") notify(snapshot);
            result = { hostContext: { theme: "light" } };
          } else if (message.method === "tools/call") {
            const name = message.params.name;
            window.toolCalls.push(name);
            if (name === "watch_tasks") {
              window.watchRequest = message.id;
              window.watchRevision = message.params.arguments.after_revision;
              return;
            }
            let data = task;
            if (name === "list_tasks") {
              data = snapshot;
              if (window.toolCalls.filter((name) => name === "list_tasks").length === 1) {
                notify(scenario === "task-during-list"
                  ? { task_id: task.task_id, status: "submitted", delivery: "pending" }
                  : snapshot);
                // The already-running read returns older data after the callback.
                data = { tasks: [{ task_id: task.task_id, status: "submitted", delivery: "pending" }], revision: "0000000000000001" };
              }
            }
            result = { structuredContent: data };
          } else return;
          post({ id: message.id, result });
        });
      }, scenario);
      await page.locator("iframe").evaluate((frame, html) => { frame.srcdoc = html; }, html);
      const frame = page.frameLocator("iframe");
      await frame.getByRole("button", { name: /startup-recovery completed/ }).waitFor({ timeout: 5000 });
      await frame.locator("#detail pre").filter({ hasText: "Startup recovered" }).waitFor({ timeout: 5000 });
      await page.waitForFunction(() => window.watchRevision === "0000000000000002", null, { timeout: 5000 });
      const calls = await page.evaluate(() => window.toolCalls);
      assert.equal(calls.filter((name) => name === "list_tasks").length,
        scenario === "task-during-list" ? 2 : scenario === "snapshot-before-ready" ? 0 : 1, scenario);
      assert.equal(calls.filter((name) => name === "watch_tasks").length, 1, scenario);
      await page.evaluate(() => {
        document.querySelector("iframe").contentWindow.postMessage({ jsonrpc: "2.0", id: 999, method: "ui/resource-teardown", params: {} }, "*");
      });
      await page.waitForFunction(() => window.cancellations.includes(window.watchRequest));
      assert.deepEqual(errors, [], scenario);
    } finally {
      await page.close();
    }
  }
}
