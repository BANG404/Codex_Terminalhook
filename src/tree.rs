use std::io;

#[cfg(windows)]
mod platform {
    use super::*;
    use std::{ffi::OsString, os::windows::ffi::OsStrExt};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE, WAIT_OBJECT_0},
        System::{
            IO::{CreateIoCompletionPort, GetQueuedCompletionStatus},
            JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
                JOBOBJECT_ASSOCIATE_COMPLETION_PORT, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                JobObjectAssociateCompletionPortInformation, JobObjectExtendedLimitInformation,
                SetInformationJobObject, TerminateJobObject,
            },
            SystemServices::JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO,
            Threading::{
                CreateEventW, OpenEventW, SYNCHRONIZATION_SYNCHRONIZE, SetEvent,
                WaitForSingleObject,
            },
        },
    };

    pub struct ProcessTree {
        job: HANDLE,
        port: HANDLE,
    }
    unsafe impl Send for ProcessTree {}
    pub struct Gate {
        handle: HANDLE,
        name: String,
    }
    unsafe impl Send for Gate {}
    impl Gate {
        pub fn new() -> io::Result<Self> {
            let name = format!("Local\\TerminalHook-{}", uuid::Uuid::new_v4());
            let wide: Vec<u16> = std::ffi::OsStr::new(&name)
                .encode_wide()
                .chain(Some(0))
                .collect();
            let handle = unsafe { CreateEventW(std::ptr::null(), 1, 0, wide.as_ptr()) };
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            Ok(Self { handle, name })
        }
        pub fn command(
            &self,
            program: &str,
            args: &[String],
        ) -> io::Result<(OsString, Vec<OsString>)> {
            let mut result = vec![
                "--child-gate".into(),
                self.name.clone().into(),
                program.into(),
            ];
            result.extend(args.iter().map(OsString::from));
            Ok((std::env::current_exe()?.into_os_string(), result))
        }
        pub fn release(&self) -> io::Result<()> {
            if unsafe { SetEvent(self.handle) } == 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(())
            }
        }
    }
    impl Drop for Gate {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.handle);
            }
        }
    }
    impl ProcessTree {
        pub fn attach(handle: std::os::windows::io::RawHandle) -> io::Result<Self> {
            unsafe {
                let raw = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if raw.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let port = CreateIoCompletionPort(INVALID_HANDLE_VALUE, std::ptr::null_mut(), 0, 1);
                let job = Self { job: raw, port };
                if port.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let association = JOBOBJECT_ASSOCIATE_COMPLETION_PORT {
                    CompletionKey: std::ptr::null_mut(),
                    CompletionPort: port,
                };
                if SetInformationJobObject(
                    raw,
                    JobObjectAssociateCompletionPortInformation,
                    &association as *const _ as *const _,
                    std::mem::size_of_val(&association) as u32,
                ) == 0
                {
                    return Err(io::Error::last_os_error());
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                if SetInformationJobObject(
                    raw,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const _,
                    std::mem::size_of_val(&info) as u32,
                ) == 0
                    || AssignProcessToJobObject(raw, handle as HANDLE) == 0
                {
                    return Err(io::Error::last_os_error());
                }
                Ok(job)
            }
        }
    }
    impl ProcessTree {
        pub async fn terminate(self) -> Result<(), String> {
            tokio::task::spawn_blocking(move || self.terminate_blocking())
                .await
                .map_err(|e| e.to_string())?
        }
        fn terminate_blocking(self) -> Result<(), String> {
            unsafe {
                if TerminateJobObject(self.job, 1) == 0 {
                    return Err(format!(
                        "TerminateJobObject failed: {}",
                        io::Error::last_os_error()
                    ));
                }
                let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
                loop {
                    let mut message = 0;
                    let mut key = 0;
                    let mut overlapped = std::ptr::null_mut();
                    let remaining = deadline
                        .saturating_duration_since(std::time::Instant::now())
                        .as_millis()
                        .min(u32::MAX as u128) as u32;
                    if remaining == 0
                        || GetQueuedCompletionStatus(
                            self.port,
                            &mut message,
                            &mut key,
                            &mut overlapped,
                            remaining,
                        ) == 0
                    {
                        return Err(
                            "job cleanup did not report ACTIVE_PROCESS_ZERO within 10s".into()
                        );
                    }
                    if message == JOB_OBJECT_MSG_ACTIVE_PROCESS_ZERO {
                        return Ok(());
                    }
                }
            }
        }
    }
    impl Drop for ProcessTree {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.job);
                if !self.port.is_null() {
                    CloseHandle(self.port);
                }
            }
        }
    }

    pub fn child_gate(args: &[OsString]) -> io::Result<()> {
        if args.len() < 4 {
            return Err(io::Error::other("invalid child gate invocation"));
        }
        let name: Vec<u16> = args[2].encode_wide().chain(Some(0)).collect();
        let event = unsafe { OpenEventW(SYNCHRONIZATION_SYNCHRONIZE, 0, name.as_ptr()) };
        if event.is_null() {
            return Err(io::Error::last_os_error());
        }
        let result = unsafe { WaitForSingleObject(event, 10_000) };
        unsafe {
            CloseHandle(event);
        }
        if result != WAIT_OBJECT_0 {
            return Err(io::Error::other("parent did not release child gate in 10s"));
        }
        // Target and all ordinary descendants inherit the job, before any target code runs.
        let status = std::process::Command::new(&args[3])
            .args(&args[4..])
            .status()?;
        std::process::exit(status.code().unwrap_or(1));
    }
}
#[cfg(unix)]
mod platform {
    use super::*;
    pub struct ProcessTree(i32);
    impl ProcessTree {
        pub fn attach(pid: u32) -> io::Result<Self> {
            Ok(Self(pid as i32))
        }
        pub async fn terminate(self) -> Result<(), String> {
            drop(self);
            Ok(())
        }
    }
    impl Drop for ProcessTree {
        fn drop(&mut self) {
            unsafe {
                libc::kill(-self.0, libc::SIGKILL);
            }
        }
    }
}
pub use platform::*;
