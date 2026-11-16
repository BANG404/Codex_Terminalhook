use rmcp::{
    ErrorData, RoleServer, ServerHandler,
    model::{
        CallToolRequestParams, CallToolResponse, CallToolResult, Implementation,
        ListResourcesResult, ListToolsResult, PaginatedRequestParams, ReadResourceRequestParams,
        ReadResourceResponse, Resource, ServerCapabilities, ServerConfig, Tool, ToolAnnotations,
    },
    service::RequestContext,
};
use serde_json::{Value, json};

use crate::{
    background::{self, SubmitRequest, TaskRequest, WatchTasksRequest},
    process::Manager,
    spec::{ProcessRequest, RunRequest, WaitRequest, WriteRequest},
};

#[derive(Clone)]
pub struct TerminalHook {
    manager: Manager,
}

impl TerminalHook {
    pub fn new(manager: Manager) -> Self {
        Self { manager }
    }

    fn tools() -> Vec<Tool> {
        let tools = vec![
            Tool::new(
                "run_until",
                "Start a command and await one output condition, natural exit, or timeout locally. For builds, tests, and CI/CD, wait for the final outcome in this call; do not return at started/queued/in_progress markers just to issue more waits. To await natural exit, use a specific regex the command will never emit and check exit_code. Choose a task-sized timeout (up to 600000 ms, below the client limit); do not split a long wait into repeated short calls. Default: terminate on match/timeout. Use on_match=keep_running for a service or a genuine intermediate action. Pipe separates stdout/stderr; PTY merges terminal output. Supports multiline matching and rotating logs.",
                schemars::schema_for!(RunRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "wait_output",
                "Await a later condition on a retained process after a genuine intermediate action or other useful work. Use run_until for a new command and its final outcome. This is not a status-polling tool: do not repeatedly call it after short timeouts or match successive progress messages only to keep waiting. Choose a task-sized timeout; on timeout diagnose/report before deciding recovery, rather than automatically repeating the wait. Replays buffered output after the previous cursor. Timeout/cancellation preserves the process; cursor_expired reports evicted history.",
                schemars::schema_for!(WaitRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "write_input",
                "Write UTF-8 input to a managed process. When a response is needed, include wait to send and await it in one call, instead of writing then polling with wait_output. The cursor is registered before sending to avoid missing immediate responses. Choose a task-sized response timeout; do not automatically repeat waits or resend input on timeout. Pipe EOF can be sent explicitly. PTY merges terminal output.",
                schemars::schema_for!(WriteRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "stop_process",
                "Terminate a process tree retained by run_until in this MCP connection and wait for cleanup.",
                schemars::schema_for!(ProcessRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "submit_task",
                "Submit one explicitly authorized long-running command to an independent worker and return immediately. The worker survives this MCP connection and waits without model polling. Omitted timeout_ms means no execution deadline. Completion is delivered once to this calling chat through the existing Codex daemon. Use a stable task_key for idempotency; after submission end the turn and await delivery. Do not poll get_task or resubmit to keep waiting.",
                schemars::schema_for!(SubmitRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "get_task",
                "Inspect a durable background task for diagnosis or recovery, including execution result and delivery receipt. This does not wait; do not use it for periodic model-driven polling.",
                schemars::schema_for!(TaskRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "cancel_task",
                "Cancel an explicitly selected background task owned by the calling chat. Terminates its managed process tree; completion delivery reports cancellation.",
                schemars::schema_for!(TaskRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "retry_delivery",
                "Retry only a saved background result whose delivery failed before submission. Never reruns the command. Ambiguous delivery is preserved for manual reconciliation instead of blindly replayed.",
                schemars::schema_for!(TaskRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "check_background",
                "Read-only check that the existing Codex daemon can access this calling chat. Does not start another app server or a model turn.",
                json!({"type":"object","properties":{},"additionalProperties":false})
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "list_tasks",
                "Show the background task dashboard for the calling chat. Lists execution and delivery state without launching tasks or a model turn. UI refreshes call this tool directly.",
                json!({"type":"object","properties":{},"additionalProperties":false})
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "watch_tasks",
                "App-only task subscription. Wait for a native filesystem state event after a list revision; no timer, polling, command launch, or model turn.",
                schemars::schema_for!(WatchTasksRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
        ];
        tools
            .into_iter()
            .map(|mut tool| {
                if matches!(
                    tool.name.as_ref(),
                    "list_tasks" | "get_task" | "check_background" | "watch_tasks"
                ) {
                    tool.annotations = Some(
                        ToolAnnotations::new()
                            .read_only(true)
                            .destructive(false)
                            .idempotent(true)
                            .open_world(false),
                    );
                }
                if tool.name == "watch_tasks" {
                    tool.meta = Some(
                        json!({"ui":{"visibility":["app"]}})
                            .as_object()
                            .unwrap()
                            .clone()
                            .into(),
                    );
                    tool
                } else if matches!(
                    tool.name.as_ref(),
                    "submit_task" | "get_task" | "list_tasks"
                ) {
                    crate::ui::attach(tool)
                } else {
                    tool
                }
            })
            .collect()
    }
}

impl ServerHandler for TerminalHook {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().enable_resources().enable_extensions_with(serde_json::from_value(json!({"io.modelcontextprotocol/ui":{"mimeTypes":[crate::ui::MIME]}})).unwrap()).build())
            .with_server_info(Implementation::new("terminalhook", env!("TERMINALHOOK_VERSION")))
            .with_instructions("Call these tools directly and await one result. For one-shot tasks, make run_until await the final outcome or natural exit, not startup/progress markers. Local waiting avoids model polling only within a call; repeated short waits still cause model round trips. Set a task-sized timeout up to 600000 ms below the client limit; do not use short timeouts for periodic updates. Do not automatically repeat waits after timeout: diagnose/report and decide recovery from new evidence or an explicit instruction to continue. Reserve wait_output for retained processes when a genuine intermediate action or other useful work separated the waits. Use write_input with wait for input and its response together. Keep process_id and cursor; stop_process cleans up retained commands. Match does not prove successful exit; inspect the natural exit code when success requires completion. PTY merges sources into terminal. Commands use MCP OS permissions. Direct process ids/history/temp logs live only for this connection. For explicitly requested background work that must outlive the call, use submit_task with a stable task_key, then end the turn and await host delivery. Background tasks persist independently; get_task/list_tasks are for inspection and recovery, not model polling. check_background verifies the host bridge without starting a model turn.")
    }

    async fn list_tools(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, ErrorData> {
        Ok(ListToolsResult::with_all_items(Self::tools()))
    }

    fn get_tool(&self, name: &str) -> Option<Tool> {
        Self::tools().into_iter().find(|t| t.name == name)
    }

    async fn list_resources(
        &self,
        _: Option<PaginatedRequestParams>,
        _: RequestContext<RoleServer>,
    ) -> Result<ListResourcesResult, ErrorData> {
        Ok(ListResourcesResult::with_all_items(vec![
            Resource::new(crate::ui::URI, "TerminalHook background tasks")
                .with_mime_type(crate::ui::MIME),
        ]))
    }

    async fn read_resource(
        &self,
        request: ReadResourceRequestParams,
        _: RequestContext<RoleServer>,
    ) -> Result<ReadResourceResponse, ErrorData> {
        if request.uri != crate::ui::URI {
            return Err(ErrorData::invalid_params("Unknown UI resource", None));
        }
        Ok(crate::ui::resource().into())
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        let args = Value::Object(request.arguments.unwrap_or_default());
        if matches!(
            request.name.as_ref(),
            "submit_task"
                | "get_task"
                | "cancel_task"
                | "retry_delivery"
                | "check_background"
                | "list_tasks"
                | "watch_tasks"
        ) {
            let result = async {
                let thread_id = context.meta.get("threadId").and_then(Value::as_str).ok_or("background tools require Codex-provided threadId metadata; never supply a guessed chat ID")?;
                if uuid::Uuid::parse_str(thread_id).is_err() {return Err("Codex threadId is not a UUID".to_string());}
                match request.name.as_ref() {
                    "submit_task" => background::submit(serde_json::from_value(args).map_err(|e|format!("Invalid arguments: {e}"))?,thread_id,context.ct.clone()).await,
                    "check_background" => crate::bridge::probe(thread_id).await,
                    "list_tasks" => background::list(thread_id).await,
                    "watch_tasks" => background::watch_tasks(serde_json::from_value(args).map_err(|e|format!("Invalid arguments: {e}"))?,thread_id,context.ct.clone()).await,
                    name => {
                        let task:TaskRequest=serde_json::from_value(args).map_err(|e|format!("Invalid arguments: {e}"))?;
                        match name {"get_task"=>background::get(&task.task_id,thread_id).await,"cancel_task"=>background::cancel(&task.task_id,thread_id).await,_=>background::retry_delivery(&task.task_id,thread_id).await}
                    }
                }
            }.await;
            return Ok(match result {
                Ok(value) => CallToolResult::structured(value),
                Err(message) => {
                    CallToolResult::structured_error(json!({"status":"error","message":message}))
                }
            }
            .into());
        }
        let result = match request.name.as_ref() {
            "run_until" => match serde_json::from_value::<RunRequest>(args) {
                Ok(input) => self.manager.run(input, context.ct).await,
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
            "wait_output" => match serde_json::from_value::<WaitRequest>(args) {
                Ok(input) => self.manager.wait_output(input, context.ct).await,
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
            "write_input" => match serde_json::from_value::<WriteRequest>(args) {
                Ok(input) => self.manager.write_input(input, context.ct).await,
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
            "stop_process" => match serde_json::from_value::<ProcessRequest>(args) {
                Ok(input) => self.manager.stop(&input.process_id).await,
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
            _ => return Err(ErrorData::invalid_params("Unknown tool", None)),
        };
        Ok(match result {
            Ok(value) if value["status"] == "error" => CallToolResult::structured_error(value),
            Ok(value) => CallToolResult::structured(value),
            Err(message) => {
                CallToolResult::structured_error(json!({"status":"error", "message":message}))
            }
        }
        .into())
    }
}
