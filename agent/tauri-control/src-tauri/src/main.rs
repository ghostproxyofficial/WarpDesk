#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use reqwest::blocking::Client;
use reqwest::Url;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fs::{self, OpenOptions};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

#[derive(Debug, Deserialize)]
struct RuntimeConfig {
    #[serde(rename = "hostUrl")]
    host_url: String,
    username: String,
    password: String,
    fps: u32,
    scale: u32,
    #[serde(rename = "monitorIndex")]
    monitor_index: u32,
}

#[derive(Debug, Serialize)]
struct BackendState {
    running: bool,
    pid: Option<u32>,
}

#[derive(Debug, Serialize)]
struct ConnectResult {
    success: bool,
    message: String,
    #[serde(rename = "desktopUrl")]
    desktop_url: Option<String>,
    token: Option<String>,
}

#[derive(Debug, Serialize)]
struct MonitorOption {
    index: u32,
    label: String,
}

struct BackendProc(Mutex<Option<Child>>);

fn normalize_url(raw: &str) -> Result<Url, String> {
    let mut text = raw.trim().to_string();
    if text.is_empty() {
        return Err("Host URL is empty".to_string());
    }
    if !text.starts_with("http://") && !text.starts_with("https://") {
        text = format!("https://{}", text);
    }
    if text.ends_with('/') {
        text.pop();
    }
    Url::parse(&text).map_err(|e| format!("Invalid URL: {e}"))
}

fn host_port_from_url(url: &Url) -> Result<(String, u16), String> {
    let host = url
        .host_str()
        .ok_or_else(|| "URL host is missing".to_string())?
        .to_string();
    let port = url
        .port_or_known_default()
        .ok_or_else(|| "URL port is missing".to_string())?;
    Ok((host, port))
}

fn candidate_agent_roots() -> Result<Vec<PathBuf>, String> {
    let mut roots = Vec::new();

    let mut push_variants = |base: PathBuf| {
        roots.push(base.clone());
        roots.push(base.join("agent"));
        roots.push(base.join("_up_"));
        roots.push(base.join("_up_").join("_up_"));
        roots.push(base.join("resources"));
        roots.push(base.join("resources").join("_up_"));
        roots.push(base.join("resources").join("_up_").join("_up_"));
    };

    if let Ok(env_root) = std::env::var("WARPDESK_AGENT_DIR") {
        push_variants(PathBuf::from(env_root));
    }

    if let Ok(resources_dir) = std::env::var("TAURI_RESOURCES_DIR") {
        push_variants(PathBuf::from(resources_dir));
    }

    if let Ok(cwd) = std::env::current_dir() {
        push_variants(cwd.clone());

        let mut p = cwd;
        for _ in 0..6 {
            if let Some(parent) = p.parent() {
                push_variants(parent.to_path_buf());
                p = parent.to_path_buf();
            } else {
                break;
            }
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            push_variants(exe_dir.to_path_buf());

            let mut p = exe_dir.to_path_buf();
            for _ in 0..6 {
                if let Some(parent) = p.parent() {
                    push_variants(parent.to_path_buf());
                    p = parent.to_path_buf();
                } else {
                    break;
                }
            }
        }
    }

    let mut unique = Vec::new();
    for root in roots {
        if !unique.iter().any(|x: &PathBuf| x == &root) {
            unique.push(root);
        }
    }

    if unique.is_empty() {
        return Err("No candidate roots found".to_string());
    }

    Ok(unique)
}

fn resolve_agent_paths() -> Result<(PathBuf, PathBuf), String> {
    let candidates = candidate_agent_roots()?;

    for root in candidates {
        let app_py = root.join("app.py");
        if !app_py.exists() {
            continue;
        }

        let app_dir = app_py
            .parent()
            .ok_or_else(|| "Failed to resolve app.py parent".to_string())?
            .to_path_buf();

        let mut web_candidates = vec![app_dir.join("web"), app_dir.join("_up_").join("web")];
        web_candidates.push(app_dir.join("_up_").join("_up_").join("web"));

        if let Some(parent) = app_dir.parent() {
            web_candidates.push(parent.join("web"));
            web_candidates.push(parent.join("_up_").join("web"));
            web_candidates.push(parent.join("_up_").join("_up_").join("web"));

            if let Some(grand_parent) = parent.parent() {
                web_candidates.push(grand_parent.join("web"));
                web_candidates.push(grand_parent.join("_up_").join("web"));
                web_candidates.push(grand_parent.join("_up_").join("_up_").join("web"));
            }
        }

        for web in web_candidates {
            if web.exists() {
                return Ok((app_py, web));
            }
        }
    }

    Err("Unable to locate app.py/web resources for backend startup".to_string())
}

