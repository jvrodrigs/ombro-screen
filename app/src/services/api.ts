import type {
  AppError,
  AppInfo,
  AppPrefs,
  CliResult,
  ConfigInfo,
  ConfigSchema,
  ConfigUpdateResult,
  ConfigValue,
  DaemonState,
  DisplayStatus,
  DoctorResult,
  IntegrationAction,
  IntegrationCheck,
  IntegrationRunResult,
  Link,
  Preview,
  ProviderId,
  ServiceAction,
  ServiceRunResult,
  ServiceStatus,
  Snapshot,
} from "../types/ombro";

/** Tudo o que a interface pode pedir. Cada método vira um comando Tauri tipado (src-tauri/src/commands.rs). */
export interface OmbroApi {
  readonly kind: "tauri" | "mock";
  getSnapshot(): Promise<Snapshot>;
  onSnapshot(cb: (s: Snapshot) => void): Promise<() => void>;
  onNavigate(cb: (page: string) => void): Promise<() => void>;
  refresh(): Promise<void>;
  appInfo(): Promise<AppInfo>;
  daemonStart(): Promise<DaemonState>;
  daemonRestart(): Promise<DaemonState>;
  displayStatus(): Promise<DisplayStatus>;
  displayPreview(): Promise<Preview>;
  configGet(): Promise<ConfigInfo>;
  configSchema(): Promise<ConfigSchema>;
  configUpdate(changes: Record<string, ConfigValue>, persist: boolean): Promise<ConfigUpdateResult>;
  setBrightness(value: number, persist: boolean): Promise<ConfigUpdateResult>;
  setLayout(layout: string, persist: boolean): Promise<ConfigUpdateResult>;
  integrationsList(): Promise<CliResult<{ integrations: IntegrationCheck[] }>>;
  integrationRun(provider: ProviderId, action: IntegrationAction, dryRun: boolean): Promise<CliResult<IntegrationRunResult>>;
  serviceStatus(): Promise<CliResult<ServiceStatus>>;
  serviceRun(action: Exclude<ServiceAction, "status">, dryRun: boolean): Promise<CliResult<ServiceRunResult>>;
  doctorRun(): Promise<CliResult<DoctorResult>>;
  doctorReport(): Promise<string>;
  prefsGet(): Promise<AppPrefs>;
  prefsSet(patch: Partial<Pick<AppPrefs, "onboarding_completed" | "notifications">>): Promise<AppPrefs>;
  autostartSet(enabled: boolean): Promise<boolean>;
  openLink(link: Link): Promise<void>;
}

export function isAppError(e: unknown): e is AppError {
  return typeof e === "object" && e !== null && "kind" in e && "message" in e;
}

/** Mensagem para a interface, a partir de qualquer erro (os do Rust já vêm curtos e sem caminhos). */
export function errorMessage(e: unknown): string {
  if (isAppError(e)) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

function hasTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function tauriApi(): Promise<OmbroApi> {
  const { invoke } = await import("@tauri-apps/api/core");
  const { listen } = await import("@tauri-apps/api/event");
  return {
    kind: "tauri",
    getSnapshot: () => invoke("get_snapshot"),
    onSnapshot: (cb) => listen<Snapshot>("ombro://snapshot", (e) => cb(e.payload)),
    onNavigate: (cb) => listen<string>("ombro://navigate", (e) => cb(e.payload)),
    refresh: () => invoke("refresh"),
    appInfo: () => invoke("app_info"),
    daemonStart: () => invoke("daemon_start"),
    daemonRestart: () => invoke("daemon_restart"),
    displayStatus: () => invoke("display_status"),
    displayPreview: () => invoke("display_preview"),
    configGet: () => invoke("config_get"),
    configSchema: () => invoke("config_schema"),
    configUpdate: (changes, persist) => invoke("config_update", { changes, persist }),
    setBrightness: (value, persist) => invoke("display_set_brightness", { value, persist }),
    setLayout: (layout, persist) => invoke("display_set_layout", { layout, persist }),
    integrationsList: () => invoke("integrations_list"),
    integrationRun: (provider, action, dryRun) => invoke("integration_run", { provider, action, dryRun }),
    serviceStatus: () => invoke("service_run", { action: "status", dryRun: false }),
    serviceRun: (action, dryRun) => invoke("service_run", { action, dryRun }),
    doctorRun: () => invoke("doctor_run"),
    doctorReport: () => invoke("doctor_report"),
    prefsGet: () => invoke("prefs_get"),
    prefsSet: (patch) => invoke("prefs_set", { patch }),
    autostartSet: (enabled) => invoke("autostart_set", { enabled }),
    openLink: (link) => invoke("open_link", { link }),
  };
}

let current: Promise<OmbroApi> | null = null;

/** A API em uso: a do Tauri dentro do app; o mock do contrato no navegador (`npm run dev`) e nos testes. */
export function getApi(): Promise<OmbroApi> {
  if (!current) {
    current = hasTauri() ? tauriApi() : import("./mock").then((m) => m.createMockApi());
  }
  return current;
}

/** Para os testes: trocar a API por uma montada à mão. */
export function setApiForTests(api: OmbroApi | null): void {
  current = api ? Promise.resolve(api) : null;
}
