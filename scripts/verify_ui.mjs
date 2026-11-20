// Run after cargo build. Supply a Playwright module path when it is not installed locally.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { verifyStartup } from "./verify_ui_startup.mjs";
import { verifyFork } from "./verify_ui_fork.mjs";

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
const appRequests = new Map();
createInterface({ input: child.stdout }).on("line", (line) => {
  const message = JSON.parse(line),
    entry = pending.get(message.id);
  if (!entry) return;
  pending.delete(message.id);
  appRequests.delete(entry.appId);
  clearTimeout(entry.timer);
  message.error
    ? entry.reject(new Error(JSON.stringify(message.error)))
    : entry.resolve(message.result);
});
function rpc(method, params, appId) {
  return new Promise((resolve, reject) => {
    const ident = ++id,
      timer = setTimeout(() => {
        pending.delete(ident);
        reject(new Error(method + " timed out"));
      }, params?.name === "watch_tasks" ? 600000 : 20000);
    if (appId != null) appRequests.set(appId, ident);
    pending.set(ident, { resolve, reject, timer, appId });
    child.stdin.write(
      JSON.stringify({ jsonrpc: "2.0", id: ident, method, params }) + "\n",
    );
  });
}
function tool(name, args = {}, appId) {
  return rpc("tools/call", {
    name,
    arguments: args,
    _meta: { threadId: owner },
  }, appId);
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
        [
          "import time",
          "print('Build started')",
          "time.sleep(3)",
          "for step in range(1, 13):",
          "    print(f'[{step:02d}/12] Compiled workspace module')",
          "print('a' * 240)",
          "print('<img src=x onerror=alert(1)>')",
          "print('Build finished 中文')",
        ].join("\n"),
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
  await page.exposeFunction("callMcp", (name, args, appId) => tool(name, args, appId));
  await page.exposeFunction("cancelMcp", (appId) => {
    const requestId = appRequests.get(appId);
    if (requestId != null) child.stdin.write(JSON.stringify({jsonrpc:"2.0",method:"notifications/cancelled",params:{requestId}})+"\n");
  });
  await page.setContent(
    '<style>body{margin:0;background:#eef0f4;padding:18px}iframe{width:100%;height:800px;border:0;border-radius:14px}</style><iframe title="TerminalHook task dashboard"></iframe>',
  );
  await page.evaluate((submitted) => {
    window.hostCalls = [];
    window.toolCalls = [];
    window.toolArgs = [];
    window.sizes = [];
    window.addEventListener("message", async (event) => {
      const frame = document.querySelector("iframe");
      if (event.source !== frame.contentWindow) return;
      const message = event.data;
      window.hostCalls.push(message.method);
      if (message.method === "notifications/cancelled") { await window.cancelMcp(message.params.requestId); return; }
      if (message.method === "ui/notifications/size-changed") {
        window.sizes.push(message.params);
        frame.style.height = `${message.params.height}px`;
      }
      if (message.id == null) return;
      let result;
      if (message.method === "ui/initialize") {
        // Opening submit_task's resource delivers the single-task result before
        // initialization finishes. Its task ID scopes the first detail read.
        frame.contentWindow.postMessage(
          { jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: submitted } },
          "*",
        );
        result = {
          protocolVersion: "2026-01-26",
          hostCapabilities: {},
          hostContext: { theme: "light" },
        };
      } else if (message.method === "tools/call") {
        const { name, arguments: args } = message.params;
        window.toolCalls.push(name);
        window.toolArgs.push(args);
        result = await window.callMcp(name, args, message.id);
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
  }, submitted);
  await page.locator("iframe").evaluate((frame, html) => {
    // Exercise the real deadline callback without waiting ten minutes. The
    // callback is invoked explicitly only after the first completion below.
    const clock = `<script>const nativeTimeout=window.setTimeout.bind(window);window.setTimeout=(fn,ms,...args)=>{const id=nativeTimeout(fn,ms,...args);if(ms===600000)window.expireTaskSubscription=()=>{clearTimeout(id);fn(...args)};return id};</script>`;
    frame.srcdoc = html.replace("<head>", "<head>" + clock);
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
  await frame.locator(".detail-heading strong").filter({hasText: "dashboard-verification"}).waitFor();
  assert.equal(await frame.locator(".task-list, .task-row, [role=tab], input, header, h1, select, [role=switch]").count(), 0);
  assert.equal(await frame.locator("html").getAttribute("lang"), "en");
  // Native state/result events must update this task without list reads.
  await frame.locator(".task-badges .status-badge").filter({hasText: "completed"}).waitFor();
  const terminal = frame.locator("[data-slot=trace]");
  const terminalTrigger = terminal.getByRole("button", { name: /^Ran / });
  const outputRegion = frame.getByRole("region", { name: "Output", exact: true });
  const outputText = outputRegion.locator("[data-slot=code-block-code]");
  await outputText.filter({hasText: "Build finished"}).waitFor();
  await frame.getByLabel("Delivery: delivered", {exact: true}).waitFor();
  const actual = (await tool("get_task", { task_id: submitted.task_id })).structuredContent;
  assert.equal(actual.status, "completed");
  assert.equal(await outputText.locator("img").count(), 0);
  assert.match(await outputText.innerText(), /<img src=x onerror=alert\(1\)>/);
  assert.equal(await frame.locator("#detail").getAttribute("data-slot"), "scroll-area-viewport");
  assert.equal(await outputRegion.getAttribute("data-slot"), "scroll-area-viewport");
  assert.equal(await outputRegion.evaluate(el => getComputedStyle(el).scrollbarWidth), "none");
  await terminalTrigger.press("Enter");
  await outputRegion.waitFor({ state: "hidden" });
  assert.equal(await terminalTrigger.getAttribute("aria-expanded"), "false");
  await terminalTrigger.press("Enter");
  await outputRegion.waitFor();
  assert.equal(await terminalTrigger.getAttribute("aria-expanded"), "true");
  await terminal.locator("[data-slot=scroll-area-thumb]").waitFor();
  await outputRegion.press("End");
  const appFrame = page.frame({ url: "about:srcdoc" });
  await appFrame.waitForFunction(() => {
    const viewport = document.querySelector("[data-slot=scroll-area-viewport][aria-label=Output]");
    return viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 1;
  });
  await outputRegion.press("Home");
  await appFrame.waitForFunction(
    () => document.querySelector("[data-slot=scroll-area-viewport][aria-label=Output]").scrollTop === 0,
  );
  await frame.getByRole("button", { name: "Details", exact: true }).click();
  await frame.getByText("No deadline", { exact: true }).waitFor();
  await frame.getByRole("button", { name: "Details", exact: true }).click();

  const subscriptions = await page.evaluate(() => window.toolCalls.filter(name => name === "watch_tasks").length);
  await frame.locator("body").evaluate(() => window.expireTaskSubscription());
  await page.waitForFunction(count => window.toolCalls.filter(name => name === "watch_tasks").length > count, subscriptions);
  const callsAfterRenewal = await page.evaluate(() => window.toolCalls.length);
  const later = (await tool("submit_task", {
    task_key: "unrelated-task", program: process.env.TERMINALHOOK_TEST_PYTHON || "python3",
    args: ["-u", "-c", "print('Unrelated task finished')"], cwd: root,
  })).structuredContent;
  assert.equal(later.status, "submitted");
  await notify({ structuredContent: later });
  await notify({ structuredContent: {tasks: [later], revision: "ffffffffffffffff"} });
  await notify({ structuredContent: {tasks: []} });
  // Exceeds the former refresh period; unrelated state writes do not wake the card.
  await page.waitForTimeout(4200);
  await frame.locator("body").evaluate((body) => {
    for (const hidden of [true, false]) {
      Object.defineProperty(body.ownerDocument, "hidden", { configurable: true, value: hidden });
      body.ownerDocument.dispatchEvent(new Event("visibilitychange"));
    }
  });
  await notify({ structuredContent: actual });
  assert.equal(await page.evaluate(() => window.toolCalls.length), callsAfterRenewal);
  await frame.locator("#detail pre").filter({hasText: "Build finished"}).waitFor();
  assert.equal(await frame.getByText("unrelated-task", {exact: true}).count(), 0);
  assert.ok((await page.evaluate(() => window.toolArgs)).every(args => args.task_id === submitted.task_id));
  assert.ok(!(await page.evaluate(() => window.toolCalls)).includes("list_tasks"));
  const desktopSize = await frame.locator("main").boundingBox();
  assert.ok(desktopSize.height <= 340, `desktop height: ${desktopSize.height}`);
  await frame.locator("main").screenshot({path: path.join(root, "artifacts/task-dashboard.png")});
  await page.evaluate(() => document.querySelector("iframe").contentWindow.postMessage({
    jsonrpc: "2.0", method: "ui/notifications/host-context-changed", params: {theme: "dark"},
  }, "*"));
  await frame.locator("html.dark").waitFor();
  await frame.locator("main").screenshot({path: path.join(root, "artifacts/task-dashboard-dark.png")});
  await page.setViewportSize({ width: 360, height: 800 });
  const mobileSize = await frame.locator("main").boundingBox();
  assert.ok(mobileSize.height <= 340, `mobile height: ${mobileSize.height}`);
  assert.ok(mobileSize.width <= 324, `mobile width: ${mobileSize.width}`);
  assert.ok(await frame.locator("body").evaluate(body => body.scrollWidth <= body.clientWidth));
  assert.ok(await outputRegion.evaluate(el => el.scrollWidth <= el.clientWidth));
  await frame.locator("main").screenshot({path: path.join(root, "artifacts/task-dashboard-mobile.png")});
  await page.evaluate(() => document.querySelector("iframe").contentWindow.postMessage({
    jsonrpc: "2.0", id: 998, method: "ui/resource-teardown", params: {},
  }, "*"));
  const callsAtTeardown = await page.evaluate(() => window.toolCalls.length);
  await notify({ structuredContent: {...actual, status: "failed"} });
  assert.equal(await page.evaluate(() => window.toolCalls.length), callsAtTeardown);
  assert.equal(requests.filter(url => /^https?:/.test(url)).length, 0);
  assert.deepEqual(errors, []);
  const calls = await page.evaluate(() => window.hostCalls);
  assert.ok(calls.includes("ui/notifications/initialized"));
  assert.ok(!calls.includes("ui/message"));
  await verifyStartup(browser, html);
  const denied = await rpc("tools/call", { name: "get_task",
    arguments: { task_id: submitted.task_id },
    _meta: { threadId: "01a1e361-731f-7020-8ba1-5fb700728b78" },
  });
  assert.equal(denied.isError, true);
  assert.equal(denied.structuredContent.code, "task_chat_mismatch");
  await verifyFork(browser, html, denied);
  const evidence = {
    passed: true,
    actual_mcp_resource: true,
    actual_task_tools: true,
    current_task_only: true,
    no_list_calls_or_list_controls: true,
    startup_callback_orderings: true,
    forked_task_notice_and_no_retry: true,
    native_worker_event_updates: true,
    unrelated_tasks_ignored: true,
    timeout_callback_renews_without_refresh: true,
    no_idle_or_visibility_polling: true,
    callback_echo_no_loop: true,
    details_collapse: true,
    host_theme: true,
    compact_desktop_height: desktopSize.height,
    compact_mobile_height: mobileSize.height,
    output_escaped: true,
    terminal_trace_collapses_with_keyboard: true,
    output_component_scrollbar_and_keyboard: true,
    external_requests: 0,
    model_messages: 0,
    host: "Playwright MCP Apps bridge simulation; not native desktop rendering",
  };
  await writeFile(path.join(root, "artifacts/ui-validation.json"), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence));
} finally {
  if (browser) await browser.close();
  for (const entry of pending.values()) clearTimeout(entry.timer);
  pending.clear();
  appRequests.clear();
  host.kill();
  child.stdin.end();
  await new Promise((resolve) => {
    if (child.exitCode != null) resolve();
    else child.on("exit", resolve);
  });
}
