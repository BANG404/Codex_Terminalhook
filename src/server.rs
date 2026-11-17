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
            .with_server_info(Implementation::new("terminalhook", env!("TERMINALHOOK_VERSION")))
            .with_instructions("TerminalHook exposes background tasks and event-driven task dashboards only. Call check_background before the first submission to verify the existing shared host. Submit authorized work once with submit_task and a stable task_key, then end the turn and await completion delivery to this chat. Omit until to wait for natural exit; omit timeout_ms for no execution deadline. Do not poll get_task or list_tasks, replay submissions, or start another daemon. Use get_task for diagnosis, cancel_task for explicit cancellation, and retry_delivery only for a saved result whose delivery failed before submission. Uncertain delivery requires reconciliation. The four legacy synchronous/interactive process tools have been removed; use the host terminal for interactive commands. This package uses native events and existing-daemon callbacks without lifecycle hooks.")
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
