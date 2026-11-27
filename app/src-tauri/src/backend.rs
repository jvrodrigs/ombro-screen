//! O backend Python: onde ele está, a CLI do Ombro (integrações, serviço e doctor) e o ombrod que o app inicia.
//!
//! - App distribuído: `Contents/Resources/runtime/bin/python3` (CPython relocável) e `Contents/Resources/backend/`
//!   (o código do repositório, pré-compilado). O hook e o LaunchAgent gravam esses caminhos, então só deixamos
//!   instalar hooks ou o serviço com o app em /Applications (ou ~/Applications) e fora do App Translocation.
//! - Desenvolvimento (`tauri dev`): o próprio repositório e o python do `.venv`.
//!
//! Quem cuida do ombrod (nunca dois ao mesmo tempo; o lock de instância do backend garante isso de qualquer jeito):
//!   1. já há um ombrod respondendo no socket (do launchd, de um terminal ou de uma execução anterior): o app só conversa;
//!   2. o LaunchAgent do ombrod está instalado: quem inicia é o launchd; o app espera e não inicia outro;
//!   3. senão o app inicia um ombrod filho e, ao sair, encerra esse (e só esse) com SIGTERM.

use crate::ipc::{AppError, Client, ErrorKind, Method, Result, home};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs::OpenOptions;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::sync::mpsc;
use std::time::{Duration, Instant};

/// O mesmo PATH do LaunchAgent (ombro/launchagent.py): sem ele, um app aberto pelo Finder não acha codex,
/// cursor-agent nem macmon. Nenhum desses é obrigatório.
const PATH: &str = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin";
/// Flags do python embarcado: ignora PYTHON* do ambiente (-E), o site do usuário (-s) e não grava .pyc (-B).
const PY_FLAGS: [&str; 3] = ["-E", "-s", "-B"];
const DAEMON_LABEL: &str = "com.ombro-screen.daemon";
const LEGACY_LABEL: &str = "com.ombro-screen";

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Bundled,
    Development,
}

#[derive(Debug, Clone)]
pub struct Backend {
    pub mode: Mode,
    pub python: PathBuf,
    pub root: PathBuf,
}

impl Backend {
    /// `resources`: a pasta de recursos do app (`Contents/Resources`), quando existe.
    pub fn locate(resources: Option<PathBuf>) -> Result<Backend> {
        if let Some(res) = resources {
            let root = res.join("backend");
            let python = res.join("runtime").join("bin").join("python3");
            if root.join("ombrod.py").is_file() && python.is_file() {
                return Ok(Backend {
                    mode: Mode::Bundled,
                    python,
                    root,
                });
            }
        }
        if cfg!(debug_assertions) {
            // Só em desenvolvimento: o repositório onde este crate está (app/src-tauri -> raiz).
            let root = std::env::var_os("OMBRO_BACKEND_DIR")
                .map(PathBuf::from)
                .unwrap_or_else(|| Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join(".."));
            let root = root.canonicalize().unwrap_or(root);
            // o .venv do repositório; senão o primeiro python3 dos lugares de sempre (o do sistema, por último)
            let candidatos = [
                root.join(".venv").join("bin").join("python3"),
                PathBuf::from("/opt/homebrew/bin/python3"),
                PathBuf::from("/usr/local/bin/python3"),
                PathBuf::from("/usr/bin/python3"),
            ];
            let python = candidatos.into_iter().find(|p| p.is_file());
            if let Some(python) = python
                && root.join("ombrod.py").is_file()
            {
                return Ok(Backend {
                    mode: Mode::Development,
                    python,
                    root,
                });
            }
        }
        Err(AppError::new(
            ErrorKind::BackendMissing,
            "o backend do Ombro não foi encontrado dentro do app",
        ))
    }

    fn command(&self, script: &Path) -> Command {
        let mut c = Command::new(&self.python);
        c.args(PY_FLAGS)
            .arg(script)
            .current_dir(&self.root)
            .env("PATH", PATH)
            .env("PYTHONUNBUFFERED", "1");
        c
    }

