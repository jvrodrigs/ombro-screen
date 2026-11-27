//! A Menu Bar: o cloud monocromático (imagem template), um resumo e os atalhos. O texto vem do último retrato
//! do monitor; o ícone ganha um número ao lado só quando alguma sessão espera você.

use crate::backend::{DaemonState, Owner};
use crate::monitor::Snapshot;
use serde_json::Value;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Wry};

pub struct Tray {
    pub icon: TrayIcon<Wry>,
    header: MenuItem<Wry>,
    providers: [MenuItem<Wry>; AGENTES.len()],
    display: MenuItem<Wry>,
    quit: MenuItem<Wry>,
}

/// Os agentes do menu, na ordem da tela: (id no providers.list, nome enquanto o ombrod não responde).
const AGENTES: [(&str, &str); 4] = [
    ("claude", "Claude Code"),
    ("codex", "Codex"),
    ("cursor", "Cursor"),
    ("opencode", "OpenCode"),
];

pub const OPEN: &str = "open";
pub const SETTINGS: &str = "settings";
pub const QUIT: &str = "quit";

pub fn build(app: &AppHandle) -> tauri::Result<Tray> {
    let header = MenuItem::with_id(app, "h", "Ombro: conectando…", false, None::<&str>)?;
    let providers = [
        MenuItem::with_id(app, "p0", "Claude Code", false, None::<&str>)?,
        MenuItem::with_id(app, "p1", "Codex", false, None::<&str>)?,
        MenuItem::with_id(app, "p2", "Cursor", false, None::<&str>)?,
        MenuItem::with_id(app, "p3", "OpenCode", false, None::<&str>)?,
    ];
    let display = MenuItem::with_id(app, "d", "Tela: —", false, None::<&str>)?;
    let open = MenuItem::with_id(app, OPEN, "Abrir o Ombro", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, SETTINGS, "Ajustes…", true, Some("CmdOrCtrl+,"))?;
    let quit = MenuItem::with_id(app, QUIT, "Sair do Ombro", true, None::<&str>)?;
    let sep = || PredefinedMenuItem::separator(app);
    let menu = Menu::with_items(
        app,
        &[
            &header,
            &sep()?,
            &providers[0],
            &providers[1],
            &providers[2],
            &providers[3],
            &display,
            &sep()?,
            &open,
            &settings,
            &sep()?,
            &quit,
        ],
    )?;
    let image = tauri::image::Image::from_bytes(include_bytes!("../icons/tray/tray.png"))?;
    let icon = TrayIconBuilder::with_id("ombro")
        .icon(image)
        .icon_as_template(true)
        .tooltip("Ombro")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .build(app)?;
    Ok(Tray {
        icon,
        header,
        providers,
        display,
        quit,
    })
}

/// Os textos do menu para um retrato (separado do Tauri para dar para testar).
pub struct Texts {
    pub header: String,
    pub providers: [String; AGENTES.len()],
    pub display: String,
    pub quit: String,
    pub badge: Option<String>,
}

fn n(v: Option<&Value>) -> u64 {
    v.and_then(Value::as_u64).unwrap_or(0)
}

/// O que a integração diz de si (hoje, o OpenCode) quando isso vale mais que a contagem de sessões.
fn aviso_da_condicao(p: &Value) -> Option<&'static str> {
    let ativas = n(p.get("working")) + n(p.get("waiting"));
    if p.get("state").and_then(Value::as_str) == Some("desconectado") {
        return Some("desconectado");
    }
    match p.get("condition").and_then(Value::as_str)? {
        "incompatible" if ativas == 0 => Some("versão sem suporte"),
        "conflict" if ativas == 0 => Some("origem em conflito"),
        "unverified" if ativas == 0 => Some("não comprovado"),
        "never" if n(p.get("sessions")) == 0 => Some("nunca conectou"),
        "unregistered" if n(p.get("sessions")) == 0 => Some("origem sem registro"),
        "collecting" if n(p.get("sessions")) == 0 => Some("lendo sessões"),
        _ => None,
    }
}