fn select_python(app_py: &Path) -> String {
    if let Ok(py) = std::env::var("WARPDESK_PYTHON_EXE") {
        if !py.trim().is_empty() {
            return py;
        }
    }

    let app_dir = app_py.parent().unwrap_or_else(|| Path::new("."));
    let mut candidates: Vec<PathBuf> = vec![
        app_dir.join(".venv").join("Scripts").join("python.exe"),
        app_dir.join("venv").join("Scripts").join("python.exe"),
        app_dir
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(".venv")
            .join("Scripts")
            .join("python.exe"),
        app_dir
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("venv")
            .join("Scripts")
            .join("python.exe"),
    ];

    candidates.push(app_dir.join(".venv").join("bin").join("python3"));
    candidates.push(app_dir.join(".venv").join("bin").join("python"));
    candidates.push(app_dir.join("venv").join("bin").join("python3"));
    candidates.push(app_dir.join("venv").join("bin").join("python"));
    candidates.push(
        app_dir
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(".venv")
            .join("bin")
            .join("python3"),
    );
    candidates.push(
        app_dir
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(".venv")
            .join("bin")
            .join("python"),
    );
    candidates.push(
        app_dir
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("venv")
            .join("bin")
            .join("python3"),
    );
    candidates.push(
        app_dir
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join("venv")
            .join("bin")
            .join("python"),
    );

    for candidate in candidates {
        if candidate.exists() {
            return candidate.to_string_lossy().to_string();
        }
    }

    if cfg!(target_os = "windows") {
        "python".to_string()
    } else {
        "python3".to_string()
    }
}

#[tauri::command]
fn list_monitors(app: tauri::AppHandle) -> Result<Vec<MonitorOption>, String> {
    let monitors = app
        .available_monitors()
        .map_err(|e| format!("Failed to detect monitors: {e}"))?;

    if monitors.is_empty() {
        return Ok(vec![MonitorOption {
            index: 1,
            label: "Monitor 1".to_string(),
        }]);
    }

    let options = monitors
        .iter()
        .enumerate()
        .map(|(i, monitor)| {
            let idx = (i + 1) as u32;
            let label = monitor
                .name()
                .filter(|n| !n.trim().is_empty())
                .map(|n| format!("Monitor {idx} - {n}"))
                .unwrap_or_else(|| format!("Monitor {idx}"));
            MonitorOption { index: idx, label }
        })
        .collect();

    Ok(options)
}

#[tauri::command]
fn backend_state(state: tauri::State<'_, BackendProc>) -> BackendState {
    let mut lock = state.0.lock().expect("backend mutex poisoned");
    if let Some(child) = lock.as_mut() {
        match child.try_wait() {
            Ok(Some(_)) => {
                *lock = None;
                return BackendState {
                    running: false,
                    pid: None,
                };
            }
            Ok(None) => {
                return BackendState {
                    running: true,
                    pid: Some(child.id()),
                };
            }
            Err(_) => {
                *lock = None;
            }
        }
    }

    BackendState {
        running: false,
        pid: None,
    }
}

#[tauri::command]
fn check_port(host_url: String) -> Result<String, String> {
    let url = normalize_url(&host_url)?;
    let (host, port) = host_port_from_url(&url)?;

    let mut addrs = (host.as_str(), port)
        .to_socket_addrs()
        .map_err(|e| format!("Failed to resolve host: {e}"))?;
    let addr = addrs
        .next()
        .ok_or_else(|| "Host resolution returned no address".to_string())?;

    match TcpStream::connect_timeout(&addr, Duration::from_millis(1200)) {
        Ok(_) => Ok(format!("Port {port} reachable")),
        Err(e) => Ok(format!("Port {port} not reachable: {e}")),
    }
}

