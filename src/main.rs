mod backend;
mod jobs;
mod logs;
mod matcher;
mod process;
mod server;
mod spec;
mod tree;

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
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?
        .block_on(serve())
}

async fn serve() -> Result<(), Box<dyn std::error::Error>> {
    let manager = process::Manager::new()?;
    let service = server::TerminalHook::new(manager.clone())
        .serve(rmcp::transport::stdio())
        .await?;
    let result = tokio::select! {
        result = service.waiting() => result.map(|_| ()),
        _ = tokio::signal::ctrl_c() => Ok(()),
    };
    manager.shutdown().await;
    result?;
    Ok(())
}
