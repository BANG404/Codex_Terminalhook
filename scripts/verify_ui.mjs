// Run after cargo build. Supply a Playwright module path when it is not installed locally.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = await import(
  process.env.TERMINALHOOK_PLAYWRIGHT_MODULE || "playwright"
);
const directory = path.join(root, "artifacts", "ui-verification-" + Date.now());
await mkdir(directory, { recursive: true });
const owner = "01a1e361-731f-7020-8ba1-5fb700728b79";
const environment = {
  ...process.env,
  TERMINALHOOK_TASK_DIR: path.join(directory, "tasks"),
  TERMINALHOOK_PROXY_COMMAND: JSON.stringify([
    process.env.TERMINALHOOK_TEST_PYTHON || "python3",
    path.join(root, "tests/fake_codex_proxy.py"),
    "--proxy",
  ]),
  TERMINALHOOK_FAKE_STATE_DIR: directory,
  TERMINALHOOK_FAKE_THREAD_ID: owner,
};
const host = spawn(
  process.env.TERMINALHOOK_TEST_PYTHON || "python3",
  [path.join(root, "tests/fake_codex_proxy.py"), "--host"],
  { env: environment, stdio: ["ignore", "pipe", "inherit"] },
);
environment.TERMINALHOOK_FAKE_PORT = String(
  await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error("fake host startup timed out")),
      10000,
    );
    createInterface({ input: host.stdout }).once("line", (line) => {
      clearTimeout(timer);
      resolve(JSON.parse(line).port);
    });
    host.once("error", reject);
  }),
);
const child = spawn(
  process.env.TERMINALHOOK_EXE ||
    path.join(
      root,
      "target/debug",
      process.platform === "win32" ? "terminalhook.exe" : "terminalhook",
    ),
  [],
  { env: environment, stdio: ["pipe", "pipe", "inherit"] },
);

