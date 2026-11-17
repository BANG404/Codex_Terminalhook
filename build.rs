fn main() {
    println!("cargo:rerun-if-changed=ui/dist/index.html");
    assert!(
        std::path::Path::new("ui/dist/index.html").is_file(),
        "Dashboard bundle missing. Run npm ci && npm run build:ui before Cargo."
    );
    println!("cargo:rerun-if-env-changed=TERMINALHOOK_BUILD_VERSION");
    let version = std::env::var("TERMINALHOOK_BUILD_VERSION")
        .unwrap_or_else(|_| std::env::var("CARGO_PKG_VERSION").unwrap());
    assert!(
        !version.is_empty()
            && version
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b".-+".contains(&c)),
        "invalid build version"
    );
    println!("cargo:rustc-env=TERMINALHOOK_VERSION={version}");
}
