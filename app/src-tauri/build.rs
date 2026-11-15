// Os comandos do app só ficam acessíveis ao JavaScript pelas permissões da capability
// (capabilities/main.json): sem o manifest, o Tauri liberaria todos para qualquer janela.
const COMMANDS: &[&str] = &[
    "get_snapshot",
    "refresh",
    "app_info",
    "daemon_start",
    "daemon_restart",
    "display_status",
    "display_preview",
    "config_get",
    "config_schema",
    "config_update",
    "display_set_brightness",
    "display_set_layout",
    "integrations_list",
    "integration_run",
    "service_run",
    "doctor_run",
    "doctor_report",
    "prefs_get",
    "prefs_set",
    "autostart_set",
    "open_link",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("falha no build do Tauri");
}
