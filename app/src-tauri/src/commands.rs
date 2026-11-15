//! Os comandos que o JavaScript pode chamar. Cada um tem tipos fixos; nenhum aceita método de IPC, comando,
//! caminho ou URL vindos da interface. Tudo o que bloqueia (socket, subprocesso) roda fora da thread principal.

use crate::Ombro;
use crate::backend::{
    Backend, DaemonState, IntegrationAction, Owner, Provider, ServiceAction, legacy_installed, service_installed,
};
use crate::ipc::{AppError, Client, ErrorKind, Method, PROTOCOL, Result};
use crate::monitor::Snapshot;
use crate::prefs::{AppPrefs, PrefsPatch};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use tauri::{AppHandle, Manager};

async fn blocking<T: Send + 'static>(
    app: AppHandle,
    f: impl FnOnce(&Ombro) -> Result<T> + Send + 'static,
) -> Result<T> {
    tauri::async_runtime::spawn_blocking(move || f(&app.state::<Ombro>()))
        .await
        .unwrap_or_else(|_| Err(AppError::new(ErrorKind::Ipc, "falha interna do app")))
}

fn backend(o: &Ombro) -> Result<&Backend> {
    o.backend.as_ref().map_err(Clone::clone)
}

fn ipc(method: Method, params: Value) -> Result<Value> {
    Client::default().call(method, params)
}

// ---------- estado geral ----------

#[tauri::command]
pub async fn get_snapshot(app: AppHandle) -> Result<Snapshot> {
    blocking(app, |o| Ok(o.monitor.latest.lock().unwrap().clone())).await
}

#[tauri::command]
pub async fn refresh(app: AppHandle) -> Result<()> {
    blocking(app, |o| {
        o.monitor.poke();
        Ok(())
    })
    .await
}

#[derive(Serialize)]
pub struct AppInfo {
    version: String,
    protocol: u64,
    mode: Option<crate::backend::Mode>,
    backend_error: Option<AppError>,
    location_problem: Option<String>,
    service_installed: bool,
    legacy_installed: bool,
    arch: &'static str,
    os_version: Option<String>,
}

#[tauri::command]
pub async fn app_info(app: AppHandle) -> Result<AppInfo> {
    let version = app.package_info().version.to_string();
    blocking(app, move |o| {
        let os_version = std::process::Command::new("/usr/bin/sw_vers")
            .arg("-productVersion")
            .output()
            .ok()
            .map(|out| String::from_utf8_lossy(&out.stdout).trim().to_string());
        Ok(AppInfo {
            version,
            protocol: PROTOCOL,
            mode: o.backend.as_ref().ok().map(|b| b.mode),
            backend_error: o.backend.as_ref().err().cloned(),
            location_problem: o.backend.as_ref().ok().and_then(Backend::location_problem),
            service_installed: service_installed(),
            legacy_installed: legacy_installed(),
            arch: std::env::consts::ARCH,
            os_version,
        })
    })
    .await
}

// ---------- o ombrod ----------

#[tauri::command]
pub async fn daemon_start(app: AppHandle) -> Result<DaemonState> {
    blocking(app, |o| {
        let st = o.start_daemon()?;
        o.monitor.poke();
        Ok(st)
    })
    .await
}

/// Reinicia o ombrod para valer o que só vale "ao reiniciar". Só o do app ou o do launchd.
#[tauri::command]
pub async fn daemon_restart(app: AppHandle) -> Result<DaemonState> {
    blocking(app, |o| {
        let st = match o.daemon.state() {
            DaemonState::Connected { owner: Owner::App, .. } => {
                o.daemon.stop_owned();
                o.start_daemon()?
            }
            DaemonState::Connected {
                owner: Owner::Launchd, ..
            }
            | DaemonState::WaitingForService => {
                backend(o)?.service(ServiceAction::Restart, false)?;
                o.daemon.state()
            }
            DaemonState::Connected {
                owner: Owner::External, ..
            } => {
                return Err(AppError::new(
                    ErrorKind::NotAllowed,
                    "Este ombrod foi iniciado fora do app (num terminal): reinicie-o por lá.",
                ));
            }
            _ => o.start_daemon()?,
        };
        o.monitor.poke();
        Ok(st)
    })
    .await
}

// ---------- leitura pela IPC ----------

#[tauri::command]
pub async fn display_status(app: AppHandle) -> Result<Value> {
    blocking(app, |_| ipc(Method::DisplayStatus, json!({}))).await
}

#[tauri::command]
pub async fn display_preview(app: AppHandle) -> Result<Value> {
    blocking(app, |_| ipc(Method::DisplayPreview, json!({"format": "png"}))).await
}

#[tauri::command]
pub async fn config_get(app: AppHandle) -> Result<Value> {
    blocking(app, |_| ipc(Method::ConfigGet, json!({}))).await
}

