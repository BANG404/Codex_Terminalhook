use data_encoding::BASE64;
use rmcp::model::{Icon, ReadResourceResult, ResourceContents, Tool};
use serde_json::json;
pub const URI: &str = "ui://terminalhook/tasks.html";
pub const MIME: &str = "text/html;profile=mcp-app";

pub fn logo() -> Icon {
    let svg = include_bytes!("../plugins/terminalhook/assets/terminalhook-logo.svg");
    Icon::new(format!("data:image/svg+xml;base64,{}", BASE64.encode(svg)))
        .with_mime_type("image/svg+xml")
        .with_sizes(vec!["any".into()])
}

pub fn attach(mut tool: Tool) -> Tool {
    tool.icons = Some(vec![logo()]);
    tool.meta=Some(json!({"ui":{"resourceUri":URI,"visibility":["model","app"]},"openai/ui":{"preferredModelDisplayMode":"inline"}}).as_object().unwrap().clone().into());
    tool
}

pub fn resource() -> ReadResourceResult {
    ReadResourceResult::new(vec![ResourceContents::TextResourceContents {
        uri:URI.into(),mime_type:Some(MIME.into()),text:include_str!("../ui/dist/index.html").into(),
        meta:Some(json!({"ui":{"prefersBorder":false,"csp":{"connectDomains":[],"resourceDomains":[],"frameDomains":[]}}}).as_object().unwrap().clone().into())
    }])
}
