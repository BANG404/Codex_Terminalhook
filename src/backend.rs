pub enum Event {
    Data(usize, Vec<u8>),
    Eof(usize),
    Exited(Option<i32>),
    Error(String),
}

#[cfg(target_os = "macos")]
mod macos;
#[cfg(not(target_os = "macos"))]
mod native;

#[cfg(target_os = "macos")]
pub use macos::Backend;
#[cfg(not(target_os = "macos"))]
pub use native::Backend;
