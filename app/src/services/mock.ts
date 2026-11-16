// Mock do contrato, com as formas que o ombrod --demo devolve de verdade (capturadas da IPC e da CLI).
// Serve aos testes e a `npm run dev` no navegador. Dentro do app, nunca é usado: lá tudo vem do ombrod.
import type { OmbroApi } from "./api";
import type {
  AppPrefs,
  ConfigInfo,
  ConfigSchema,
  DoctorResult,
  IntegrationCheck,
  ProviderInfo,
  Session,
  ServiceStatus,
  Snapshot,
} from "../types/ombro";

const agora = () => new Date();
const iso = (d: Date) => {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
const mais = (min: number) => iso(new Date(agora().getTime() + min * 60_000));

export function mockProviders(): ProviderInfo[] {
  return [
    {
      id: "claude", name: "Claude Code", enabled: true,
      capabilities: ["attention", "events", "quota_windows", "sessions", "token_usage"],
      installed: true, integration: "ok", collection: "ok", state: "aguardando",
      sessions: 3, working: 1, waiting: 1, errors: 0, quota_max_percent: 67, stale_parts: [], recovered: false,
      last_collected_at: iso(agora()), last_event_at: null,
      quotas: {
        source: "statusline", fetched_at: mais(-1), stale: false,
        windows: [
          { id: "5h", label: "sessão 5h", used_percent: 42, resets_at: mais(134) },
          { id: "7d", label: "semana", used_percent: 67, resets_at: mais(3240) },
        ],
      },
      usage: { scope: "today", total_tokens: 18_612_000, input_tokens: 18_400_000, output_tokens: 212_000, messages: 486 },
      avatar: { avatar: "ombro-cloud", color: "#DF8559", choice: "padrao" },
    },
    {
      id: "codex", name: "Codex", enabled: true, capabilities: ["attention", "events", "sessions"],
      installed: true, integration: "ok", collection: "ok", state: "aprovacao",
      sessions: 1, working: 1, waiting: 1, errors: 0, quota_max_percent: null, stale_parts: [], recovered: false,
      last_collected_at: iso(agora()), last_event_at: null, quotas: null, usage: null,
      avatar: { avatar: "orbita", color: "#83B596", choice: "padrao" },
    },
    {
      id: "cursor", name: "Cursor", enabled: true, capabilities: ["events", "sessions"],
      installed: true, integration: "not_configured", collection: "ok", state: "nao_configurado",
      sessions: 0, working: 0, waiting: 0, errors: 0, quota_max_percent: null, stale_parts: [], recovered: false,
      last_collected_at: iso(agora()), last_event_at: null, quotas: null, usage: null,
      avatar: { avatar: "cubo", color: "#79A9D8", choice: "padrao" },
    },
  ];
}

export function mockSessions(): Session[] {
  const s = (provider: Session["provider"], id: string, project: string, status: Session["status"], min: number): Session => ({
    provider, session_id: id, project, status, activity: status === "working" ? "working" : "idle",
    attention: status === "waiting" ? "waiting_user" : status === "approval" ? "waiting_approval" : "none",
    started_at: mais(-40), status_since: mais(-min), updated_at: iso(agora()),
  });
  return [
    s("claude", "demo-1", "ombro-screen", "working", 2),
    s("claude", "demo-2", "site", "waiting", 4),
    s("claude", "demo-3", "notas", "idle", 18),
    s("codex", "demo-codex-1", "api-gateway", "approval", 3),
  ];
}

export function mockSnapshot(): Snapshot {
  return {
    seq: 1, at_ms: Date.now(),
    daemon: { state: "connected", owner: "app", pid: 4321 },
    status: {
      daemon: "running", version: "0.4.0", protocol: 1, pid: 4321, started_at: mais(-75), uptime_s: 4500,
      health: "healthy", providers: { configured: 3, installed: 3, operational: 3 }, sessions: 4, waiting: 2, errors: 0,
      display: { kind: "simulator", connection: "connected", layout: "auto" }, storage: "persistent", demo: true,
      collected_at: iso(agora()), conflicts: [],
      system: { cpu_percent: 23.4, ram_used_bytes: 15_247_133_900, ram_total_bytes: 25_769_803_776, memory_pressure: "normal" },
    },
    providers: {
      providers: mockProviders(), collected_at: iso(agora()),
      ombro_avatar: { avatar: "ombro-cloud", color: "#DF8559", choice: "padrao" }, random_mode: "inicio",
    },
    sessions: { sessions: mockSessions(), collected_at: iso(agora()) },
    health: {
      status: "healthy",
      components: {
        daemon: { status: "healthy", state: "running" }, ipc: { status: "healthy", state: "listening" },
        storage: { status: "healthy", state: "persistent" }, display: { status: "healthy", state: "connected" },
        "provider.claude": { status: "healthy", state: "aguardando" }, "provider.codex": { status: "healthy", state: "aprovacao" },
        "provider.cursor": { status: "unavailable", state: "not_configured" },
      },
    },
    error: null,
  };
}

export const mockSchema: ConfigSchema = {
  version: 1,
  options: [
    { key: "usb", group: "display", type: "boolean", description: "usar a tela USB (desligado: simulador no navegador)", live: false, nullable: false },
    { key: "revisao", group: "display", type: "enum", description: "revisão do hardware da tela", live: false, nullable: false, choices: ["A", "B", "C", "D", "WEACT_A"] },
    { key: "transporte", group: "display", type: "enum", description: "como falar com a tela: auto, USB direto ou porta serial", live: false, nullable: false, choices: ["auto", "usb", "serial"] },
    { key: "porta", group: "display", type: "string", description: "porta serial (AUTO, ou /dev/cu.<nome>)", live: false, nullable: false },
    { key: "brilho", group: "display", type: "integer", description: "brilho em % (a revisão A esquenta acima de 50)", live: true, nullable: false, min: 0, max: 100 },
    { key: "paisagem", group: "display", type: "boolean", description: "tela deitada no layout clássico", live: false, nullable: false },
    { key: "girar", group: "display", type: "boolean", description: "girar 180°", live: false, nullable: false },
    { key: "porta_sim", group: "display", type: "integer", description: "porta do simulador em localhost", live: false, nullable: false, min: 1024, max: 65535 },
    { key: "layout", group: "appearance", type: "enum", description: "o modelo de tela (de ou para o clássico, só ao reiniciar)", live: true, nullable: false, choices: ["auto", "ombro", "classico", "minimal-v2", "multiagente"] },
    { key: "cor_mascote", group: "appearance", type: "color", description: "cor fixa do avatar (#RRGGBB; vale por cima do cor_modo)", live: true, nullable: true },
    { key: "cor_modo", group: "appearance", type: "enum", description: "cor do avatar: a do agente, ou sorteada a cada troca de estado", live: true, nullable: false, choices: ["agente", "sorteada"] },
    ...(["claude", "codex", "cursor", "padrao"] as const).map((p) => ({
      key: `avatares.${p}`, group: "avatars", type: "enum" as const, description: `avatar ${p}`, live: true, nullable: false,
      choices: ["padrao", "aleatorio", "ombro-cloud", "sereno", "orbita", "cubo", "scout", "pixel", "chevron", "maestro", "nova"],
    })),
    { key: "avatar_sorteio", group: "avatars", type: "enum", description: "o aleatório: um por inicialização, ou um por sessão", live: true, nullable: false, choices: ["inicio", "sessao"] },
    { key: "intervalo", group: "appearance", type: "number", description: "segundos entre quadros (null: o padrão do layout)", live: false, nullable: true, min: 0.05, max: 10 },
    { key: "vista", group: "multi_agent", type: "enum", description: "visão do layout multiagente", live: true, nullable: false, choices: ["overview", "focus", "attention"] },
    { key: "foco", group: "multi_agent", type: "enum", description: "o agente da visão focus", live: true, nullable: true, choices: ["claude", "codex", "cursor"] },
    { key: "rotacao", group: "multi_agent", type: "boolean", description: "alternar sozinho entre a visão geral e cada agente", live: true, nullable: false },
    { key: "intervalo_rotacao", group: "multi_agent", type: "number", description: "segundos em cada visão da rotação", live: true, nullable: false, min: 5, max: 3600 },
    { key: "providers.claude", group: "providers", type: "boolean", description: "acompanhar o Claude Code", live: false, nullable: false },
    { key: "providers.codex", group: "providers", type: "boolean", description: "acompanhar o Codex", live: false, nullable: false },
    { key: "providers.cursor", group: "providers", type: "boolean", description: "acompanhar o Cursor", live: false, nullable: false },
    { key: "sem_macmon", group: "hardware", type: "boolean", description: "não usar o macmon (só psutil: sem GPU e temperatura)", live: false, nullable: false },
    { key: "debug", group: "diagnostics", type: "boolean", description: "logs detalhados (nível DEBUG)", live: false, nullable: false },
  ],
};

export function mockConfig(): ConfigInfo {
  return {
    file: "~/.ombro-screen/config.json", file_exists: false,
    values: {
      brilho: 30, cor_mascote: null, cor_modo: "agente", avatar_sorteio: "inicio",
      avatares: { claude: "padrao", codex: "padrao", cursor: "padrao", padrao: "padrao" },
      debug: false, foco: null, girar: false, intervalo: null, intervalo_rotacao: 15,
      layout: "auto", paisagem: false, porta: "AUTO", porta_sim: 5680, providers: ["claude", "codex", "cursor"],
      revisao: "A", rotacao: false, sem_macmon: false, transporte: "auto", usb: false, vista: "overview",
    },
    providers_enabled: { claude: true, codex: true, cursor: true },
    editable: mockSchema.options.map((o) => o.key),
    live: mockSchema.options.filter((o) => o.live).map((o) => o.key),
    layouts_live: ["auto", "ombro", "minimal-v2", "multiagente"],
    views: ["overview", "focus", "attention"],
  };
}

export function mockIntegrations(): IntegrationCheck[] {
  const base = (provider: IntegrationCheck["provider"], file: string, eventos: string[], presentes: string[], instalado: boolean): IntegrationCheck => ({
    provider, file, existe: presentes.length > 0, instalado, eventos, presentes, desatualizados: [], ambiguos: [], sobrando: [],
    hook_existe: true, erro: null, state: presentes.length === eventos.length ? "configured" : presentes.length ? "partial" : "not_configured",
  });
  const claude = ["SessionStart", "SessionEnd", "PermissionRequest", "Stop", "StopFailure"];
  const codex = ["SessionStart", "SessionEnd", "UserPromptSubmit", "PermissionRequest", "PostToolUse", "Stop", "Interrupt", "PostModelRequest"];
  const cursor = ["sessionStart", "sessionEnd", "beforeSubmitPrompt", "postToolUse", "postToolUseFailure", "stop"];
  return [
    base("claude", "~/.claude/settings.json", claude, claude, true),
    base("codex", "~/.codex/hooks.json", codex, codex, true),
    base("cursor", "~/.cursor/hooks.json", cursor, [], true),
  ];
}

export function mockService(): ServiceStatus {
  return {
    label: "com.ombro-screen.daemon", plist: "~/Library/LaunchAgents/com.ombro-screen.daemon.plist", installed: false, ours: false,
    loaded: null, log: "~/Library/Logs/ombro-screen/ombrod.log", program: [],
    legacy: { label: "com.ombro-screen", plist: "~/Library/LaunchAgents/com.ombro-screen.plist", installed: false, loaded: null },
    backups: [],
  };
}

export function mockDoctor(): DoctorResult {
  return {
    schema: "ombro.doctor", schema_version: 1, ombro_version: "0.4.0", ipc_protocol: 1, generated_at: iso(agora()),
    summary: { status: "WARN", pass: 5, warn: 1, fail: 0, skip: 1 },
    checks: [
      { id: "env.macos", category: "environment", status: "PASS", title: "Versão do macOS", message: "27.0.1", action: null },
      { id: "env.arch", category: "environment", status: "PASS", title: "Arquitetura", message: "arm64", action: null },
      { id: "service.daemon", category: "service", status: "PASS", title: "ombrod", message: "running, saúde healthy, protocolo v1", action: null },
      { id: "service.launchagent", category: "service", status: "WARN", title: "LaunchAgent", message: "o serviço não está instalado: o ombrod só roda com o app aberto", action: "python3 -m ombro service install" },
      { id: "provider.claude", category: "providers", status: "PASS", title: "Claude Code", message: "integração configurada", action: null },
      { id: "provider.codex", category: "providers", status: "SKIP", title: "Codex", message: "não instalado", action: null },
      { id: "display.connection", category: "display", status: "PASS", title: "Tela", message: "simulator: connected", action: null },
    ],
  };
}

/** Um mock com estado (as mudanças de preferência ficam até recarregar), para usar a interface no navegador. */
export function createMockApi(): OmbroApi {
  const config = mockConfig();
  let prefs: AppPrefs = { onboarding_completed: true, notifications: false, owned_daemon_pid: null, autostart: false };
  const integrations = mockIntegrations();
  const ok = <T,>(result: T) => Promise.resolve({ exit_code: 0, result });
  return {
    kind: "mock",
    getSnapshot: () => Promise.resolve(mockSnapshot()),
    onSnapshot: async (cb) => {
      const t = setInterval(() => cb(mockSnapshot()), 3000);
      return () => clearInterval(t);
    },
    onNavigate: async () => () => {},
    refresh: () => Promise.resolve(),
    appInfo: () =>
      Promise.resolve({
        version: "0.5.0-alpha.1", protocol: 1, mode: null, backend_error: null, location_problem: null,
        service_installed: false, legacy_installed: false, arch: "aarch64", os_version: "27.0.1",
      }),
    daemonStart: () => Promise.resolve({ state: "connected", owner: "app", pid: 4321 }),
    daemonRestart: () => Promise.resolve({ state: "connected", owner: "app", pid: 4322 }),
    displayStatus: () =>
      Promise.resolve({
        kind: "simulator", connection: "connected", transport: "http", brightness: null, orientation: "portrait",
        layout: String(config.values.layout), frames: 1200, last_frame_at: iso(agora()), url: "http://localhost:5680",
        mascot: { avatar: "ombro-cloud", provider: "claude", state: "esperando", color: "#DF8559" },
      }),
    displayPreview: () => Promise.resolve({ available: false }),
    configGet: () => Promise.resolve(structuredClone(config)),
    configSchema: () => Promise.resolve(mockSchema),
    configUpdate: async (changes, persist) => {
      const live = new Set(config.live);
      for (const [k, v] of Object.entries(changes)) {
        if (k.startsWith("avatares.")) (config.values.avatares as Record<string, unknown>)[k.slice(9)] = v;
        else config.values[k] = v;
      }
      const keys = Object.keys(changes);
      return { applied: keys.filter((k) => live.has(k)), restart_required: keys.filter((k) => !live.has(k)), persisted: persist, file: persist ? config.file : null };
    },
    setBrightness: async (value, persist) => {
      config.values.brilho = value;
      return { applied: ["brilho"], restart_required: [], persisted: persist, file: null };
    },
    setLayout: async (layout, persist) => {
      config.values.layout = layout;
      return { applied: ["layout"], restart_required: [], persisted: persist, file: null };
    },
    integrationsList: () => ok({ integrations }),
    integrationRun: (provider, action, dryRun) =>
      ok({
        ok: true as const, provider, action, file: integrations.find((i) => i.provider === provider)!.file, changed: true, dry_run: dryRun,
        diff: dryRun ? `--- ~/.cursor/hooks.json (agora)\n+++ ~/.cursor/hooks.json (depois)\n+  "sessionStart": [{"command": "python3 …/ombro_hook.py cursor"}]\n` : "",
        backup: dryRun ? null : "~/.ombro-screen/backups/integrations/cursor/x.bak", check: null, note: "",
      }),
    serviceStatus: () => ok(mockService()),
    serviceRun: (action, dryRun) => ok({ ok: true as const, action, dry_run: dryRun, steps: ["(demonstração: nada foi feito)"] }),
    doctorRun: () => ok(mockDoctor()),
    doctorReport: () => Promise.resolve("# Ombro: relatório de diagnóstico\n\n(demonstração)\n"),
    prefsGet: () => Promise.resolve({ ...prefs }),
    prefsSet: async (patch) => (prefs = { ...prefs, ...patch }),
    autostartSet: async (enabled) => (prefs = { ...prefs, autostart: enabled }).autostart,
    openLink: () => Promise.resolve(),
  };
}
