// Run after cargo build. Supply a Playwright module path when it is not installed locally.
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const { chromium } = await import(
  process.env.TERMINALHOOK_PLAYWRIGHT_MODULE || "playwright"
);
const directory = path.join(root, "artifacts", "ui-verification-" + Date.now());
await mkdir(directory, { recursive: true });
const owner = "01a1e361-731f-7020-8ba1-5fb700728b79";
const child = spawn(
  process.env.TERMINALHOOK_EXE ||
    path.join(
      root,
      "target/debug",
      process.platform === "win32" ? "terminalhook.exe" : "terminalhook",
    ),
  [],
  {
    env: {
      ...process.env,
      TERMINALHOOK_TASK_DIR: path.join(directory, "tasks"),
      TERMINALHOOK_PROXY_COMMAND: JSON.stringify([
        process.env.TERMINALHOOK_TEST_PYTHON || "python3",
        path.join(root, "tests/fake_codex_proxy.py"),
      ]),
      TERMINALHOOK_FAKE_STATE_DIR: directory,
      TERMINALHOOK_FAKE_THREAD_ID: owner,
    },
    stdio: ["pipe", "pipe", "inherit"],
  },
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
  assert.equal(html, await readFile(path.join(root, "ui/tasks.html"), "utf8"));
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
    locale: "en-US",
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
    window.addEventListener("message", async (event) => {
      const frame = document.querySelector("iframe");
      if (event.source !== frame.contentWindow) return;
      const message = event.data;
      window.hostCalls.push(message.method);
      if (message.id == null) return;
      let result;
      if (message.method === "ui/initialize")
        result = {
          protocolVersion: "2026-01-26",
          hostCapabilities: {},
          hostContext: { theme: "light" },
        };
      else if (message.method === "tools/call")
        result = await window.callMcp(
          message.params.name,
          message.params.arguments,
        );
      else return;
      frame.contentWindow.postMessage(
        { jsonrpc: "2.0", id: message.id, result },
        "*",
      );
    });
  });
  await page.locator("iframe").evaluate((frame, html) => {
    frame.srcdoc = html;
  }, html);
  const frame = page.frameLocator("iframe");
  await frame.getByRole("button", { name: /dashboard-verification/ }).click();
  await frame.getByRole("button", { name: "Refresh", exact: true }).click();
  await frame
    .locator("#detail pre")
    .filter({ hasText: "Build finished" })
    .waitFor({ timeout: 10000 });
  await frame.locator("#auto").uncheck();
  assert.equal(await frame.locator("#detail pre img").count(), 0);
  assert.match(
    await frame.locator("#detail pre").innerText(),
    /<img src=x onerror=alert\(1\)>/,
  );
  await frame.locator("#language").selectOption("zh");
  await frame.getByRole("heading", { name: "后台任务", exact: true }).waitFor();
  await frame.getByRole("button", { name: "刷新", exact: true }).click();
  await frame.locator("#detail").getByText("已交付", { exact: true }).waitFor();
  await page.screenshot({
    path: path.join(root, "artifacts/task-dashboard.png"),
    fullPage: true,
  });
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
  const evidence = {
    passed: true,
    actual_mcp_resource: true,
    actual_task_tools: true,
    language_switch: true,
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
  child.stdin.end();
  await new Promise((resolve) => {
    if (child.exitCode != null) resolve();
    else child.on("exit", resolve);
  });
}