    /// Por que este app não pode gravar o próprio caminho em arquivos de fora (hooks, LaunchAgent); None = pode.
    pub fn location_problem(&self) -> Option<String> {
        if self.mode == Mode::Development {
            return None;
        }
        let exe = std::env::current_exe().ok()?;
        let s = exe.to_string_lossy();
        if s.contains("/AppTranslocation/") {
            return Some("O Ombro está rodando direto do download (App Translocation). Arraste o Ombro para a pasta Aplicativos e abra de lá.".into());
        }
        let user_apps = home().join("Applications");
        if !(exe.starts_with("/Applications") || exe.starts_with(&user_apps)) {
            return Some("O Ombro precisa estar na pasta Aplicativos para instalar hooks ou o serviço (eles gravam o caminho do app).".into());
        }
        None
    }

    /// Roda `ombro <args>` e devolve (código de saída, stdout). Mata o processo (só ele) se passar do tempo.
    fn run_cli(&self, args: &[&str], timeout: Duration) -> Result<(i32, String)> {
        let mut c = self.command(&self.root.join("ombro"));
        c.args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let child = c
            .spawn()
            .map_err(|_| AppError::new(ErrorKind::BackendMissing, "não consegui rodar o backend do Ombro"))?;
        let pid = child.id() as libc::pid_t;
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = tx.send(child.wait_with_output());
        });
        let out = match rx.recv_timeout(timeout) {
            Ok(r) => r,
            Err(_) => {
                // SAFETY: pid é o do filho que acabamos de criar e que ainda não foi colhido.
                unsafe { libc::kill(pid, libc::SIGKILL) };
                return Err(AppError::new(
                    ErrorKind::Cli,
                    "a operação demorou demais e foi cancelada",
                ));
            }
        }
        .map_err(|_| AppError::new(ErrorKind::Cli, "a CLI do Ombro falhou"))?;
        Ok((
            out.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&out.stdout).into_owned(),
        ))
    }

    /// Como run_cli, para os comandos com `--json`: o resultado é o JSON que a CLI imprimiu (também em falhas,
    /// que vêm como `{"ok": false, "error": ...}`).
    fn run_cli_json(&self, args: &[&str], timeout: Duration) -> Result<Value> {
        let (code, stdout) = self.run_cli(args, timeout)?;
        let v: Value = serde_json::from_str(stdout.trim()).map_err(|_| {
            AppError::new(
                ErrorKind::Cli,
                format!("a CLI do Ombro não devolveu JSON (saída {code})"),
            )
        })?;
        Ok(json!({"exit_code": code, "result": v}))
    }

    pub fn integrations_list(&self) -> Result<Value> {
        self.run_cli_json(&["integrations", "list", "--json"], Duration::from_secs(15))
    }

    pub fn integration(&self, provider: Provider, action: IntegrationAction, dry_run: bool) -> Result<Value> {
        let writes = matches!(action, IntegrationAction::Install) && !dry_run;
        if writes && let Some(p) = self.location_problem() {
            return Err(AppError::new(ErrorKind::NotAllowed, p));
        }
        let mut args = vec!["integrations", action.as_str(), provider.as_str()];
        if dry_run && action != IntegrationAction::Check {
            args.push("--dry-run");
        }
        args.push("--json");
        self.run_cli_json(&args, Duration::from_secs(20))
    }

    pub fn service(&self, action: ServiceAction, dry_run: bool) -> Result<Value> {
        let writes_path = matches!(action, ServiceAction::Install | ServiceAction::Migrate);
        if writes_path
            && !dry_run
            && let Some(p) = self.location_problem()
        {
            return Err(AppError::new(ErrorKind::NotAllowed, p));
        }
        let mut args = vec!["service", action.as_str()];
        if dry_run && action != ServiceAction::Status {
            args.push("--dry-run");
        }
        args.push("--json");
        self.run_cli_json(&args, Duration::from_secs(30))
    }

    pub fn doctor(&self) -> Result<Value> {
        self.run_cli_json(&["doctor", "--json"], Duration::from_secs(45))
    }

    /// O relatório de suporte do doctor (sem dados pessoais: o próprio doctor sanitiza).
    pub fn doctor_report(&self) -> Result<String> {
        let (_code, out) = self.run_cli(&["doctor", "--report"], Duration::from_secs(45))?;
        if out.trim().is_empty() {
            return Err(AppError::new(ErrorKind::Cli, "o doctor não devolveu o relatório"));
        }
        Ok(out)
    }
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Claude,
    Codex,
    Cursor,
    Opencode,
}

