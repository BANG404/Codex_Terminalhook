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

use crate::background::{self, SubmitRequest, TaskRequest, WatchTasksRequest};

#[derive(Clone)]
pub struct TerminalHook;

impl TerminalHook {
    fn tools() -> Vec<Tool> {
        let tools = vec![
            Tool::new(
                "submit_task",
                "Proactively submit an in-scope command when it is expected to take a long time or may wait for an unpredictable duration. The user's task request is sufficient to choose background execution. An independent worker waits without model polling and survives this MCP connection. For CI, builds, and deployments, omit until to wait for natural exit. Omit timeout_ms by default for no execution deadline. Set a finite deadline only when the user or the operation explicitly requires one. Use a stable task_key for idempotency. After receiving the task ID, end the turn and await completion delivery to this chat. Do not poll get_task or resubmit to keep waiting.",
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
                "Request cancellation of an explicitly selected background task owned by the calling chat. Completion delivery reports whether its managed process tree was stopped.",
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
                "list_tasks",
                "List background tasks for explicit inspection of the calling chat. Does not open a UI or launch tasks or a model turn.",
                json!({"type":"object","properties":{},"additionalProperties":false})
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "watch_tasks",
                "App-only task subscription. Supply task_id and its revision to watch only the current task through native filesystem events; no timer, polling, command launch, or model turn.",
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
                    "list_tasks" | "get_task" | "watch_tasks"
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
                    "submit_task" | "get_task" | "cancel_task" | "retry_delivery"
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
            .with_server_info(
                Implementation::new("terminalhook", env!("TERMINALHOOK_VERSION"))
                    .with_icons(vec![crate::ui::logo()]),
            )
            .with_instructions("Proactively use TerminalHook for commands within the user's task that are expected to take a long time or may wait for an unpredictable duration, such as waiting for CI or a deployment to finish. The user's task request is sufficient to choose background execution. Submit directly with submit_task and a stable task_key. For a new task, submission verifies the existing shared host and originating chat before launch and returns any verification error. After receiving the task ID, end the turn and await completion delivery to this chat. Omit until to wait for natural exit. Leave timeout_ms unset and run the native wait command without an added shell or watcher deadline unless the user or the operation explicitly requires a finite deadline. The MCP request timeout applies to the submission call. The worker's lifetime is independent of it. Do not poll get_task or list_tasks, replay submissions, or start another daemon. Use get_task for diagnosis, cancel_task for explicit cancellation, and retry_delivery only for a saved result whose delivery failed before submission. Uncertain delivery requires reconciliation. Use the host terminal for quick or interactive commands. This package uses native events and existing-daemon callbacks without lifecycle hooks.")
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
                | "list_tasks"
                | "watch_tasks"
        ) {
            let result = async {
                let thread_id = context.meta.get("threadId").and_then(Value::as_str).ok_or("background tools require Codex-provided threadId metadata; never supply a guessed chat ID")?;
                if uuid::Uuid::parse_str(thread_id).is_err() {return Err("Codex threadId is not a UUID".to_string());}
                match request.name.as_ref() {
                    "submit_task" => background::submit(serde_json::from_value(args).map_err(|e|format!("Invalid arguments: {e}"))?,thread_id,context.ct.clone()).await,
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
                    let mut detail = json!({"status":"error","message":message});
                    if message == background::TASK_CHAT_MISMATCH {
                        detail["code"] = json!("task_chat_mismatch");
                    }
                    CallToolResult::structured_error(detail)
                }
            }
            .into());
        }
        Err(ErrorData::invalid_params("Unknown tool", None))
    }
}
