//! A detached worker must inherit neither MCP containment nor launcher pipes.
use std::{os::windows::ffi::OsStrExt, path::Path};
use windows_sys::Win32::{
    Foundation::CloseHandle,
    System::Threading::{
        CREATE_BREAKAWAY_FROM_JOB, CREATE_NEW_PROCESS_GROUP, CreateProcessW, DETACHED_PROCESS,
        PROCESS_INFORMATION, STARTUPINFOW,
    },
};

pub fn spawn(path: &Path) -> Result<u32, String> {
    let executable = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut application: Vec<u16> = executable.as_os_str().encode_wide().collect();
    let directory: Vec<u16> = path.as_os_str().encode_wide().collect();
    if application.contains(&34)
        || directory.contains(&34)
        || application.contains(&0)
        || directory.contains(&0)
    {
        return Err("invalid worker executable or task directory".into());
    }
    // Both quoted arguments are native filenames and cannot contain quotes.
    // No shell is involved; the task directory always ends in a generated ID.
    let mut command = vec![34];
    command.extend_from_slice(&application);
    command.extend("\" --background-worker \"".encode_utf16());
    command.extend_from_slice(&directory);
    command.extend([34, 0]);
    application.push(0);
    let mut startup: STARTUPINFOW = unsafe { std::mem::zeroed() };
    startup.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
    let mut process: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
    let created = unsafe {
        CreateProcessW(
            application.as_ptr(),
            command.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            0, // Never inherit launcher stdout, stderr, or any unrelated host handles.
            CREATE_BREAKAWAY_FROM_JOB | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            std::ptr::null(),
            std::ptr::null(),
            &startup,
            &mut process,
        )
    };
    if created == 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    unsafe {
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
    }
    Ok(process.dwProcessId)
}