#[tauri::command]
pub async fn config_schema(app: AppHandle) -> Result<Value> {
    blocking(app, |_| ipc(Method::ConfigSchema, json!({}))).await
}

// ---------- preferências (validadas de novo pelo ombrod, que é a fonte da verdade) ----------

fn validate_changes(changes: &Map<String, Value>) -> Result<()> {
    let ok_key =
        |k: &str| !k.is_empty() && k.len() <= 40 && k.chars().all(|c| c.is_ascii_lowercase() || c == '_' || c == '.');
    let ok_value = |v: &Value| match v {
        Value::Null | Value::Bool(_) | Value::Number(_) => true,
        Value::String(s) => s.len() <= 64,
        _ => false,
    };
    if changes.is_empty() || changes.len() > 20 || !changes.iter().all(|(k, v)| ok_key(k) && ok_value(v)) {
        return Err(AppError::new(ErrorKind::NotAllowed, "mudança de preferência inválida"));
    }
    Ok(())
}

#[tauri::command]
pub async fn config_update(app: AppHandle, changes: Map<String, Value>, persist: bool) -> Result<Value> {
    validate_changes(&changes)?;
    blocking(app, move |o| {
        let r = ipc(Method::ConfigUpdate, json!({"changes": changes, "persist": persist}));
        o.monitor.poke();
        r
    })
    .await
}

#[tauri::command]
pub async fn display_set_brightness(app: AppHandle, value: u8, persist: bool) -> Result<Value> {
    if value > 100 {
        return Err(AppError::new(ErrorKind::NotAllowed, "o brilho vai de 0 a 100"));
    }
    blocking(app, move |_| {
        ipc(
            Method::DisplaySetBrightness,
            json!({"value": value, "persist": persist}),
        )
    })
    .await
}

#[tauri::command]
pub async fn display_set_layout(app: AppHandle, layout: String, persist: bool) -> Result<Value> {
    if layout.is_empty()
        || layout.len() > 32
        || !layout
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    {
        return Err(AppError::new(ErrorKind::NotAllowed, "nome de layout inválido"));
    }
    blocking(app, move |o| {
        let r = ipc(Method::DisplaySetLayout, json!({"layout": layout, "persist": persist}));
        o.monitor.poke();
        r
    })
    .await
}

// ---------- CLI: integrações, serviço e doctor ----------

#[tauri::command]
pub async fn integrations_list(app: AppHandle) -> Result<Value> {
    blocking(app, |o| backend(o)?.integrations_list()).await
}

#[tauri::command]
pub async fn integration_run(
    app: AppHandle,
    provider: Provider,
    action: IntegrationAction,
    dry_run: bool,
) -> Result<Value> {
    blocking(app, move |o| {
        let r = backend(o)?.integration(provider, action, dry_run);
        o.monitor.poke();
        r
    })
    .await
}

#[tauri::command]
pub async fn service_run(app: AppHandle, action: ServiceAction, dry_run: bool) -> Result<Value> {
    blocking(app, move |o| {
        let b = backend(o)?;
        // Instalar o serviço com um ombrod do app rodando: o do app sai antes, para o launchd assumir a tela.
        if matches!(action, ServiceAction::Install | ServiceAction::Migrate) && !dry_run {
            if let Some(p) = b.location_problem() {
                return Err(AppError::new(ErrorKind::NotAllowed, p));
            }
            o.daemon.stop_owned();
            let _ = o.prefs.update(|p| p.owned_daemon_pid = None);
        }
        let r = b.service(action, dry_run);
        if !dry_run {
            let ok = r
                .as_ref()
                .ok()
                .and_then(|v| v.pointer("/result/ok"))
                .and_then(Value::as_bool)
                == Some(true);
            match action {
                // falhou depois de o app ter parado o próprio ombrod: o app volta a monitorar
                ServiceAction::Install | ServiceAction::Migrate if !ok => {
                    let _ = o.start_daemon();
                }
                // sem o serviço, o monitoramento volta a ser do app (no rollback, não: o Ombro antigo volta a mandar)
                ServiceAction::Uninstall if ok => {
                    let _ = o.start_daemon();
                }
                _ => {}
            }
        }
        o.monitor.poke();
        r
    })
    .await
}

#[tauri::command]
pub async fn doctor_run(app: AppHandle) -> Result<Value> {
    blocking(app, |o| backend(o)?.doctor()).await
}

#[tauri::command]
pub async fn doctor_report(app: AppHandle) -> Result<String> {
    blocking(app, |o| backend(o)?.doctor_report()).await
}

// ---------- preferências do app ----------

#[derive(Serialize)]
pub struct PrefsView {
    #[serde(flatten)]
    prefs: AppPrefs,
    autostart: bool,
}

