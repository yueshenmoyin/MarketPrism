use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::Manager;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[cfg(windows)]
const BACKEND_EXE: &str = "aitrade-backend.exe";

#[cfg(not(windows))]
const BACKEND_EXE: &str = "aitrade-backend";

const BACKEND_DIR: &str = "aitrade-backend";

const PACKAGED_AUTH_SERVER_URL: Option<&str> = option_env!("AITRADE_AUTH_SERVER_URL");
const DESKTOP_BACKEND_VERSION: &str = env!("CARGO_PKG_VERSION");

struct BackendState {
    process: Mutex<Option<Child>>,
}

impl BackendState {
    fn stop_child(&self) {
        if let Ok(mut process) = self.process.lock() {
            if let Some(mut child) = process.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

impl Drop for BackendState {
    fn drop(&mut self) {
        if let Ok(process) = self.process.get_mut() {
            if let Some(mut child) = process.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

fn response_has_compatible_backend(response: &[u8]) -> bool {
    std::str::from_utf8(response)
        .map(|text| {
            (text.starts_with("HTTP/1.1 200") || text.starts_with("HTTP/1.0 200"))
                && text.contains("\"status\":\"ok\"")
                && text.contains("\"backend\":\"desktop\"")
                && text.contains(&format!("\"version\":\"{}\"", DESKTOP_BACKEND_VERSION))
        })
        .unwrap_or(false)
}

fn backend_healthy() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], 8765));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }

    let mut buffer = [0_u8; 256];
    match stream.read(&mut buffer) {
        Ok(size) => response_has_compatible_backend(&buffer[..size]),
        Err(_) => false,
    }
}

fn stale_backend_cleanup_script() -> &'static str {
    r#"$ErrorActionPreference='SilentlyContinue'; Get-NetTCPConnection -LocalPort 8765 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { $p = Get-Process -Id $_ -ErrorAction SilentlyContinue; if ($p -and $p.ProcessName -eq 'aitrade-backend') { Stop-Process -Id $p.Id -Force } }"#
}

#[cfg(windows)]
fn stop_stale_backend_processes_on_port() {
    let mut command = Command::new("powershell.exe");
    command.args([
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        stale_backend_cleanup_script(),
    ]);
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW);

    let _ = command.status();
    thread::sleep(Duration::from_millis(300));
}

#[cfg(not(windows))]
fn stop_stale_backend_processes_on_port() {}

fn wait_for_backend(child: &mut Child) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if backend_healthy() {
            return Ok(());
        }
        if let Some(status) = child.try_wait().map_err(|err| format!("检查 Python 后端状态失败: {err}"))? {
            return Err(format!("Python 后端启动后立即退出: {status}"));
        }
        thread::sleep(Duration::from_millis(250));
    }
    Err("Python 后端启动超时，请确认依赖已安装且 8765 端口可用。".to_string())
}

fn push_backend_candidates(candidates: &mut Vec<PathBuf>, directory: &Path) {
    candidates.push(directory.join(BACKEND_EXE));
    candidates.push(directory.join(BACKEND_DIR).join(BACKEND_EXE));
}

fn backend_sidecar_candidates(resource_dir: &Path, executable_dir: Option<&Path>) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(directory) = executable_dir {
        push_backend_candidates(&mut candidates, directory);
    }
    push_backend_candidates(&mut candidates, resource_dir);
    push_backend_candidates(&mut candidates, &resource_dir.join("binaries"));
    candidates
}

fn configure_backend_command(command: &mut Command) {
    command.stdout(Stdio::null()).stderr(Stdio::null());
    if let Some(auth_server_url) = PACKAGED_AUTH_SERVER_URL {
        if !auth_server_url.trim().is_empty() {
            command.env("AITRADE_AUTH_SERVER_URL", auth_server_url.trim());
        }
    }

    #[cfg(windows)]
    {
        command.creation_flags(CREATE_NO_WINDOW);
    }
}

fn bundled_backend_command(app: &tauri::AppHandle) -> Option<Command> {
    let resource_dir = app.path().resource_dir().ok()?;
    let executable_dir = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf));
    for candidate in backend_sidecar_candidates(&resource_dir, executable_dir.as_deref()) {
        if candidate.is_file() {
            let mut command = Command::new(candidate);
            configure_backend_command(&mut command);
            return Some(command);
        }
    }
    None
}

fn python_backend_command() -> Command {
    let mut command = Command::new("python");
    command.args(["-m", "aitrade.desktop.api"]);
    configure_backend_command(&mut command);

    command
}

fn backend_command(app: &tauri::AppHandle) -> Command {
    bundled_backend_command(app).unwrap_or_else(python_backend_command)
}

#[tauri::command]
fn start_backend(app: tauri::AppHandle, state: tauri::State<'_, BackendState>) -> Result<String, String> {
    let mut process = state.process.lock().map_err(|_| "后端状态锁定失败。".to_string())?;
    if backend_healthy() {
        return Ok("本地后端已在运行。".to_string());
    }
    stop_stale_backend_processes_on_port();
    if backend_healthy() {
        return Ok("本地后端已在运行。".to_string());
    }
    if let Some(child) = process.as_mut() {
        if child.try_wait().map_err(|err| format!("检查 Python 后端状态失败: {err}"))?.is_none() {
            wait_for_backend(child)?;
            return Ok("本地后端已在运行。".to_string());
        }
        *process = None;
    }

    let mut child = backend_command(&app)
        .spawn()
        .map_err(|err| format!("无法启动 Python 后端: {err}"))?;

    wait_for_backend(&mut child)?;
    *process = Some(child);
    Ok("本地后端已启动。".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendState {
            process: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![start_backend])
        .build(tauri::generate_context!())
        .expect("error while building AITrade desktop app");

    app.run(|app_handle, event| match event {
        tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
            app_handle.state::<BackendState>().stop_child();
            stop_stale_backend_processes_on_port();
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::{
        backend_sidecar_candidates, response_has_compatible_backend, stale_backend_cleanup_script, BACKEND_DIR,
        BACKEND_EXE,
    };
    use std::path::Path;

    #[test]
    fn response_has_compatible_backend_accepts_current_desktop_backend() {
        assert!(response_has_compatible_backend(
            b"HTTP/1.1 200 OK\r\nContent-Length: 55\r\n\r\n{\"status\":\"ok\",\"backend\":\"desktop\",\"version\":\"0.1.0\"}"
        ));
    }

    #[test]
    fn response_has_compatible_backend_rejects_old_health_response() {
        assert!(!response_has_compatible_backend(
            b"HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{\"status\":\"ok\"}"
        ));
        assert!(!response_has_compatible_backend(b"HTTP/1.1 503 Service Unavailable\r\n\r\n"));
    }

    #[test]
    fn sidecar_candidates_include_resource_root_and_binaries_dir() {
        let candidates = backend_sidecar_candidates(Path::new("resources"), Some(Path::new("app")));

        assert!(candidates.contains(&Path::new("app").join(BACKEND_EXE)));
        assert!(candidates.contains(&Path::new("app").join(BACKEND_DIR).join(BACKEND_EXE)));
        assert!(candidates.contains(&Path::new("resources").join(BACKEND_DIR).join(BACKEND_EXE)));
    }

    #[test]
    fn stale_backend_cleanup_script_only_targets_aitrade_backend_on_8765() {
        let script = stale_backend_cleanup_script();

        assert!(script.contains("LocalPort 8765"));
        assert!(script.contains("ProcessName -eq 'aitrade-backend'"));
        assert!(script.contains("Stop-Process"));
    }
}
