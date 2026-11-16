use rmcp::model::{ReadResourceResult, ResourceContents, Tool};
use serde_json::json;
pub const URI: &str = "ui://terminalhook/tasks.html";
pub const MIME: &str = "text/html;profile=mcp-app";

pub fn attach(mut tool: Tool) -> Tool {
    tool.meta=Some(json!({"ui":{"resourceUri":URI,"visibility":["model","app"]},"openai/ui":{"preferredModelDisplayMode":"inline"}}).as_object().unwrap().clone().into());
    tool
}

pub fn resource() -> ReadResourceResult {
    ReadResourceResult::new(vec![ResourceContents::TextResourceContents {
        uri:URI.into(),mime_type:Some(MIME.into()),text:include_str!("../ui/dist/index.html").into(),
        meta:Some(json!({"ui":{"prefersBorder":true,"csp":{"connectDomains":[],"resourceDomains":[],"frameDomains":[]}}}).as_object().unwrap().clone().into())
    }])
}
