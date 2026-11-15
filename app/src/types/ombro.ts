// O contrato com o backend: a IPC v1 do ombrod (docs/ipc.md), a saída --json da CLI (integrations, service,
// doctor) e o que o lado Rust do app acrescenta (Snapshot, DaemonState, AppInfo). Campos desconhecidos são
// ignorados; campos que o backend não informa são `null`, e a interface nunca os troca por zero.

export type ProviderId = "claude" | "codex" | "cursor";
export const PROVIDER_IDS: ProviderId[] = ["claude", "codex", "cursor"];

export type ErrorKind =
  | "daemon_unavailable"
  | "protocol_mismatch"
  | "rejected"
  | "ipc"
  | "backend_missing"
  | "cli"
  | "not_allowed";

export interface AppError {
  kind: ErrorKind;
  code?: string;
  message: string;
}

export type DaemonOwner = "app" | "launchd" | "external";

export type DaemonState =
  | { state: "connected"; owner: DaemonOwner; pid: number | null }
  | { state: "waiting_for_service" }
  | { state: "incompatible"; reason: string }
  | { state: "failed"; reason: string };

export type Lifecycle = "starting" | "running" | "degraded" | "stopping" | "stopped";
export type HealthStatus = "healthy" | "degraded" | "unhealthy" | "unavailable";

export interface SystemMetrics {
  cpu_percent: number;
  ram_used_bytes: number;
  ram_total_bytes: number;
  memory_pressure: "normal" | "warning" | "critical" | null;
}

export interface DaemonStatus {
  daemon: Lifecycle;
  version: string;
  protocol: number;
  pid: number;
  started_at: string | null;
  uptime_s: number;
  health: HealthStatus;
  providers: { configured: number; installed: number; operational: number };
  sessions: number;
  waiting: number;
  errors: number;
  display: { kind: DisplayKind; connection: DisplayConnection; layout: string };
  storage: "persistent" | "memory_only" | "disabled" | string;
  demo: boolean;
  collected_at: string | null;
  conflicts: number[];
  system?: SystemMetrics | null;
}

export type Capability =
  | "sessions"
  | "attention"
  | "token_usage"
  | "quota_windows"
  | "context_usage"
  | "costs"
  | "events";

export interface QuotaWindow {
  id: string;
  label: string;
  used_percent: number | null;
  resets_at: string | null;
}

export interface Quotas {
  source: string;
  fetched_at: string | null;
  stale: boolean;
  windows: QuotaWindow[];
}

export interface Usage {
  scope: string;
  total_tokens: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  messages: number | null;
}

/** Estados do core/resumo.py (em português no backend). */
export type ProviderState =
  | "nao_instalado"
  | "nao_configurado"
  | "indisponivel"
  | "erro"
  | "aprovacao"
  | "aguardando"
  | "alerta"
  | "trabalhando"
  | "sem_sessoes"
  | "ocioso"
  | "desconhecido";

export interface ProviderInfo {
  id: ProviderId;
  name?: string;
  enabled: boolean;
  capabilities?: Capability[];
  installed?: boolean | null;
  integration?: "ok" | "not_installed" | "not_configured" | null;
  collection?: "ok" | "partial" | "error" | null;
  state?: ProviderState;
  sessions?: number;
  working?: number;
  waiting?: number;
  errors?: number;
  quota_max_percent?: number | null;
  stale_parts?: string[];
  recovered?: boolean;
  last_collected_at?: string | null;
  last_event_at?: string | null;
  quotas?: Quotas | null;
  usage?: Usage | null;
}

export type SessionStatus = "working" | "idle" | "waiting" | "approval" | "error" | "unknown";

export interface Session {
  provider: ProviderId;
  session_id: string;
  project: string | null;
  status: SessionStatus;
  activity: string;
  attention: string;
  started_at: string | null;
  status_since: string | null;
  updated_at: string | null;
}

export interface HealthComponent {
  status: HealthStatus;
  state: string;
}

export interface Health {
  status: HealthStatus;
  components: Record<string, HealthComponent>;
}

export interface Snapshot {
  seq: number;
  at_ms: number;
  daemon: DaemonState | null;
  status: DaemonStatus | null;
  providers: { providers: ProviderInfo[]; collected_at: string | null } | null;
  sessions: { sessions: Session[]; collected_at: string | null } | null;
  health: Health | null;
  error: AppError | null;
}

