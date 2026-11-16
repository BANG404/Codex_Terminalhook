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
        vec![
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
        ]
    }
}

impl ServerHandler for TerminalHook {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new("terminalhook", env!("TERMINALHOOK_VERSION")))
            .with_instructions("Call these tools directly and await one result. For one-shot tasks, make run_until await the final outcome or natural exit, not startup/progress markers. Local waiting avoids model polling only within a call; repeated short waits still cause model round trips. Set a task-sized timeout up to 600000 ms below the client limit; do not use short timeouts for periodic updates. Do not automatically repeat waits after timeout: diagnose/report and decide recovery from new evidence or an explicit instruction to continue. Reserve wait_output for retained processes when a genuine intermediate action or other useful work separated the waits. Use write_input with wait for input and its response together. Keep process_id and cursor; stop_process cleans up retained commands. Match does not prove successful exit; inspect the natural exit code when success requires completion. PTY merges sources into terminal. Commands use MCP OS permissions; process ids/history/temp logs live only for this connection.")
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
