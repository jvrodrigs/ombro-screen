//! O único laço de atualização do app: consulta o ombrod, guarda o último retrato, avisa a janela (evento
//! `ombro://snapshot`), atualiza a Menu Bar e decide as notificações. A interface não faz polling próprio
//! destes dados; só a prévia da tela e a configuração são pedidas sob demanda, pela página que as mostra.

use crate::backend::{DaemonManager, DaemonState, Owner, service_installed};
use crate::ipc::{AppError, Client, ErrorKind, Method, Result};
use serde::Serialize;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Condvar, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const VISIBLE_INTERVAL: Duration = Duration::from_secs(3);
pub const HIDDEN_INTERVAL: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, Serialize, Default)]
pub struct Snapshot {
    pub seq: u64,
    pub at_ms: u64,
    pub daemon: Option<DaemonState>,
    pub status: Option<Value>,
    pub providers: Option<Value>,
    pub sessions: Option<Value>,
    pub health: Option<Value>,
    pub error: Option<AppError>,
}

pub struct Monitor {
    pub latest: Mutex<Snapshot>,
    wake: (Mutex<bool>, Condvar),
    pub window_visible: AtomicBool,
}

impl Default for Monitor {
    fn default() -> Self {
        Monitor {
            latest: Mutex::new(Snapshot::default()),
            wake: (Mutex::new(false), Condvar::new()),
            window_visible: AtomicBool::new(true),
        }
    }
}

impl Monitor {
    /// Pede uma atualização já (depois de uma ação do usuário), sem esperar o intervalo.
    pub fn poke(&self) {
        let (m, c) = &self.wake;
        *m.lock().unwrap() = true;
        c.notify_all();
    }

    pub fn sleep(&self) {
        let limite = if self.window_visible.load(Ordering::Relaxed) {
            VISIBLE_INTERVAL
        } else {
            HIDDEN_INTERVAL
        };
        let (m, c) = &self.wake;
        let guard = m.lock().unwrap();
        let (mut g, _) = c.wait_timeout_while(guard, limite, |acordar| !*acordar).unwrap();
        *g = false;
    }

    pub fn collect(&self, daemon: &DaemonManager) -> Snapshot {
        let client = Client::default();
        let status = client.call(Method::Status, json!({}));
        let mut snap = Snapshot {
            seq: self.latest.lock().unwrap().seq + 1,
            at_ms: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_millis() as u64)
                .unwrap_or(0),
            daemon: Some(classify(daemon, &status)),
            ..Default::default()
        };
        match status {
            Ok(st) => {
                snap.status = Some(st);
                // Um provider (ou método) que falha não derruba o resto do retrato.
                snap.providers = client.call(Method::ProvidersList, json!({})).ok();
                snap.sessions = client.call(Method::SessionsList, json!({})).ok();
                snap.health = client.call(Method::Health, json!({})).ok();
            }
            Err(e) => snap.error = Some(e),
        }
        *self.latest.lock().unwrap() = snap.clone();
        snap
    }
}

fn classify(daemon: &DaemonManager, status: &Result<Value>) -> DaemonState {
    match status {
        Ok(st) => {
            let pid = st.get("pid").and_then(Value::as_u64);
            let owner = if pid.is_some() && pid == daemon.owned_pid() {
                Owner::App
            } else if service_installed() {
                Owner::Launchd
            } else {
                Owner::External
            };
            DaemonState::Connected { owner, pid }
        }
        Err(e) if e.kind == ErrorKind::ProtocolMismatch => DaemonState::Incompatible {
            reason: e.message.clone(),
        },
        Err(_) if service_installed() => DaemonState::WaitingForService,
        Err(e) => DaemonState::Failed {
            reason: e.message.clone(),
        },
    }
}

/// Uma notificação a mostrar. Nunca leva nome de projeto, id de sessão, prompt ou caminho.
#[derive(Debug, Clone, PartialEq)]
pub struct Notice {
    pub key: String,
    pub title: String,
    pub body: String,
}

/// Decide o que notificar, com deduplicação (só na mudança de estado) e intervalo mínimo por tipo e geral.
#[derive(Default)]
pub struct Notifier {
    last: HashMap<(String, String), String>,
    sent: HashMap<String, Instant>,
    last_any: Option<Instant>,
    primed: bool,
    down_since: Option<Instant>,
    down_notified: bool,
    was_connected: bool,
}