#[tauri::command]
fn connect_remote(host_url: String, username: String, password: String) -> Result<ConnectResult, String> {
    let base = normalize_url(&host_url)?;
    let login_url = base
        .join("api/login")
        .map_err(|e| format!("Invalid login URL: {e}"))?;

    let client = Client::builder()
        .danger_accept_invalid_certs(true)
        .timeout(Duration::from_secs(6))
        .build()
        .map_err(|e| format!("HTTP client init failed: {e}"))?;

    let response = client
        .post(login_url)
        .json(&json!({
            "username": username,
            "password": password,
        }))
        .send()
        .map_err(|e| format!("Connect request failed: {e}"))?;

    let status = response.status();
    let body = response.text().unwrap_or_default();
    let parsed: serde_json::Value = serde_json::from_str(&body).unwrap_or_else(|_| json!({}));

    let success = parsed
        .get("success")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);

    if !status.is_success() || !success {
        let err = parsed
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("Login failed")
            .to_string();
        return Ok(ConnectResult {
            success: false,
            message: err,
            desktop_url: None,
            token: None,
        });
    }

    let token = parsed
        .get("token")
        .and_then(|v| v.as_str())
        .unwrap_or_default()
        .to_string();

    let mut desktop_url = base;
    desktop_url.set_path("/desktop.html");

    Ok(ConnectResult {
        success: true,
        message: "Connected".to_string(),
        desktop_url: Some(desktop_url.to_string()),
        token: Some(token),
    })
}

#[tauri::command]
fn start_backend(config: RuntimeConfig, state: tauri::State<'_, BackendProc>) -> Result<(), String> {
    let mut lock = state.0.lock().map_err(|_| "backend mutex poisoned".to_string())?;

    if let Some(child) = lock.as_mut() {
        if let Ok(None) = child.try_wait() {
            return Ok(());
        }
        *lock = None;
    }

    let (app_py, web_root) = resolve_agent_paths()?;
    let app_root = app_py
        .parent()
        .ok_or_else(|| "Failed to resolve app root".to_string())?
        .to_path_buf();

    let log_dir = app_root.join("logs");
    let _ = fs::create_dir_all(&log_dir);
    let log_path = log_dir.join("warpdesk-backend.log");

    let python = select_python(&app_py);
    let out_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|e| format!("Failed to open backend log file: {e}"))?;
    let err_file = out_file
        .try_clone()
        .map_err(|e| format!("Failed to clone backend log handle: {e}"))?;

    let (_, port) = host_port_from_url(&normalize_url(&config.host_url)?)?;

    let mut cmd = Command::new(python);
    cmd.current_dir(&app_root)
        .arg(&app_py)
        .env("WARPDESK_PORT", port.to_string())
        .env("WARPDESK_USER", config.username)
        .env("WARPDESK_PASSWORD", config.password)
        .env("WARPDESK_FPS", config.fps.to_string())
        .env("WARPDESK_MAX_FPS", config.fps.to_string())
        .env("WARPDESK_SCALE", config.scale.to_string())
        .env("WARPDESK_MONITOR_INDEX", config.monitor_index.to_string())
        .env("WARPDESK_WEB_ROOT", web_root)
        .env("WARPDESK_TUI", "0")
        .env("WARPDESK_LAUNCHER_PLAIN", "1")
        .stdout(Stdio::from(out_file))
        .stderr(Stdio::from(err_file));

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("Failed to start backend: {e}. Log: {}", log_path.display()))?;

    std::thread::sleep(Duration::from_millis(900));
    if let Ok(Some(status)) = child.try_wait() {
        return Err(format!(
            "Backend exited immediately with status {status}. Check log: {}",
            log_path.display()
        ));
    }

    *lock = Some(child);
    Ok(())
}

#[tauri::command]
fn stop_backend(state: tauri::State<'_, BackendProc>) -> Result<(), String> {
    let mut lock = state.0.lock().map_err(|_| "backend mutex poisoned".to_string())?;
    if let Some(mut child) = lock.take() {
        let _ = child.kill();
    }
    Ok(())
}

fn main() {
    tauri::Builder::default()
        .manage(BackendProc(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            backend_state,
            check_port,
            connect_remote,
            list_monitors,
            start_backend,
            stop_backend
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