impl Provider {
    pub fn as_str(self) -> &'static str {
        match self {
            Provider::Claude => "claude",
            Provider::Codex => "codex",
            Provider::Cursor => "cursor",
            Provider::Opencode => "opencode",
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum IntegrationAction {
    Check,
    Install,
    Uninstall,
    Rollback,
}

impl IntegrationAction {
    fn as_str(self) -> &'static str {
        match self {
            IntegrationAction::Check => "check",
            IntegrationAction::Install => "install",
            IntegrationAction::Uninstall => "uninstall",
            IntegrationAction::Rollback => "rollback",
        }
    }
}

#[derive(Debug, Clone, Copy, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ServiceAction {
    Status,
    Install,
    Uninstall,
    Start,
    Stop,
    Restart,
    Migrate,
    Rollback,
}

impl ServiceAction {
    fn as_str(self) -> &'static str {
        match self {
            ServiceAction::Status => "status",
            ServiceAction::Install => "install",
            ServiceAction::Uninstall => "uninstall",
            ServiceAction::Start => "start",
            ServiceAction::Stop => "stop",
            ServiceAction::Restart => "restart",
            ServiceAction::Migrate => "migrate",
            ServiceAction::Rollback => "rollback",
        }
    }
}

fn launch_agents() -> PathBuf {
    home().join("Library").join("LaunchAgents")
}

/// O LaunchAgent do ombrod (Fase 4) existe: quem inicia o ombrod é o launchd.
pub fn service_installed() -> bool {
    launch_agents().join(format!("{DAEMON_LABEL}.plist")).is_file()
}

/// O LaunchAgent de antes da Fase 4 (`tela.py --usb`) existe: precisa de migração para não disputar a tela.
pub fn legacy_installed() -> bool {
    launch_agents().join(format!("{LEGACY_LABEL}.plist")).is_file()
}

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(rename_all = "snake_case", tag = "state")]
pub enum DaemonState {
    /// Há um ombrod respondendo. `owner`: quem o mantém.
    Connected { owner: Owner, pid: Option<u64> },
    /// O LaunchAgent está instalado mas o ombrod não responde (iniciando, parado com `service stop`, ou caindo).
    WaitingForService,
    /// Responde, mas com outra versão do protocolo: o app não conversa e não inicia outro por cima.
    Incompatible { reason: String },
    /// O app tentou iniciar um ombrod e não deu: `reason` diz por quê.
    Failed { reason: String },
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Owner {
    /// O app iniciou (e encerra ao sair).
    App,
    /// O LaunchAgent: continua depois que o app fecha.
    Launchd,
    /// Outro: um terminal (python3 ombrod.py), em desenvolvimento.
    External,
}

/// O ombrod que o app iniciou, se houver. Só esse recebe sinal do app.
#[derive(Default)]
pub struct DaemonManager {
    child: Mutex<Option<Child>>,
    /// PID de um ombrod iniciado por uma execução anterior do app que não fechou direito (e que segue vivo).
    adopted: Mutex<Option<u64>>,
}

fn log_file() -> Option<std::fs::File> {
    let dir = home().join("Library").join("Logs").join("ombro-screen");
    std::fs::create_dir_all(&dir).ok()?;
    OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .open(dir.join("ombrod.log"))
        .ok()
}

fn ping() -> Result<Value> {
    Client::default().call(Method::Ping, json!({}))
}

fn status_pid() -> Option<u64> {
    Client::default()
        .call(Method::Status, json!({}))
        .ok()?
        .get("pid")?
        .as_u64()
}

impl DaemonManager {
    pub fn owned_pid(&self) -> Option<u64> {
        let child = self.child.lock().unwrap().as_ref().map(|c| c.id() as u64);
        child.or(*self.adopted.lock().unwrap())
    }

    /// Lembra um ombrod de uma execução anterior do app (o PID vem das preferências do app), se for ele que responde.
    pub fn adopt(&self, pid: u64) {
        if !service_installed() && status_pid() == Some(pid) {
            *self.adopted.lock().unwrap() = Some(pid);
        }
    }