#[tauri::command]
pub async fn prefs_get(app: AppHandle) -> Result<PrefsView> {
    use tauri_plugin_autostart::ManagerExt;
    let autostart = app.autolaunch().is_enabled().unwrap_or(false);
    blocking(app, move |o| {
        Ok(PrefsView {
            prefs: o.prefs.load(),
            autostart,
        })
    })
    .await
}

#[tauri::command]
pub async fn prefs_set(app: AppHandle, patch: PrefsPatch) -> Result<PrefsView> {
    use tauri_plugin_autostart::ManagerExt;
    use tauri_plugin_notification::{NotificationExt, PermissionState};
    if patch.notifications == Some(true) {
        // A permissão do macOS só é pedida aqui, quando o usuário liga as notificações.
        let estado = app.notification().permission_state().unwrap_or(PermissionState::Prompt);
        let estado = if estado == PermissionState::Granted {
            estado
        } else {
            app.notification().request_permission().unwrap_or(estado)
        };
        if estado != PermissionState::Granted {
            return Err(AppError::new(
                ErrorKind::NotAllowed,
                "O macOS não permitiu notificações do Ombro. Libere em Ajustes do Sistema > Notificações.",
            ));
        }
    }
    let autostart = app.autolaunch().is_enabled().unwrap_or(false);
    blocking(app, move |o| {
        let prefs = o
            .prefs
            .update(|p| {
                if let Some(v) = patch.onboarding_completed {
                    p.onboarding_completed = v;
                }
                if let Some(v) = patch.notifications {
                    p.notifications = v;
                }
            })
            .map_err(|_| AppError::new(ErrorKind::Ipc, "não consegui gravar as preferências do app"))?;
        Ok(PrefsView { prefs, autostart })
    })
    .await
}

/// Abrir o Ombro ao entrar no Mac (só o app, na Menu Bar). O ombrod continua com a regra de sempre: se o
/// serviço está instalado, é o launchd que o inicia; senão, o app inicia um.
#[tauri::command]
pub async fn autostart_set(app: AppHandle, enabled: bool) -> Result<bool> {
    use tauri_plugin_autostart::ManagerExt;
    let m = app.autolaunch();
    let r = if enabled { m.enable() } else { m.disable() };
    r.map_err(|_| AppError::new(ErrorKind::NotAllowed, "não consegui mudar a abertura no login"))?;
    Ok(m.is_enabled().unwrap_or(enabled))
}

// ---------- links (fixos) ----------

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Link {
    Repository,
    Releases,
    Issues,
    Docs,
    Troubleshooting,
    NotificationSettings,
    /// Contents/Resources/licenses/THIRD_PARTY_NOTICES.md, mostrado no Finder (só no app montado)
    ThirdPartyNotices,
}

const REPO: &str = "https://github.com/jvrodrigs/ombro-screen";

#[tauri::command]
pub async fn open_link(app: AppHandle, link: Link) -> Result<()> {
    use tauri_plugin_opener::OpenerExt;
    if let Link::ThirdPartyNotices = link {
        let arquivo = app
            .path()
            .resource_dir()
            .map(|r| r.join("licenses").join("THIRD_PARTY_NOTICES.md"))
            .ok()
            .filter(|p| p.is_file())
            .ok_or_else(|| {
                AppError::new(
                    ErrorKind::NotAllowed,
                    "os avisos de terceiros só existem no app montado (npm run build:app)",
                )
            })?;
        return app
            .opener()
            .reveal_item_in_dir(arquivo)
            .map_err(|_| AppError::new(ErrorKind::NotAllowed, "não consegui mostrar o arquivo"));
    }
    let url = match link {
        Link::ThirdPartyNotices => unreachable!(),
        Link::Repository => REPO.to_string(),
        Link::Releases => format!("{REPO}/releases"),
        Link::Issues => format!("{REPO}/issues"),
        Link::Docs => format!("{REPO}#readme"),
        Link::Troubleshooting => format!("{REPO}/blob/main/docs/troubleshooting.md"),
        Link::NotificationSettings => "x-apple.systempreferences:com.apple.preference.notifications".to_string(),
    };
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|_| AppError::new(ErrorKind::NotAllowed, "não consegui abrir o link"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mudancas_aceitas_e_recusadas() {
        let ok: Map<String, Value> =
            serde_json::from_value(json!({"brilho": 40, "providers.codex": false, "cor_mascote": null})).unwrap();
        assert!(validate_changes(&ok).is_ok());
        for ruim in [
            json!({}),
            json!({"lib": {"x": 1}}),
            json!({"Brilho": 1}),
            json!({"x; rm": 1}),
            json!({"porta": "a".repeat(65)}),
            json!({"lista": [1, 2]}),
        ] {
            let m: Map<String, Value> = serde_json::from_value(ruim).unwrap();
            assert!(validate_changes(&m).is_err());
        }
    }
}
