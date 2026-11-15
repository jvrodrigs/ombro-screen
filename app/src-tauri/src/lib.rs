//! Ombro.app: a janela (React), a Menu Bar e a ponte com o backend Python (o ombrod), que é a fonte da verdade.
//!
//! Ciclo de vida:
//!   - fechar a janela (⌘W ou o botão vermelho) só a esconde: o Ombro segue na Menu Bar, sem ícone no Dock;
//!   - "Sair do Ombro" (Menu Bar ou ⌘Q) fecha o app. O ombrod que o app iniciou fecha junto (a tela para);
//!     um ombrod do LaunchAgent ("monitorar em segundo plano") continua;
//!   - abrir o app de novo (Finder, Dock ou Spotlight) mostra a janela; nunca abre um segundo app.

mod backend;
mod commands;
mod ipc;
mod monitor;
mod prefs;
mod tray;

use backend::{Backend, DaemonManager, DaemonState, Owner};
use ipc::{AppError, Result};
use monitor::{Monitor, Notifier};
use prefs::PrefsStore;
use std::sync::Mutex;
use std::sync::atomic::Ordering;
use std::time::Instant;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};

pub struct Ombro {
    pub backend: std::result::Result<Backend, AppError>,
    pub daemon: DaemonManager,
    pub prefs: PrefsStore,
    pub monitor: Monitor,
    tray: Mutex<Option<tray::Tray>>,
}

impl Ombro {
    /// Garante um ombrod (ver backend.rs) e lembra o PID se foi o app que o iniciou.
    pub fn start_daemon(&self) -> Result<DaemonState> {
        let b = self.backend.as_ref().map_err(Clone::clone)?;
        // Com o LaunchAgent de antes da Fase 4 instalado, o app não inicia outro Ombro: os dois disputariam a tela
        // (e o tela.py antigo, sem o lock, ficaria reiniciando). Conversar com um ombrod que já roda, pode.
        let now = self.daemon.state();
        if backend::legacy_installed() && !matches!(now, DaemonState::Connected { .. }) {
            return Err(AppError::new(
                ipc::ErrorKind::NotAllowed,
                "o Ombro antigo (LaunchAgent com.ombro-screen) está instalado: migre em Ajustes antes de iniciar o novo",
            ));
        }
        let st = self.daemon.ensure(b);
        let pid = match &st {
            DaemonState::Connected { owner: Owner::App, pid } => *pid,
            _ => None,
        };
        let _ = self.prefs.update(|p| p.owned_daemon_pid = pid);
        Ok(st)
    }
}

fn show_main(app: &AppHandle) {
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
    let o = app.state::<Ombro>();
    o.monitor.window_visible.store(true, Ordering::Relaxed);
    o.monitor.poke();
}

fn hide_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
    app.state::<Ombro>()
        .monitor
        .window_visible
        .store(false, Ordering::Relaxed);
}

/// O laço do monitor (thread própria): inicia o ombrod se preciso, depois consulta, avisa e espera.
fn monitor_loop(app: AppHandle) {
    let o = app.state::<Ombro>();
    if o.backend.is_ok() {
        if let Some(pid) = o.prefs.load().owned_daemon_pid {
            o.daemon.adopt(pid);
        }
        let _ = o.start_daemon();
    }
    let mut notifier = Notifier::default();
    loop {
        let snap = o.monitor.collect(&o.daemon);
        // com a janela escondida, a página não recebe nada: acordá-la a cada retrato custava CPU sem ninguém ver.
        // Ao mostrar a janela, show_main cutuca o laço e ela recebe o retrato novo na hora.
        if o.monitor.window_visible.load(Ordering::Relaxed) {
            let _ = app.emit("ombro://snapshot", &snap);
        }
        if let Some(t) = o.tray.lock().unwrap().as_ref() {
            t.update(&snap);
        }
        let avisos = notifier.process(&snap, Instant::now());
        if !avisos.is_empty() && o.prefs.load().notifications {
            use tauri_plugin_notification::NotificationExt;
            for n in avisos {
                let _ = app.notification().builder().title(&n.title).body(&n.body).show();
            }
        }
        o.monitor.sleep();
    }
}