    fn owner_of(&self, pid: Option<u64>) -> Owner {
        if pid.is_some() && pid == self.owned_pid() {
            Owner::App
        } else if service_installed() {
            Owner::Launchd
        } else {
            Owner::External
        }
    }

    /// O estado agora, sem iniciar nada.
    pub fn state(&self) -> DaemonState {
        match ping() {
            Ok(_) => {
                let pid = status_pid();
                DaemonState::Connected {
                    owner: self.owner_of(pid),
                    pid,
                }
            }
            Err(e) if e.kind == ErrorKind::ProtocolMismatch => DaemonState::Incompatible { reason: e.message },
            Err(_) if service_installed() => DaemonState::WaitingForService,
            Err(_) => DaemonState::Failed {
                reason: "o ombrod não está rodando".into(),
            },
        }
    }

    /// Garante um ombrod: conversa com o que existe, espera o do launchd ou inicia um filho.
    pub fn ensure(&self, backend: &Backend) -> DaemonState {
        let now = self.state();
        if !matches!(now, DaemonState::Failed { .. }) {
            return now;
        }
        {
            let mut slot = self.child.lock().unwrap();
            if let Some(c) = slot.as_mut()
                && matches!(c.try_wait(), Ok(None))
            {
                drop(slot);
                return self.wait_ready(Duration::from_secs(10));
            }
            let mut cmd = backend.command(&backend.root.join("ombrod.py"));
            cmd.stdin(Stdio::null()).process_group(0); // Ctrl+C no terminal do `tauri dev` não chega a ele
            match log_file() {
                Some(f) => {
                    let f2 = f.try_clone().ok();
                    cmd.stdout(f);
                    cmd.stderr(f2.map(Stdio::from).unwrap_or_else(Stdio::null));
                }
                None => {
                    cmd.stdout(Stdio::null()).stderr(Stdio::null());
                }
            }
            match cmd.spawn() {
                Ok(c) => *slot = Some(c),
                Err(_) => {
                    return DaemonState::Failed {
                        reason: "não consegui iniciar o ombrod".into(),
                    };
                }
            }
        }
        self.wait_ready(Duration::from_secs(10))
    }

