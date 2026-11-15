//! Preferências que são só do app (nada do que o backend já guarda): onboarding concluído, notificações e o
//! PID do ombrod que o app iniciou. As preferências do Ombro (tela, layout, agentes...) ficam no config.json,
//! pelo ombrod: não há cópia delas aqui.

use serde::{Deserialize, Serialize};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct AppPrefs {
    #[serde(default)]
    pub onboarding_completed: bool,
    /// Notificações locais (sessão esperando, aprovação, erro, serviço fora). Desligadas até o usuário pedir.
    #[serde(default)]
    pub notifications: bool,
    /// O ombrod iniciado pelo app na última execução, para reconhecê-lo se o app fechou sem encerrá-lo.
    #[serde(default)]
    pub owned_daemon_pid: Option<u64>,
}

/// O que o frontend pode mudar (o PID não).
#[derive(Debug, Clone, Deserialize)]
pub struct PrefsPatch {
    pub onboarding_completed: Option<bool>,
    pub notifications: Option<bool>,
}

pub struct PrefsStore {
    path: PathBuf,
}

impl PrefsStore {
    pub fn new(dir: &Path) -> Self {
        PrefsStore {
            path: dir.join("app-prefs.json"),
        }
    }

    pub fn load(&self) -> AppPrefs {
        std::fs::read(&self.path)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default()
    }

    /// Grava de uma vez (arquivo temporário + rename), 0600.
    pub fn save(&self, prefs: &AppPrefs) -> std::io::Result<()> {
        if let Some(dir) = self.path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let tmp = self.path.with_extension("json.tmp");
        {
            let mut f = std::fs::OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .mode(0o600)
                .open(&tmp)?;
            f.write_all(&serde_json::to_vec_pretty(prefs).unwrap_or_default())?;
            f.sync_all()?;
        }
        std::fs::rename(&tmp, &self.path)
    }

    pub fn update(&self, f: impl FnOnce(&mut AppPrefs)) -> std::io::Result<AppPrefs> {
        let mut p = self.load();
        f(&mut p);
        self.save(&p)?;
        Ok(p)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn padrao_grava_e_le() {
        let dir = tempfile::tempdir().unwrap();
        let s = PrefsStore::new(dir.path());
        assert_eq!(s.load(), AppPrefs::default());
        assert!(!s.load().notifications, "notificações começam desligadas");
        s.update(|p| p.onboarding_completed = true).unwrap();
        assert!(s.load().onboarding_completed);
        use std::os::unix::fs::PermissionsExt;
        let modo = std::fs::metadata(dir.path().join("app-prefs.json"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777;
        assert_eq!(modo, 0o600);
    }

    #[test]
    fn arquivo_estragado_volta_ao_padrao() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("app-prefs.json"), b"{nao e json").unwrap();
        assert_eq!(PrefsStore::new(dir.path()).load(), AppPrefs::default());
    }
}