const COOLDOWN_KIND: Duration = Duration::from_secs(120);
const COOLDOWN_ANY: Duration = Duration::from_secs(10);
const DOWN_GRACE: Duration = Duration::from_secs(30);

fn provider_names(snap: &Snapshot) -> HashMap<String, String> {
    let mut nomes = HashMap::new();
    if let Some(list) = snap
        .providers
        .as_ref()
        .and_then(|p| p.get("providers"))
        .and_then(Value::as_array)
    {
        for p in list {
            if let (Some(id), Some(name)) = (
                p.get("id").and_then(Value::as_str),
                p.get("name").and_then(Value::as_str),
            ) {
                nomes.insert(id.to_string(), name.to_string());
            }
        }
    }
    nomes
}

impl Notifier {
    pub fn process(&mut self, snap: &Snapshot, now: Instant) -> Vec<Notice> {
        let mut candidatos = Vec::new();
        let conectado = snap.status.is_some();

        // Serviço fora: só se estava conectado antes e ficou fora por um tempo (não a cada piscada).
        if conectado {
            self.down_since = None;
            self.down_notified = false;
            self.was_connected = true;
        } else if self.was_connected {
            let desde = *self.down_since.get_or_insert(now);
            if !self.down_notified && now.duration_since(desde) >= DOWN_GRACE {
                self.down_notified = true;
                candidatos.push(Notice {
                    key: "service-down".into(),
                    title: "O Ombro parou de monitorar".into(),
                    body: "O serviço local não está respondendo. Abra o Ombro para ver o diagnóstico.".into(),
                });
            }
        }

        if let Some(list) = snap
            .sessions
            .as_ref()
            .and_then(|s| s.get("sessions"))
            .and_then(Value::as_array)
        {
            let nomes = provider_names(snap);
            let mut atuais = HashMap::new();
            for s in list {
                let prov = s.get("provider").and_then(Value::as_str).unwrap_or("").to_string();
                let id = s.get("session_id").and_then(Value::as_str).unwrap_or("").to_string();
                let st = s.get("status").and_then(Value::as_str).unwrap_or("unknown").to_string();
                let antes = self.last.get(&(prov.clone(), id.clone()));
                // uma espera que veio de uma releitura (não se viu o pedido acontecer) aparece, mas não notifica
                let recuperada = s.get("recovered").and_then(Value::as_bool) == Some(true);
                // nem o que é só o último estado de uma fonte fora do ar
                let historica = recuperada || s.get("disconnected").and_then(Value::as_bool) == Some(true);
                if self.primed && !historica && antes != Some(&st) {
                    let nome = nomes.get(&prov).cloned().unwrap_or_else(|| prov.clone());
                    let n = match st.as_str() {
                        "waiting" => Some((
                            format!("{prov}:waiting"),
                            format!("{nome} está esperando você"),
                            "Uma sessão aguarda a sua resposta.",
                        )),
                        "approval" => Some((
                            format!("{prov}:approval"),
                            format!("{nome} pede aprovação"),
                            "Uma sessão aguarda a sua permissão para continuar.",
                        )),
                        "error" => Some((
                            format!("{prov}:error"),
                            format!("{nome}: uma sessão parou com erro"),
                            "Abra o agente para ver o que aconteceu.",
                        )),
                        _ => None,
                    };
                    if let Some((key, title, body)) = n {
                        candidatos.push(Notice {
                            key,
                            title,
                            body: body.into(),
                        });
                    }
                }
                atuais.insert((prov, id), st);
            }
            self.last = atuais;
            self.primed = true; // o primeiro retrato só registra o estado: abrir o app não dispara uma rajada
        }

        let mut saida = Vec::new();
        for n in candidatos {
            if self.last_any.is_some_and(|t| now.duration_since(t) < COOLDOWN_ANY) {
                continue;
            }
            if self
                .sent
                .get(&n.key)
                .is_some_and(|t| now.duration_since(*t) < COOLDOWN_KIND)
            {
                continue;
            }
            self.sent.insert(n.key.clone(), now);
            self.last_any = Some(now);
            saida.push(n);
        }
        saida
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn snap(sessoes: Value) -> Snapshot {
        Snapshot {
            status: Some(json!({"daemon": "running"})),
            providers: Some(
                json!({"providers": [{"id": "claude", "name": "Claude Code"}, {"id": "codex", "name": "Codex"}]}),
            ),
            sessions: Some(json!({"sessions": sessoes})),
            ..Default::default()
        }
    }

    #[test]
    fn primeiro_retrato_nao_notifica_e_mudanca_notifica_uma_vez() {
        let mut n = Notifier::default();
        let t0 = Instant::now();
        let esperando = json!([{"provider": "claude", "session_id": "a", "status": "waiting", "project": "segredo"}]);
        assert!(
            n.process(&snap(esperando.clone()), t0).is_empty(),
            "abrir o app não dispara notificações"
        );
        assert!(
            n.process(
                &snap(json!([{"provider": "claude", "session_id": "a", "status": "working"}])),
                t0
            )
            .is_empty()
        );
        let r = n.process(&snap(esperando.clone()), t0 + Duration::from_secs(20));
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].title, "Claude Code está esperando você");
        assert!(
            !r[0].body.contains("segredo") && !r[0].title.contains("segredo"),
            "sem nome de projeto"
        );
        // o mesmo estado de novo: nada
        assert!(n.process(&snap(esperando), t0 + Duration::from_secs(40)).is_empty());
    }

    #[test]
    fn recuperacao_e_desconhecido_nao_notificam() {
        let mut n = Notifier::default();
        let t0 = Instant::now();
        let s = |st: &str, rec: bool| json!([{"provider": "opencode", "session_id": "o:s", "status": st, "recovered": rec}]);
        n.process(&snap(s("working", false)), t0);
        assert!(
            n.process(&snap(s("unknown", false)), t0 + Duration::from_secs(20))
                .is_empty()
        );
        assert!(
            n.process(&snap(s("approval", true)), t0 + Duration::from_secs(40))
                .is_empty(),
            "veio de uma releitura"
        );
        assert!(
            n.process(&snap(s("idle", false)), t0 + Duration::from_secs(60))
                .is_empty()
        );
        assert_eq!(
            n.process(&snap(s("approval", false)), t0 + Duration::from_secs(80))
                .len(),
            1,
            "um pedido visto acontecer notifica"
        );
    }

    #[test]
    fn fonte_fora_do_ar_nao_notifica_nem_ao_voltar() {
        let mut n = Notifier::default();
        let t0 = Instant::now();
        let s = |st: &str, fora: bool| json!([{"provider": "opencode", "session_id": "o:s", "status": st, "disconnected": fora}]);
        n.process(&snap(s("working", false)), t0);
        assert!(
            n.process(&snap(s("error", true)), t0 + Duration::from_secs(20))
                .is_empty(),
            "um erro que só se conhece de antes não notifica"
        );
        assert!(
            n.process(&snap(s("error", false)), t0 + Duration::from_secs(40))
                .is_empty(),
            "voltar a fonte não repete o mesmo erro"
        );
    }

    #[test]
    fn intervalo_minimo_por_tipo() {
        let mut n = Notifier::default();
        let t0 = Instant::now();
        let w = |st: &str| json!([{"provider": "codex", "session_id": "x", "status": st}]);
        n.process(&snap(w("working")), t0);
        assert_eq!(n.process(&snap(w("approval")), t0 + Duration::from_secs(15)).len(), 1);
        n.process(&snap(w("working")), t0 + Duration::from_secs(30));
        assert!(
            n.process(&snap(w("approval")), t0 + Duration::from_secs(45)).is_empty(),
            "menos de 2 min depois"
        );
        n.process(&snap(w("working")), t0 + Duration::from_secs(150));
        assert_eq!(n.process(&snap(w("approval")), t0 + Duration::from_secs(200)).len(), 1);
    }

    #[test]
    fn servico_fora_so_depois_da_carencia_e_uma_vez() {
        let mut n = Notifier::default();
        let t0 = Instant::now();
        n.process(&snap(json!([])), t0);
        let fora = Snapshot::default();
        assert!(n.process(&fora, t0 + Duration::from_secs(5)).is_empty());
        let r = n.process(&fora, t0 + Duration::from_secs(40));
        assert_eq!(r.len(), 1);
        assert_eq!(r[0].key, "service-down");
        assert!(n.process(&fora, t0 + Duration::from_secs(400)).is_empty());
    }

    #[test]
    fn sem_nunca_ter_conectado_nao_avisa_queda() {
        let mut n = Notifier::default();
        let t0 = Instant::now();
        assert!(n.process(&Snapshot::default(), t0).is_empty());
        assert!(
            n.process(&Snapshot::default(), t0 + Duration::from_secs(120))
                .is_empty()
        );
    }
}