    fn wait_ready(&self, limit: Duration) -> DaemonState {
        let start = Instant::now();
        while start.elapsed() < limit {
            if ping().is_ok() {
                return self.state();
            }
            if let Some(c) = self.child.lock().unwrap().as_mut()
                && let Ok(Some(status)) = c.try_wait()
            {
                let reason = match status.code() {
                    Some(75) => "já existe um Ombro rodando (outro ombrod ou um tela.py com a tela)".to_string(),
                    Some(code) => format!("o ombrod fechou logo ao iniciar (saída {code}); veja o Diagnóstico"),
                    None => "o ombrod foi encerrado ao iniciar".to_string(),
                };
                return DaemonState::Failed { reason };
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        DaemonState::Failed {
            reason: "o ombrod não respondeu a tempo".into(),
        }
    }

    /// Encerra o ombrod que o app iniciou (SIGTERM e até 6 s para fechar a tela e o banco). Os outros ficam.
    pub fn stop_owned(&self) {
        if let Some(mut c) = self.child.lock().unwrap().take() {
            if matches!(c.try_wait(), Ok(None)) {
                // SAFETY: o PID é do nosso filho, ainda não colhido (try_wait acabou de dizer que segue vivo).
                unsafe { libc::kill(c.id() as libc::pid_t, libc::SIGTERM) };
                let start = Instant::now();
                while start.elapsed() < Duration::from_secs(6) {
                    if !matches!(c.try_wait(), Ok(None)) {
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
                let _ = c.kill();
                let _ = c.wait();
            }
            return;
        }
        if let Some(pid) = self.adopted.lock().unwrap().take() {
            // Só se ainda for o mesmo ombrod (o PID que ele mesmo informa pelo socket) e não do launchd.
            if !service_installed() && status_pid() == Some(pid) {
                // SAFETY: o processo confirmou o próprio PID pela IPC instantes antes.
                unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) };
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn enums_viram_os_argumentos_da_cli() {
        let p: Provider = serde_json::from_str("\"codex\"").unwrap();
        assert_eq!(p.as_str(), "codex");
        assert!(serde_json::from_str::<Provider>("\"rm -rf\"").is_err());
        let a: IntegrationAction = serde_json::from_str("\"install\"").unwrap();
        assert_eq!(a.as_str(), "install");
        assert!(serde_json::from_str::<ServiceAction>("\"exec\"").is_err());
    }

    #[test]
    fn em_desenvolvimento_acha_o_repositorio() {
        let b = Backend::locate(None).expect("o repositório tem ombrod.py");
        assert_eq!(b.mode, Mode::Development);
        assert!(b.root.join("ombro").join("__main__.py").is_file());
        assert_eq!(b.location_problem(), None);
    }
}

/// Contra um ombrod de verdade (ignorado por padrão):
///   OMBRO_SCREEN_DIR=<pasta curta> python3 ombrod.py --demo &   e depois   cargo test -- --ignored
#[cfg(test)]
mod real {
    use super::*;
    use crate::ipc::{Client, ErrorKind, Method};
    use serde_json::json;

    #[test]
    #[ignore]
    fn contra_o_ombrod_de_verdade() {
        assert!(
            std::env::var_os("OMBRO_SCREEN_DIR").is_some(),
            "rode com OMBRO_SCREEN_DIR de um ombrod --demo"
        );
        let c = Client::default();
        let st = c.call(Method::Status, json!({})).unwrap();
        assert_eq!(st["protocol"], 1);
        assert!(st["demo"].as_bool().unwrap(), "só contra um ombrod --demo");
        let provs = c.call(Method::ProvidersList, json!({})).unwrap();
        let claude = &provs["providers"][0];
        assert_eq!(claude["id"], "claude");
        assert!(
            claude["quotas"]["windows"].is_array(),
            "o campo novo da v1 chega ao app"
        );
        assert!(provs["providers"][1]["quotas"].is_null());
        assert!(c.call(Method::SessionsList, json!({})).unwrap()["sessions"].is_array());
        assert!(c.call(Method::Health, json!({})).unwrap()["components"].is_object());
        assert!(
            c.call(Method::DisplayStatus, json!({})).unwrap()["frames"]
                .as_u64()
                .unwrap()
                > 0
        );
        let pv = c.call(Method::DisplayPreview, json!({"format": "png"})).unwrap();
        assert_eq!((pv["width"].as_u64(), pv["height"].as_u64()), (Some(480), Some(320)));
        assert!(pv["data_base64"].as_str().unwrap().len() > 1000);
        assert!(c.call(Method::ConfigSchema, json!({})).unwrap()["options"].is_array());
        assert!(c.call(Method::ConfigGet, json!({})).unwrap()["values"].is_object());
        let r = c
            .call(
                Method::DisplaySetLayout,
                json!({"layout": "minimal-v2", "persist": false}),
            )
            .unwrap();
        assert_eq!(r["applied"][0], "layout");
        c.call(Method::DisplaySetLayout, json!({"layout": "auto", "persist": false}))
            .unwrap();
        let e = c.call(Method::DisplaySetBrightness, json!({"value": 300})).unwrap_err();
        assert_eq!(
            (e.kind, e.code.as_deref()),
            (ErrorKind::Rejected, Some("VALIDATION_ERROR"))
        );

        // a CLI pelo Backend (só leitura e dry-run: nada é gravado)
        let b = Backend::locate(None).unwrap();
        let l = b.integrations_list().unwrap();
        assert_eq!(l["result"]["integrations"].as_array().unwrap().len(), 4);
        let d = b.doctor().unwrap();
        assert_eq!(d["result"]["schema"], "ombro.doctor");
        assert!(b.doctor_report().unwrap().contains("Ombro"));
        let s = b.service(ServiceAction::Status, false).unwrap();
        assert_eq!(s["result"]["label"], "com.ombro-screen.daemon");
        let dry = b.integration(Provider::Codex, IntegrationAction::Install, true);
        assert!(dry.is_ok(), "dry-run devolve JSON mesmo quando recusa: {dry:?}");
    }
}