/// Autoteste do ciclo de vida (só em debug, só com OMBRO_APP_SELFTEST=1): esconde, "fecha", mostra, lê a Menu
/// Bar e sai pelo mesmo caminho do "Sair". Imprime cada fase com o tipo do app no sistema (Foreground = no Dock,
/// UIElement = só na Menu Bar). Serve para testar sem acessibilidade nem gravação de tela.
#[cfg(debug_assertions)]
fn selftest(app: AppHandle) {
    use std::time::Duration;
    let tipo = || {
        let asn = std::process::Command::new("/usr/bin/lsappinfo")
            .args(["find", &format!("pid={}", std::process::id())])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default();
        let out = std::process::Command::new("/usr/bin/lsappinfo")
            .args(["info", "-only", "applicationtype", &asn])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
            .unwrap_or_default();
        out.split("type=\"")
            .nth(1)
            .and_then(|r| r.split('"').next())
            .unwrap_or("?")
            .to_string()
    };
    let visivel = |app: &AppHandle| app.get_webview_window("main").and_then(|w| w.is_visible().ok());
    let passo =
        |n: &str, app: &AppHandle| eprintln!("selftest: {n}: janela visível={:?} tipo={}", visivel(app), tipo());
    std::thread::sleep(Duration::from_secs(6));
    passo("início", &app);
    hide_main(&app);
    std::thread::sleep(Duration::from_millis(1500));
    passo("depois de esconder", &app);
    show_main(&app);
    std::thread::sleep(Duration::from_millis(1500));
    passo("depois de mostrar", &app);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.close(); // o botão vermelho: CloseRequested, que o app transforma em esconder
    }
    std::thread::sleep(Duration::from_millis(1500));
    passo("depois de fechar", &app);
    eprintln!(
        "selftest: a janela continua existindo depois de fechar: {}",
        app.get_webview_window("main").is_some()
    );
    show_main(&app);
    std::thread::sleep(Duration::from_millis(1500));
    passo("reaberta", &app);
    let o = app.state::<Ombro>();
    let snap = o.monitor.latest.lock().unwrap().clone();
    let t = tray::texts(&snap);
    eprintln!("selftest: retrato #{} daemon={:?}", snap.seq, snap.daemon);
    eprintln!(
        "selftest: menu: {} | {} | {} | {} | {} | {} | título={:?}",
        t.header, t.providers[0], t.providers[1], t.providers[2], t.display, t.quit, t.badge
    );
    // o que a página mostrada faz em 10 s com a janela visível: mudanças de DOM, frames e animações ativas
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.eval(
            "(async()=>{let m=0,o=new Set();const mo=new MutationObserver(l=>{for(const x of l){m++;o.add(((x.target.parentElement||x.target).className||x.target.nodeName)+'')}});\
             mo.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true});const f=-1;await new Promise(r=>setTimeout(r,10000));mo.disconnect();\
             location.hash='PERF'+encodeURIComponent(JSON.stringify({mutacoes:m,onde:[...o].slice(0,12),frames:f,animacoes:document.getAnimations().map(a=>a.animationName||a.transitionProperty)}))})()",
        );
        std::thread::sleep(Duration::from_secs(12));
        let url = w
            .url()
            .map(|u| u.fragment().unwrap_or("").to_string())
            .unwrap_or_default();
        let texto = url.strip_prefix("PERF").unwrap_or(&url);
        let decod = texto.split('%').enumerate().fold(Vec::new(), |mut v, (i, parte)| {
            if i == 0 {
                v.extend_from_slice(parte.as_bytes());
            } else if parte.len() >= 2 {
                v.push(u8::from_str_radix(&parte[..2], 16).unwrap_or(b'?'));
                v.extend_from_slice(&parte.as_bytes()[2..]);
            }
            v
        });
        eprintln!("selftest: perfil da página: {}", String::from_utf8_lossy(&decod));
    }
    eprintln!("selftest: saindo pelo caminho do Sair");
    app.exit(0);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        // Tem de ser o primeiro plugin: um segundo `open` do app só traz a janela de volta.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .macos_launcher(tauri_plugin_autostart::MacosLauncher::LaunchAgent)
                .args(["--background"])
                .build(),
        )
        .setup(|app| {
            let resources = app.path().resource_dir().ok();
            let config_dir = app.path().app_config_dir()?;
            app.manage(Ombro {
                backend: Backend::locate(resources),
                daemon: DaemonManager::default(),
                prefs: PrefsStore::new(&config_dir),
                monitor: Monitor::default(),
                tray: Mutex::new(None),
            });
            let t = tray::build(app.handle())?;
            *app.state::<Ombro>().tray.lock().unwrap() = Some(t);

            // A janela nasce invisível (tauri.conf.json). Aberto pelo login (--background) e com o onboarding feito,
            // começa só na Menu Bar; senão, mostra a janela. Criar visível e esconder em seguida deixava o WebKit
            // redesenhando uma janela que ninguém via (~1% de CPU parado).
            let em_segundo_plano = std::env::args().any(|a| a == "--background");
            if em_segundo_plano && app.state::<Ombro>().prefs.load().onboarding_completed {
                hide_main(app.handle());
            } else {
                show_main(app.handle());
            }
            let handle = app.handle().clone();
            std::thread::Builder::new()
                .name("ombro-monitor".into())
                .spawn(move || monitor_loop(handle))?;
            // SIGTERM/SIGINT/SIGHUP (kill, Ctrl+C no terminal, logout): o mesmo "Sair" do menu, para o ombrod que o
            // app iniciou fechar junto em vez de ficar órfão.
            let h = app.handle().clone();
            let mut sinais = signal_hook::iterator::Signals::new([
                signal_hook::consts::SIGTERM,
                signal_hook::consts::SIGINT,
                signal_hook::consts::SIGHUP,
            ])?;
            std::thread::Builder::new().name("ombro-sinais".into()).spawn(move || {
                if sinais.forever().next().is_some() {
                    h.exit(0);
                }
            })?;
            #[cfg(debug_assertions)]
            if std::env::var_os("OMBRO_APP_SELFTEST").is_some() {
                let h = app.handle().clone();
                std::thread::spawn(move || selftest(h));
            }
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            tray::OPEN => show_main(app),
            tray::SETTINGS => {
                show_main(app);
                let _ = app.emit("ombro://navigate", "settings");
            }
            tray::QUIT => app.exit(0),
            _ => {}
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event
                && window.label() == "main"
            {
                api.prevent_close();
                hide_main(window.app_handle());
            }
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_snapshot,
            commands::refresh,
            commands::app_info,
            commands::daemon_start,
            commands::daemon_restart,
            commands::display_status,
            commands::display_preview,
            commands::config_get,
            commands::config_schema,
            commands::config_update,
            commands::display_set_brightness,
            commands::display_set_layout,
            commands::integrations_list,
            commands::integration_run,
            commands::service_run,
            commands::doctor_run,
            commands::doctor_report,
            commands::prefs_get,
            commands::prefs_set,
            commands::autostart_set,
            commands::open_link,
        ])
        .build(tauri::generate_context!())
        .expect("erro ao iniciar o Ombro");

    app.run(|app, event| match event {
        RunEvent::Exit => {
            #[cfg(debug_assertions)]
            if std::env::var_os("OMBRO_APP_SELFTEST").is_some() {
                eprintln!("selftest: RunEvent::Exit (encerrando só o ombrod do app, se houver)");
            }
            // Só o ombrod que o próprio app iniciou; o do launchd (e os agentes) nunca recebem sinal.
            let o = app.state::<Ombro>();
            o.daemon.stop_owned();
            let _ = o.prefs.update(|p| p.owned_daemon_pid = None);
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => show_main(app),
        _ => {}
    });
}