export type DisplayKind = "usb" | "simulator" | "none";
export type DisplayConnection = "connected" | "disconnected" | "error" | "conflict" | "none";

export interface DisplayStatus {
  kind: DisplayKind;
  connection: DisplayConnection;
  transport?: string | null;
  revision?: string | null;
  port?: string | null;
  brightness?: number | null;
  orientation?: string | null;
  layout: string;
  frames: number;
  last_frame_at: string | null;
  connected_since?: string | null;
  last_error?: string | null;
  url?: string | null;
}

export type Preview =
  | { available: false }
  | { available: true; format: "png"; width: number; height: number; captured_at: string | null; data_base64: string };

export type ConfigValue = string | number | boolean | null;

export interface ConfigInfo {
  file: string;
  file_exists: boolean;
  values: Record<string, unknown>;
  providers_enabled: Record<ProviderId, boolean>;
  editable: string[];
  live: string[];
  layouts_live: string[];
  views: string[];
}

export interface SchemaOption {
  key: string;
  group: "display" | "appearance" | "multi_agent" | "providers" | "hardware" | "diagnostics" | string;
  type: "boolean" | "enum" | "string" | "integer" | "number" | "color";
  description: string;
  live: boolean;
  nullable: boolean;
  choices?: (string | number)[];
  min?: number;
  max?: number;
}

export interface ConfigSchema {
  version: number;
  options: SchemaOption[];
}

export interface ConfigUpdateResult {
  applied: string[];
  restart_required: string[];
  persisted: boolean;
  file: string | null;
}

/** O que a CLI imprimiu (`result`) e o código de saída. */
export interface CliResult<T> {
  exit_code: number;
  result: T;
}

export type IntegrationState = "configured" | "partial" | "not_configured" | "conflict" | "invalid";

export interface IntegrationCheck {
  provider: ProviderId;
  file: string;
  existe: boolean;
  instalado: boolean;
  eventos: string[];
  presentes: string[];
  desatualizados: string[];
  ambiguos: string[];
  sobrando: string[];
  hook_existe: boolean;
  erro: string | null;
  state: IntegrationState;
}

export type IntegrationAction = "check" | "install" | "uninstall" | "rollback";

export type IntegrationRunResult =
  | {
      ok: true;
      provider: ProviderId;
      action: string;
      file: string;
      changed: boolean;
      dry_run: boolean;
      diff: string;
      backup: string | null;
      check: IntegrationCheck | null;
      note: string;
    }
  | { ok: false; provider: ProviderId; action: string; error: string };

export type ServiceAction = "status" | "install" | "uninstall" | "start" | "stop" | "restart" | "migrate" | "rollback";

export interface LaunchdInfo {
  state?: string;
  pid?: number;
  last_exit_code?: string;
}

export interface ServiceStatus {
  label: string;
  plist: string;
  installed: boolean;
  ours: boolean;
  loaded: LaunchdInfo | null;
  log: string;
  program: string[];
  legacy: { label: string; plist: string; installed: boolean; loaded: LaunchdInfo | null };
  backups: string[];
}

export type ServiceRunResult =
  | { ok: true; action: string; dry_run: boolean; steps: string[] }
  | { ok: false; action: string; error: string; steps?: string[] };

export type CheckStatus = "PASS" | "WARN" | "FAIL" | "SKIP";

export interface DoctorCheck {
  id: string;
  category: "environment" | "service" | "providers" | "storage" | "display" | "configuration" | string;
  status: CheckStatus;
  title: string;
  message: string;
  action: string | null;
  details?: Record<string, unknown>;
}

export interface DoctorResult {
  schema: "ombro.doctor";
  schema_version: number;
  ombro_version: string;
  ipc_protocol: number;
  generated_at: string;
  summary: { status: CheckStatus; pass: number; warn: number; fail: number; skip: number };
  checks: DoctorCheck[];
}

export interface AppInfo {
  version: string;
  protocol: number;
  mode: "bundled" | "development" | null;
  backend_error: AppError | null;
  location_problem: string | null;
  service_installed: boolean;
  legacy_installed: boolean;
  arch: string;
  os_version: string | null;
}

export interface AppPrefs {
  onboarding_completed: boolean;
  notifications: boolean;
  owned_daemon_pid: number | null;
  autostart: boolean;
}

export type Link = "repository" | "releases" | "issues" | "docs" | "troubleshooting" | "notification_settings" | "third_party_notices";
