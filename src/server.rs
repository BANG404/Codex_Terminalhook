use rmcp::{
    ErrorData, RoleServer, ServerHandler,
    model::{
        CallToolRequestParams, CallToolResponse, CallToolResult, Implementation, ListToolsResult,
        PaginatedRequestParams, ServerCapabilities, ServerConfig, Tool,
    },
    service::RequestContext,
};
use serde_json::{Value, json};

use crate::{
    jobs::{AuditRequest, HookRequest, Jobs, PrepareRequest, SubmitRequest},
    process::Manager,
    spec::{ProcessRequest, RunRequest, WaitRequest, WriteRequest},
};

#[derive(Clone)]
pub struct TerminalHook {
    manager: Manager,
    jobs: Jobs,
}

impl TerminalHook {
    pub fn new(manager: Manager) -> Self {
        Self {
            jobs: Jobs::new(manager.clone()),
            manager,
        }
    }

    fn tools() -> Vec<Tool> {
        vec![
            Tool::new(
                "submit_task",
                "Submit a background command and return immediately. Requires the plugin PreToolUse hook to inject session_id/turn_id; omit these fields from model calls. After submission, end the turn. The synchronous Stop hook waits locally and continues with results; do not poll or call wait tools.",
                schemars::schema_for!(SubmitRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "prepare_task",
                "Host-only PreToolUse hook: bind submit_task input to its actual chat and turn. Do not call from the model.",
                schemars::schema_for!(PrepareRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "await_tasks",
                "Host-only synchronous Stop hook: wait for this chat's submitted tasks using local events and return one continuation. Do not call from the model.",
                schemars::schema_for!(HookRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "audit_event",
                "Host-only model lifecycle hook. Record model-request boundaries for verifying that Stop waiting does not wake the model. Do not call from the model.",
                schemars::schema_for!(AuditRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "run_until",
                "Start a command and wait locally for an output regex, exit, or timeout without polling. Default: terminate on match/timeout. Use on_match=keep_running to retain a service, then wait_output/write_input with returned process_id and cursor. Pipe keeps stdout/stderr separate; mode=pty is a real terminal with merged terminal output. Supports line or bounded multiline window matching and rotating raw logs.",
                schemars::schema_for!(RunRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "wait_output",
                "Wait locally for new output from a TerminalHook process after its previous cursor. Replays bounded buffered output then waits without polling. Timeout/cancellation preserves the process. Reports cursor_expired if history was evicted.",
                schemars::schema_for!(WaitRequest)
                    .as_object()
                    .unwrap()
                    .clone(),
            ),
            Tool::new(
                "write_input",
                "Write UTF-8 input to a managed process. Optionally include wait with regex conditions; the output cursor is registered before sending to avoid missing immediate responses. Pipe EOF can be sent explicitly. PTY uses a merged terminal stream.",
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
        ]
    }
}

impl ServerHandler for TerminalHook {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("terminalhook", env!("TERMINALHOOK_VERSION")))
            .with_instructions("Await run_until or wait_output once; they wait locally without model polling. Keep process_id and cursor to continue after a match. Use write_input with wait to register before sending and avoid missing fast responses. stop_process cleans up retained commands. Set timeout_ms below the client's MCP timeout. Match does not prove successful exit. PTY merges sources into terminal. Commands use MCP OS permissions; process ids/history/temp logs live only for this connection.")
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

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        context: RequestContext<RoleServer>,
    ) -> Result<CallToolResponse, ErrorData> {
        let args = Value::Object(request.arguments.unwrap_or_default());
        let result = match request.name.as_ref() {
            "submit_task" => match serde_json::from_value::<SubmitRequest>(args) {
                Ok(input) => self.jobs.submit(input),
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
            "prepare_task" => match serde_json::from_value::<PrepareRequest>(args) {
                Ok(input) => self.jobs.prepare(input),
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
            "await_tasks" => match serde_json::from_value::<HookRequest>(args) {
                Ok(input) => self.jobs.await_tasks(input, context.ct).await,
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
            "audit_event" => match serde_json::from_value::<AuditRequest>(args) {
                Ok(input) => self.jobs.audit(input),
                Err(e) => Err(format!("Invalid arguments: {e}")),
            },
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
