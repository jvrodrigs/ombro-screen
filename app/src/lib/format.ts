// Formatação e rótulos em português. Valor ausente vira "—": nunca um 0 inventado.
import type { ProviderInfo, ProviderState, SessionStatus } from "../types/ombro";

export const DASH = "—";

/** Horários da IPC: ISO 8601 em hora local, sem fuso (o `Date` do JS lê assim mesmo). */
export function parseLocal(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "agora", "há 3 min", "há 2 h", "há 4 d". */
export function ago(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseLocal(iso);
  if (!d) return DASH;
  const s = Math.max(0, Math.round((now.getTime() - d.getTime()) / 1000));
  if (s < 10) return "agora";
  if (s < 60) return `há ${s} s`;
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `há ${Math.floor(s / 86400)} d`;
}

/** Quanto falta: "em 2 h 14 min", "em 3 d 4 h". */
export function until(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseLocal(iso);
  if (!d) return DASH;
  const s = Math.round((d.getTime() - now.getTime()) / 1000);
  if (s <= 0) return "agora";
  const dias = Math.floor(s / 86400);
  const horas = Math.floor((s % 86400) / 3600);
  const min = Math.floor((s % 3600) / 60);
  if (dias > 0) return `em ${dias} d${horas ? ` ${horas} h` : ""}`;
  if (horas > 0) return `em ${horas} h${min ? ` ${min} min` : ""}`;
  return `em ${Math.max(1, min)} min`;
}

/** Hora do dia ("16:44") ou dia e hora quando não é hoje ("seg 19:30"). */
export function clock(iso: string | null | undefined, now: Date = new Date()): string {
  const d = parseLocal(iso);
  if (!d) return DASH;
  const hm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return hm;
  const dia = d.toLocaleDateString("pt-BR", { weekday: "short" }).replace(".", "");
  return `${dia} ${hm}`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return DASH;
  const s = Math.floor(seconds);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d} d ${h} h`;
  if (h) return `${h} h ${m} min`;
  return `${m} min`;
}

/** 18612000 -> "18,6 M"; 212000 -> "212 mil". */
export function tokens(n: number | null | undefined): string {
  if (n == null) return DASH;
  if (n >= 1_000_000) return `${(n / 1_000_000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} M`;
  if (n >= 10_000) return `${Math.round(n / 1000).toLocaleString("pt-BR")} mil`;
  return n.toLocaleString("pt-BR");
}

export function percent(n: number | null | undefined): string {
  if (n == null) return DASH;
  return `${Math.round(n)}%`;
}

export function bytes(n: number | null | undefined): string {
  if (n == null) return DASH;
  const gb = n / 1024 ** 3;
  return `${gb.toLocaleString("pt-BR", { maximumFractionDigits: gb >= 10 ? 0 : 1 })} GB`;
}

/** Nível de uma barra de uso, com os mesmos limites da telinha (75 % atenção, 90 % crítico). */
export function level(p: number | null | undefined): "normal" | "warning" | "critical" {
  if (p == null) return "normal";
  if (p >= 90) return "critical";
  if (p >= 75) return "warning";
  return "normal";
}

export type Tone = "neutral" | "orange" | "blue" | "green" | "warning" | "critical" | "muted";

export const PROVIDER_STATE: Record<ProviderState, { label: string; tone: Tone }> = {
  trabalhando: { label: "Trabalhando", tone: "orange" },
  aguardando: { label: "Esperando você", tone: "warning" },
  aprovacao: { label: "Pede aprovação", tone: "warning" },
  erro: { label: "Sessão com erro", tone: "critical" },
  alerta: { label: "Perto do limite", tone: "critical" },
  ocioso: { label: "Ocioso", tone: "green" },
  sem_sessoes: { label: "Sem sessões", tone: "muted" },
  nao_instalado: { label: "Não instalado", tone: "muted" },
  nao_configurado: { label: "Integração não instalada", tone: "neutral" },
  indisponivel: { label: "Indisponível", tone: "critical" },
  desconhecido: { label: "Sem dados", tone: "muted" },
};

export const SESSION_STATUS: Record<SessionStatus, { label: string; tone: Tone }> = {
  working: { label: "trabalhando", tone: "orange" },
  waiting: { label: "esperando você", tone: "warning" },
  approval: { label: "pede aprovação", tone: "warning" },
  error: { label: "erro", tone: "critical" },
  idle: { label: "ocioso", tone: "green" },
  unknown: { label: "sem status", tone: "muted" },
};

export const PROVIDER_NAMES: Record<string, string> = { claude: "Claude Code", codex: "Codex", cursor: "Cursor" };

export function providerName(p: Pick<ProviderInfo, "id" | "name">): string {
  return p.name ?? PROVIDER_NAMES[p.id] ?? p.id;
}

/** O estado de um provider para a interface, separando "desligado" e "dados velhos" do estado do backend. */
export function providerBadge(p: ProviderInfo): { label: string; tone: Tone } {
  if (!p.enabled) return { label: "Desligado", tone: "muted" };
  if (p.collection === "error" && !p.sessions) return { label: "Coleta com erro", tone: "critical" };
  const base = p.state ? PROVIDER_STATE[p.state] : PROVIDER_STATE.desconhecido;
  return base ?? PROVIDER_STATE.desconhecido;
}

export const LAYOUT_NAMES: Record<string, string> = {
  auto: "Automático",
  ombro: "Ombro",
  "minimal-v2": "Minimal v2",
  multiagente: "Multiagente",
  classico: "Clássico",
};

export const QUOTA_SOURCES: Record<string, string> = {
  statusline: "statusline",
  habblaud: "Habblaud",
  "/usage": "/usage",
};

export function pluralize(n: number, um: string, varios: string): string {
  return `${n} ${n === 1 ? um : varios}`;
}
