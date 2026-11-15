//! Cliente da IPC v1 do ombrod (docs/ipc.md): JSON Lines por Unix Domain Socket, só para o usuário atual.
//!
//! O app nunca monta um método a partir de texto vindo do JavaScript: cada chamada usa um `Method` desta
//! allowlist, e os parâmetros são montados pelos comandos Tauri (src/commands.rs) com tipos fixos.

use serde::Serialize;
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

pub const PROTOCOL: u64 = 1;
const MAX_RESPONSE: u64 = 4 * 1024 * 1024; // o mesmo limite do ClienteIPC do Python
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

/// Os únicos métodos que o app chama. Instalar hooks e mexer no serviço não passam pela IPC (ver backend.rs).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    Ping,
    Status,
    Health,
    ProvidersList,
    SessionsList,
    DisplayStatus,
    DisplayPreview,
    ConfigGet,
    ConfigSchema,
    ConfigUpdate,
    DisplaySetBrightness,
    DisplaySetLayout,
}

impl Method {
    pub fn as_str(self) -> &'static str {
        match self {
            Method::Ping => "ping",
            Method::Status => "status",
            Method::Health => "health",
            Method::ProvidersList => "providers.list",
            Method::SessionsList => "sessions.list",
            Method::DisplayStatus => "display.status",
            Method::DisplayPreview => "display.preview",
            Method::ConfigGet => "config.get",
            Method::ConfigSchema => "config.schema",
            Method::ConfigUpdate => "config.update",
            Method::DisplaySetBrightness => "display.set_brightness",
            Method::DisplaySetLayout => "display.set_layout",
        }
    }
}

/// Erro que chega ao frontend: `kind` diz o que a interface deve mostrar; `message` é curta e sem caminhos.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct AppError {
    pub kind: ErrorKind,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<String>,
    pub message: String,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    /// Não há ombrod respondendo no socket.
    DaemonUnavailable,
    /// O ombrod respondeu com outra versão do protocolo.
    ProtocolMismatch,
    /// O ombrod recusou o pedido (VALIDATION_ERROR, SERVICE_UNAVAILABLE...): `code` traz o código.
    Rejected,
    /// Resposta que não segue o protocolo, ou tempo esgotado.
    Ipc,
    /// O backend embarcado (ou o do repositório, em desenvolvimento) não foi encontrado.
    BackendMissing,
    /// A CLI do Ombro falhou sem devolver o JSON esperado.
    Cli,
    /// Operação recusada pelo próprio app (ex.: instalar hooks com o app fora de /Applications).
    NotAllowed,
}

impl AppError {
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        AppError {
            kind,
            code: None,
            message: message.into(),
        }
    }
}

pub type Result<T> = std::result::Result<T, AppError>;

/// `~/.ombro-screen`, ou `OMBRO_SCREEN_DIR` (a mesma regra do config.py).
pub fn ombro_dir() -> PathBuf {
    match std::env::var_os("OMBRO_SCREEN_DIR") {
        Some(v) if !v.is_empty() => expand_home(Path::new(&v)),
        _ => home().join(".ombro-screen"),
    }
}

pub fn socket_path() -> PathBuf {
    ombro_dir().join("run").join("ombro.sock")
}

pub fn home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

fn expand_home(p: &Path) -> PathBuf {
    match p.strip_prefix("~") {
        Ok(rest) => home().join(rest),
        Err(_) => p.to_path_buf(),
    }
}

/// Confere, antes de conectar, que o caminho é um socket do usuário atual (como o ClienteIPC do Python).
fn check_socket(path: &Path) -> Result<()> {
    let meta = match std::fs::symlink_metadata(path) {
        Ok(m) => m,
        Err(_) => return Err(AppError::new(ErrorKind::DaemonUnavailable, "o ombrod não está rodando")),
    };
    if !meta.file_type().is_socket() {
        return Err(AppError::new(
            ErrorKind::Ipc,
            "o caminho do socket não é um socket; nada foi enviado",
        ));
    }
    // SAFETY: getuid não tem pré-condições.
    let uid = unsafe { libc::getuid() };
    if meta.uid() != uid {
        return Err(AppError::new(
            ErrorKind::Ipc,
            "o socket é de outro usuário; nada foi enviado",
        ));
    }
    Ok(())
}

pub struct Client {
    path: PathBuf,
    timeout: Duration,
}

impl Default for Client {
    fn default() -> Self {
        Client {
            path: socket_path(),
            timeout: Duration::from_secs(3),
        }
    }
}

impl Client {
    #[cfg(test)]
    pub fn at(path: PathBuf) -> Self {
        Client {
            path,
            timeout: Duration::from_secs(2),
        }
    }