let id = 0;
const pending = new Map();
createInterface({ input: child.stdout }).on("line", (line) => {
  const message = JSON.parse(line),
    entry = pending.get(message.id);
  if (!entry) return;
  pending.delete(message.id);
  clearTimeout(entry.timer);
  message.error
    ? entry.reject(new Error(JSON.stringify(message.error)))
    : entry.resolve(message.result);
});
function rpc(method, params) {
  return new Promise((resolve, reject) => {
    const ident = ++id,
      timer = setTimeout(() => {
        pending.delete(ident);
        reject(new Error(method + " timed out"));
      }, 20000);
    pending.set(ident, { resolve, reject, timer });
    child.stdin.write(
      JSON.stringify({ jsonrpc: "2.0", id: ident, method, params }) + "\n",
    );
  });
}
function tool(name, args = {}) {
  return rpc("tools/call", {
    name,
    arguments: args,
    _meta: { threadId: owner },
  });
}
let browser;
try {
  await rpc("initialize", {
    protocolVersion: "2025-11-25",
    capabilities: {},
    clientInfo: { name: "ui-verifier", version: "1" },
  });
  child.stdin.write(
    JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }) +
      "\n",
  );
  const html = (
    await rpc("resources/read", { uri: "ui://terminalhook/tasks.html" })
  ).contents[0].text;
  assert.equal(
    html,
    await readFile(path.join(root, "ui/dist/index.html"), "utf8"),
  );
  const submitted = (
    await tool("submit_task", {
      task_key: "dashboard-verification",
      program: process.env.TERMINALHOOK_TEST_PYTHON || "python3",
      args: [
        "-u",
        "-c",
        "import time; print('Build started'); time.sleep(1); print('<img src=x onerror=alert(1)>'); print('Build finished 中文')",
      ],
      cwd: root,
    })
  ).structuredContent;
  assert.equal(submitted.status, "submitted");
  browser = await chromium.launch({
    headless: true,
    ...(process.env.TERMINALHOOK_CHROMIUM
      ? { executablePath: process.env.TERMINALHOOK_CHROMIUM }
      : {}),
  });
  const page = await browser.newPage({
    viewport: { width: 1080, height: 850 },
    locale: "zh-CN",
    reducedMotion: "reduce",
  });
  const errors = [],
    requests = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("request", (request) => requests.push(request.url()));
  await page.exposeFunction("callMcp", (name, args) => tool(name, args));
  await page.setContent(
    '<style>body{margin:0;background:#eef0f4;padding:18px}iframe{width:100%;height:800px;border:0;border-radius:14px}</style><iframe title="TerminalHook task dashboard"></iframe>',
  );
  await page.evaluate(() => {
    window.hostCalls = [];
    window.toolCalls = [];
    window.sizes = [];
    window.addEventListener("message", async (event) => {
      const frame = document.querySelector("iframe");
      if (event.source !== frame.contentWindow) return;
      const message = event.data;
      window.hostCalls.push(message.method);
      if (message.method === "ui/notifications/size-changed") {
        window.sizes.push(message.params);
        frame.style.height = `${message.params.height}px`;
      }
      if (message.id == null) return;
      let result;
      if (message.method === "ui/initialize")
        result = {
          protocolVersion: "2026-01-26",
          hostCapabilities: {},
          hostContext: { theme: "light" },
        };
      else if (message.method === "tools/call") {
        const { name, arguments: args } = message.params;
        window.toolCalls.push(name);
        result = window.fixture
          ? {
              structuredContent:
                name === "list_tasks"
                  ? { tasks: window.fixture.tasks }
                  : window.fixture.details[args.task_id],
            }
          : await window.callMcp(name, args);
      } else return;
      frame.contentWindow.postMessage(
        { jsonrpc: "2.0", id: message.id, result },
        "*",
      );
      // Hosts may echo app-initiated calls as callbacks too. These must not
      // cause a list/get/list feedback loop.
      if (message.method === "tools/call")
        frame.contentWindow.postMessage(
          {
            jsonrpc: "2.0",
            method: "ui/notifications/tool-result",
            params: result,
          },
          "*",
        );
    });
  });
  await page.locator("iframe").evaluate((frame, html) => {
    frame.srcdoc = html;
  }, html);
  const frame = page.frameLocator("iframe");
  async function notify(params) {
    await page.evaluate(
      (params) =>
        document
          .querySelector("iframe")
          .contentWindow.postMessage(
            { jsonrpc: "2.0", method: "ui/notifications/tool-result", params },
            "*",
          ),
      params,
    );
  }
  await frame.getByRole("button", { name: /dashboard-verification/ }).waitFor();
  await frame.locator('#detail[aria-busy="false"] pre').waitFor();
  assert.equal(
    await frame.locator("header, h1, select, [role=switch]").count(),
    0,
  );
  assert.equal(
    await frame.getByRole("button", { name: /Refresh|Retry/i }).count(),
    0,
  );
  assert.equal(await frame.locator("html").getAttribute("lang"), "en");
  const callsBeforeIdle = await page.evaluate(() => window.toolCalls.length);
  // Longer than the former three-second refresh interval: no background calls.
  await page.waitForTimeout(4200);
  assert.equal(
    await page.evaluate(() => window.toolCalls.length),
    callsBeforeIdle,
  );
  await frame.locator("body").evaluate((body) => {
    for (const hidden of [true, false]) {
      Object.defineProperty(body.ownerDocument, "hidden", {
        configurable: true,
        value: hidden,
      });
      body.ownerDocument.dispatchEvent(new Event("visibilitychange"));
    }
  });
  const actual = (await tool("get_task", { task_id: submitted.task_id }))
    .structuredContent;
  assert.equal(actual.status, "completed");
  await notify({ structuredContent: actual });
  await frame
    .locator("#detail pre")
    .filter({ hasText: "Build finished" })
    .waitFor();
  assert.equal(
    await page.evaluate(() => window.toolCalls.length),
    callsBeforeIdle,
  );
  assert.equal(await frame.locator("#detail pre img").count(), 0);
  assert.match(
    await frame.locator("#detail pre").innerText(),
    /<img src=x onerror=alert\(1\)>/,
  );
  await frame.getByRole("button", { name: "Details", exact: true }).click();
  await frame.getByText("No deadline", { exact: true }).waitFor();
  await frame.getByRole("button", { name: "Details", exact: true }).click();
  await frame
    .getByRole("textbox", { name: "Find a task…" })
    .fill("does-not-exist");
  await frame.getByText("No matching tasks", { exact: true }).waitFor();
  await frame.getByRole("button", { name: "Clear search" }).click();
  const desktopSize = await frame.locator("main").boundingBox();
  assert.ok(desktopSize.height <= 340, `desktop height: ${desktopSize.height}`);
  await page.waitForFunction(() =>
    window.sizes.some((size) => size.height < 360),
  );

  // Exercise dense lists, attention, failures and empty state with deterministic
  // host responses after verifying the actual embedded resource and task tools.
  const fixture = { tasks: [], details: {} };
  for (let index = 0; index < 12; index++) {
    const task = {
      task_id: `fixture-${index}`,
      task_key:
        ["build-release", "integration-tests", "lint-checks"][index % 3] +
        (index >= 3 ? `-${index}` : ""),
      program: index % 2 ? "cargo test --locked" : "npm run build",
      cwd: root,
      status: index === 0 ? "running" : index === 1 ? "failed" : "completed",
      delivery:
        index === 0 ? "pending" : index === 1 ? "uncertain" : "delivered",
      timeout_ms: null,
    };
    fixture.tasks.push(task);
    fixture.details[task.task_id] = {
      ...actual,
      ...task,
      result: {
        exit_code: index === 0 ? null : index === 1 ? 1 : 0,
        elapsed_ms: 1420,
        output_tail: {
          stdout:
            "✓ Compiling terminalhook\n✓ Checking native targets\n✓ Build finished 中文\n" +
            Array(40).fill("Verification output").join("\n"),
        },
      },
      delivery_error:
        index === 1 ? "Delivery receipt needs verification" : undefined,
    };
  }
  await page.evaluate((fixture) => {
    window.fixture = fixture;
  }, fixture);
  await notify({ structuredContent: { tasks: fixture.tasks } });
  await frame.getByRole("button", { name: /build-release running/ }).waitFor();
  assert.equal(await frame.locator(".task-row").count(), 12);
  await frame.getByRole("tab", { name: /Active/ }).click();
  assert.equal(await frame.locator(".task-row").count(), 1);
  assert.notEqual(
    await frame
      .getByRole("tab", { name: /Active/ })
      .evaluate((element) => getComputedStyle(element).backgroundColor),
    "rgba(0, 0, 0, 0)",
  );
  await frame.getByRole("tab", { name: /Active/ }).press("ArrowRight");
  await frame.getByRole("tab", { name: /Attention/, selected: true }).waitFor();
  await frame.getByRole("tab", { name: /Attention/ }).click();
  await frame.getByRole("button", { name: /integration-tests/ }).click();
  await frame
    .getByRole("alert")
    .filter({ hasText: "Delivery receipt" })
    .waitFor();
  assert.equal(await frame.locator(".task-row").count(), 1);
  await frame.getByRole("tab", { name: /All/ }).click();
  await frame.getByRole("button", { name: /build-release running/ }).click();
  await frame.locator('#detail[aria-busy="false"] pre').waitFor();
  const callsBeforeError = await page.evaluate(() => window.toolCalls.length);
  await notify({
    isError: true,
    content: [{ type: "text", text: "Host unavailable" }],
  });
  await frame
    .getByRole("status")
    .filter({ hasText: "Host unavailable" })
    .waitFor();
  assert.equal(await frame.locator(".task-row").count(), 12);
  await notify({ structuredContent: fixture.details["fixture-0"] });
  await frame.getByRole("status").filter({ hasText: "Updated" }).waitFor();
  assert.equal(
    await page.evaluate(() => window.toolCalls.length),
    callsBeforeError,
  );
  await frame.locator("main").screenshot({
    path: path.join(root, "artifacts/task-dashboard.png"),
  });
  await page.evaluate(() =>
    document.querySelector("iframe").contentWindow.postMessage(
      {
        jsonrpc: "2.0",
        method: "ui/notifications/host-context-changed",
        params: { theme: "dark" },
      },
      "*",
    ),
  );
  await frame.locator("html.dark").waitFor();
  await frame
    .locator("main")
    .screenshot({ path: path.join(root, "artifacts/task-dashboard-dark.png") });
  await page.setViewportSize({ width: 360, height: 800 });
  const mobileSize = await frame.locator("main").boundingBox();
  assert.ok(mobileSize.height <= 480, `mobile height: ${mobileSize.height}`);
  assert.ok(mobileSize.width <= 324, `mobile width: ${mobileSize.width}`);
  assert.ok(
    await frame
      .locator("body")
      .evaluate((body) => body.scrollWidth <= body.clientWidth),
  );
  await frame.locator("main").screenshot({
    path: path.join(root, "artifacts/task-dashboard-mobile.png"),
  });
  await page.evaluate(() => {
    window.fixture.tasks = [];
  });
  const beforeSubmission = await page.evaluate(() => window.toolCalls.length);
  await notify({
    structuredContent: {
      task_id: "new-task",
      status: "submitted",
      delivery: "pending",
    },
  });
  await frame.getByRole("button", { name: /new-task submitted/ }).waitFor();
  assert.equal(
    await page.evaluate(() => window.toolCalls.length),
    beforeSubmission,
  );
  await notify({ structuredContent: { tasks: [] } });
  await frame.getByText("No tasks yet", { exact: true }).waitFor();
  assert.equal(await frame.locator("#detail pre").count(), 0);
  // Save a host preview for manual CLI inspection, using fixture data only.
  const preview = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TerminalHook compact dashboard preview</title><style>body{margin:0;padding:20px;background:#f4f5f6}iframe{display:block;width:100%;max-width:900px;margin:auto;border:0}</style></head><body><iframe title="TerminalHook preview"></iframe><script>
    const fixture=${JSON.stringify(fixture).replaceAll("<", "\\u003c")};
    addEventListener('message',event=>{const frame=document.querySelector('iframe'),m=event.data;if(event.source!==frame.contentWindow)return;if(m.method==='ui/notifications/size-changed')frame.style.height=m.params.height+'px';if(m.id==null)return;const result=m.method==='ui/initialize'?{hostContext:{theme:'light'}}:m.method==='tools/call'?{structuredContent:m.params.name==='list_tasks'?{tasks:fixture.tasks}:fixture.details[m.params.arguments.task_id]}:{};frame.contentWindow.postMessage({jsonrpc:'2.0',id:m.id,result},'*')});
    document.querySelector('iframe').srcdoc=${JSON.stringify(html).replaceAll("<", "\\u003c")};
  </script></body></html>`;
  await writeFile(path.join(root, "artifacts/dashboard-preview.html"), preview);
  await page.evaluate(() =>
    document
      .querySelector("iframe")
      .contentWindow.postMessage(
        { jsonrpc: "2.0", id: 998, method: "ui/resource-teardown", params: {} },
        "*",
      ),
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(
    requests.filter((url) => /^https?:/.test(url)),
    [],
  );
  const calls = await page.evaluate(() => window.hostCalls);
  assert.equal(calls[0], "ui/initialize");
  assert.ok(calls.includes("ui/notifications/initialized"));
  assert.ok(!calls.includes("ui/message"));
  assert.equal(
    await page.evaluate(
      () => window.toolCalls.filter((name) => name === "list_tasks").length,
    ),
    1,
  );
  assert.deepEqual(
    [...new Set(await page.evaluate(() => window.toolCalls))].sort(),
    ["get_task", "list_tasks"],
  );
  const evidence = {
    passed: true,
    actual_mcp_resource: true,
    actual_task_tools: true,
    english_only: true,
    no_header_or_refresh_controls: true,
    no_idle_or_visibility_polling: true,
    callback_updates: true,
    callback_echo_no_loop: true,
    filtering: true,
    details_collapse: true,
    callback_error_recovery: true,
    removed_selection_cleared: true,
    host_theme: true,
    compact_desktop_height: desktopSize.height,
    compact_mobile_height: mobileSize.height,
    output_escaped: true,
    external_requests: 0,
    model_messages: 0,
    host: "Playwright MCP Apps bridge simulation; not native desktop rendering",
  };
  await writeFile(
    path.join(root, "artifacts/ui-validation.json"),
    JSON.stringify(evidence, null, 2),
  );
  console.log(JSON.stringify(evidence));
} finally {
  if (browser) await browser.close();
  host.kill();
  child.stdin.end();
  await new Promise((resolve) => {
    if (child.exitCode != null) resolve();
    else child.on("exit", resolve);
  });
}
