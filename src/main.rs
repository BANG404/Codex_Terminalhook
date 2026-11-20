mod backend;
mod background;
mod bridge;
mod logs;
mod matcher;
mod process;
mod server;
mod spec;
mod tree;
mod ui;

use rmcp::ServiceExt;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(windows)]
    {
        let args: Vec<_> = std::env::args_os().collect();
        if args.get(1).is_some_and(|s| s == "--child-gate") {
            tree::child_gate(&args)?;
            return Ok(());
        }
    }
    if std::env::args().any(|a| a == "--version") {
        println!("terminalhook {}", env!("TERMINALHOOK_VERSION"));
        return Ok(());
    }
    let mut args = std::env::args().skip(1);
    let action = args.next();
    if action.as_deref() == Some("--launch-background-worker") {
        let path = args.next().ok_or("missing task directory")?;
        let pid = background::spawn_worker(std::path::Path::new(&path))?;
        println!("{}", serde_json::json!({"worker_pid":pid}));
        return Ok(());
    }
    if action.as_deref() == Some("--background-worker") {
        let path = args.next().ok_or("missing task directory")?;
        return tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()?
            .block_on(background::worker(std::path::Path::new(&path)))
            .map_err(Into::into);
    }
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(serve())
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let service = server::TerminalHook.serve(rmcp::transport::stdio()).await?;
    let result = tokio::select! {
        result = service.waiting() => result.map(|_| ()),
        _ = tokio::signal::ctrl_c() => Ok(()),
    };
    result?;
    Ok(())
}