    /// Uma chamada numa conexão nova (o custo é ~0,2 ms; nada de conexão parada segurando um dos 8 lugares).
    pub fn call(&self, method: Method, params: Value) -> Result<Value> {
        check_socket(&self.path)?;
        let mut stream = UnixStream::connect(&self.path)
            .map_err(|_| AppError::new(ErrorKind::DaemonUnavailable, "o ombrod não está rodando"))?;
        stream.set_read_timeout(Some(self.timeout)).ok();
        stream.set_write_timeout(Some(self.timeout)).ok();
        let request_id = format!("app-{}", NEXT_ID.fetch_add(1, Ordering::Relaxed));
        let mut line = serde_json::to_vec(&json!({
            "version": PROTOCOL, "request_id": request_id, "method": method.as_str(), "params": params,
        }))
        .map_err(|_| AppError::new(ErrorKind::Ipc, "pedido inválido"))?;
        line.push(b'\n');
        stream
            .write_all(&line)
            .map_err(|_| AppError::new(ErrorKind::DaemonUnavailable, "o ombrod fechou a conexão"))?;

        let mut reader = BufReader::new(stream.take(MAX_RESPONSE + 1));
        let mut buf = Vec::new();
        let n = reader.read_until(b'\n', &mut buf).map_err(|e| {
            if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) {
                AppError::new(ErrorKind::Ipc, "o ombrod não respondeu a tempo")
            } else {
                AppError::new(ErrorKind::DaemonUnavailable, "o ombrod fechou a conexão")
            }
        })?;
        if n == 0 {
            return Err(AppError::new(ErrorKind::DaemonUnavailable, "o ombrod fechou a conexão"));
        }
        if buf.len() as u64 > MAX_RESPONSE || !buf.ends_with(b"\n") {
            return Err(AppError::new(ErrorKind::Ipc, "resposta grande demais ou incompleta"));
        }
        parse_response(&buf, &request_id)
    }
}

fn parse_response(buf: &[u8], request_id: &str) -> Result<Value> {
    let resp: Value =
        serde_json::from_slice(buf).map_err(|_| AppError::new(ErrorKind::Ipc, "resposta que não é JSON"))?;
    let version = resp.get("version").and_then(Value::as_u64);
    if version != Some(PROTOCOL) {
        return Err(AppError::new(
            ErrorKind::ProtocolMismatch,
            format!(
                "o ombrod fala o protocolo {}, e este app, o {PROTOCOL}",
                version.map_or("?".into(), |v| v.to_string())
            ),
        ));
    }
    if resp.get("request_id").and_then(Value::as_str) != Some(request_id) {
        return Err(AppError::new(ErrorKind::Ipc, "resposta de outro pedido"));
    }
    match resp.get("ok").and_then(Value::as_bool) {
        Some(true) => Ok(resp.get("result").cloned().unwrap_or(Value::Null)),
        Some(false) => {
            let err = resp.get("error");
            let code = err
                .and_then(|e| e.get("code"))
                .and_then(Value::as_str)
                .unwrap_or("UNKNOWN")
                .to_string();
            let message = err
                .and_then(|e| e.get("message"))
                .and_then(Value::as_str)
                .unwrap_or("erro")
                .to_string();
            Err(AppError {
                kind: ErrorKind::Rejected,
                code: Some(code),
                message,
            })
        }
        None => Err(AppError::new(ErrorKind::Ipc, "resposta sem \"ok\"")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixListener;
    use std::thread;

    /// Um servidor de uma resposta, que devolve o que `responder` monta a partir do pedido.
    fn servidor(responder: impl FnOnce(Value) -> String + Send + 'static) -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("s.sock");
        let listener = UnixListener::bind(&path).unwrap();
        thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            let req: Value = serde_json::from_str(&line).unwrap();
            let mut out = stream;
            out.write_all(responder(req).as_bytes()).unwrap();
        });
        (dir, path)
    }

    #[test]
    fn sucesso_devolve_o_result() {
        let (_d, path) = servidor(|req| {
            assert_eq!(req["version"], 1);
            assert_eq!(req["method"], "status");
            format!(
                "{{\"version\":1,\"request_id\":{},\"ok\":true,\"result\":{{\"daemon\":\"running\"}}}}\n",
                req["request_id"]
            )
        });
        let r = Client::at(path).call(Method::Status, json!({})).unwrap();
        assert_eq!(r["daemon"], "running");
    }

    #[test]
    fn erro_do_ombrod_vira_rejected_com_codigo() {
        let (_d, path) = servidor(|req| {
            format!(
                "{{\"version\":1,\"request_id\":{},\"ok\":false,\"error\":{{\"code\":\"VALIDATION_ERROR\",\"message\":\"brilho deve ser de 0 a 100\"}}}}\n",
                req["request_id"]
            )
        });
        let e = Client::at(path)
            .call(Method::DisplaySetBrightness, json!({"value": 300}))
            .unwrap_err();
        assert_eq!(e.kind, ErrorKind::Rejected);
        assert_eq!(e.code.as_deref(), Some("VALIDATION_ERROR"));
    }

    #[test]
    fn outra_versao_e_recusada() {
        let (_d, path) = servidor(|req| {
            format!(
                "{{\"version\":2,\"request_id\":{},\"ok\":true,\"result\":{{}}}}\n",
                req["request_id"]
            )
        });
        let e = Client::at(path).call(Method::Ping, json!({})).unwrap_err();
        assert_eq!(e.kind, ErrorKind::ProtocolMismatch);
    }

    #[test]
    fn sem_socket_e_daemon_indisponivel() {
        let dir = tempfile::tempdir().unwrap();
        let e = Client::at(dir.path().join("nada.sock"))
            .call(Method::Ping, json!({}))
            .unwrap_err();
        assert_eq!(e.kind, ErrorKind::DaemonUnavailable);
    }

    #[test]
    fn arquivo_comum_no_lugar_do_socket_e_recusado() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("ombro.sock");
        std::fs::write(&path, b"x").unwrap();
        let e = Client::at(path).call(Method::Ping, json!({})).unwrap_err();
        assert_eq!(e.kind, ErrorKind::Ipc);
    }

    #[test]
    fn resposta_de_outro_pedido_e_recusada() {
        let (_d, path) =
            servidor(|_| "{\"version\":1,\"request_id\":\"outro\",\"ok\":true,\"result\":{}}\n".to_string());
        let e = Client::at(path).call(Method::Ping, json!({})).unwrap_err();
        assert_eq!(e.kind, ErrorKind::Ipc);
    }
}