pub fn texts(snap: &Snapshot) -> Texts {
    let header = match (&snap.daemon, snap.status.as_ref()) {
        (Some(DaemonState::Connected { .. }), Some(st)) => match st.get("daemon").and_then(Value::as_str) {
            Some("running") => "Ombro: monitorando".to_string(),
            Some("degraded") => "Ombro: monitorando, com avisos".to_string(),
            Some("starting") => "Ombro: iniciando…".to_string(),
            Some(_) => "Ombro: encerrando…".to_string(),
            None => "Ombro".to_string(),
        },
        (Some(DaemonState::WaitingForService), _) => "Ombro: esperando o serviço".to_string(),
        (Some(DaemonState::Incompatible { .. }), _) => "Ombro: serviço de outra versão".to_string(),
        (None, _) => "Ombro: conectando…".to_string(),
        _ => "Ombro: serviço parado".to_string(),
    };

    let mut providers = AGENTES.map(|(_, nome)| nome.to_string());
    if let Some(list) = snap
        .providers
        .as_ref()
        .and_then(|p| p.get("providers"))
        .and_then(Value::as_array)
    {
        for (i, (id, _)) in AGENTES.iter().enumerate() {
            let Some(p) = list.iter().find(|p| p.get("id").and_then(Value::as_str) == Some(*id)) else {
                continue;
            };
            let nome = p
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or(&providers[i])
                .to_string();
            let detalhe = if p.get("enabled").and_then(Value::as_bool) == Some(false) {
                "desligado".to_string()
            } else if p.get("installed").and_then(Value::as_bool) == Some(false) {
                "não instalado".to_string()
            } else if let Some(aviso) = aviso_da_condicao(p) {
                aviso.to_string()
            } else {
                let (w, a, s) = (n(p.get("working")), n(p.get("waiting")), n(p.get("sessions")));
                let mut partes = Vec::new();
                if w > 0 {
                    partes.push(format!("{w} trabalhando"));
                }
                if a > 0 {
                    partes.push(format!("{a} esperando"));
                }
                if partes.is_empty() {
                    if s > 0 {
                        format!("{s} {}", if s == 1 { "sessão" } else { "sessões" })
                    } else {
                        "sem sessões".to_string()
                    }
                } else {
                    partes.join(" · ")
                }
            };
            providers[i] = format!("{nome} — {detalhe}");
        }
    }

    let display = match snap.status.as_ref().and_then(|s| s.get("display")) {
        Some(d) => match (
            d.get("kind").and_then(Value::as_str),
            d.get("connection").and_then(Value::as_str),
        ) {
            (Some("usb"), Some("connected")) => "Tela USB: conectada",
            (Some("usb"), Some("conflict")) => "Tela USB: em uso por outro Ombro",
            (Some("usb"), Some("error")) => "Tela USB: com erro",
            (Some("usb"), _) => "Tela USB: desconectada",
            (Some("simulator"), _) => "Tela: só a prévia (sem USB)",
            _ => "Tela: —",
        }
        .to_string(),
        None => "Tela: —".to_string(),
    };

    let quit = match &snap.daemon {
        Some(DaemonState::Connected { owner: Owner::App, .. }) => "Sair do Ombro (o monitoramento para)",
        Some(DaemonState::Connected { .. }) | Some(DaemonState::WaitingForService) => {
            "Sair do Ombro (o serviço continua)"
        }
        _ => "Sair do Ombro",
    }
    .to_string();

    let esperando = n(snap.status.as_ref().and_then(|s| s.get("waiting")));
    let badge = (esperando > 0).then(|| esperando.to_string());
    Texts {
        header,
        providers,
        display,
        quit,
        badge,
    }
}

impl Tray {
    pub fn update(&self, snap: &Snapshot) {
        let t = texts(snap);
        let _ = self.header.set_text(&t.header);
        for (item, texto) in self.providers.iter().zip(t.providers.iter()) {
            let _ = item.set_text(texto);
        }
        let _ = self.display.set_text(&t.display);
        let _ = self.quit.set_text(&t.quit);
        let _ = self.icon.set_title(t.badge.as_deref());
        let _ = self.icon.set_tooltip(Some(&t.header));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn textos_do_menu() {
        let snap = Snapshot {
            daemon: Some(DaemonState::Connected {
                owner: Owner::App,
                pid: Some(1),
            }),
            status: Some(
                json!({"daemon": "running", "waiting": 1, "display": {"kind": "simulator", "connection": "connected"}}),
            ),
            providers: Some(json!({"providers": [
                {"id": "claude", "name": "Claude Code", "enabled": true, "installed": true, "working": 2, "waiting": 1, "sessions": 3},
                {"id": "codex", "name": "Codex", "enabled": true, "installed": false},
                {"id": "cursor", "enabled": false},
                {"id": "opencode", "name": "OpenCode", "enabled": true, "installed": true, "condition": "incompatible"}
            ]})),
            ..Default::default()
        };
        let t = texts(&snap);
        assert_eq!(t.header, "Ombro: monitorando");
        assert_eq!(t.providers[0], "Claude Code — 2 trabalhando · 1 esperando");
        assert_eq!(t.providers[1], "Codex — não instalado");
        assert_eq!(t.providers[2], "Cursor — desligado");
        assert_eq!(t.providers[3], "OpenCode — versão sem suporte");
        assert_eq!(t.display, "Tela: só a prévia (sem USB)");
        assert_eq!(t.quit, "Sair do Ombro (o monitoramento para)");
        assert_eq!(t.badge.as_deref(), Some("1"));
    }

    #[test]
    fn opencode_desconectado() {
        let linha = |p: Value| {
            let snap = Snapshot {
                providers: Some(json!({"providers": [p]})),
                ..Default::default()
            };
            texts(&snap).providers[3].clone()
        };
        let base = |estado: &str, condicao: Value, sessoes: u64| {
            json!({"id": "opencode", "name": "OpenCode", "enabled": true, "installed": true, "state": estado,
                   "condition": condicao, "sessions": sessoes, "disconnected_sessions": 2})
        };
        assert_eq!(
            linha(base("desconectado", json!("offline"), 0)),
            "OpenCode — desconectado"
        );
        assert_eq!(
            linha(base("desconectado", json!("unregistered"), 0)),
            "OpenCode — desconectado"
        );
        // uma origem fora do ar e outra no ar: o menu fala das sessões de agora
        assert_eq!(linha(base("ocioso", Value::Null, 1)), "OpenCode — 1 sessão");
    }

    #[test]
    fn sem_daemon() {
        let t = texts(&Snapshot {
            daemon: Some(DaemonState::Failed { reason: "x".into() }),
            ..Default::default()
        });
        assert_eq!(t.header, "Ombro: serviço parado");
        assert_eq!(t.badge, None);
        assert_eq!(t.quit, "Sair do Ombro");
    }
}
