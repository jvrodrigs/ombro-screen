// O estado do mascote de um agente no app, com as mesmas regras da telinha (mascote.py, Mascote.decidir):
// esperando > alerta > trabalhando > concluído > ocioso, e o reset (a janela de limite que reiniciou) por cima.
// O estado de base vem do ombrod (providers.list[].state, de core/resumo.py); concluído e reset são transições
// que o app percebe entre um retrato e outro, como a telinha percebe entre uma leitura e outra.
import type { ProviderInfo, SessionStatus } from "../../types/ombro";

export type MascotState = "ocioso" | "trabalhando" | "esperando" | "alerta" | "reset" | "concluido";

/** Quanto tempo o "concluído" fica (o mesmo DURACAO_CONCLUIDO da telinha) e o reset (o mesmo _reset_ate). */
export const DONE_MS = 300_000;
export const RESET_MS = 3_000;
/** O horário de reset de uma janela andou mais que isso: a janela reiniciou (como na telinha). */
const RESET_JUMP_MS = 3_600_000;

/** O estado de agora, só pelo retrato (sem concluído nem reset, que dependem do anterior). */
export function baseState(p: Pick<ProviderInfo, "state" | "working" | "waiting" | "errors">): MascotState {
  switch (p.state) {
    case "erro":
    case "aprovacao":
    case "aguardando":
      return "esperando";
    case "alerta":
      return "alerta";
    case "trabalhando":
      return "trabalhando";
    default:
      if (p.waiting || p.errors) return "esperando";
      return p.working ? "trabalhando" : "ocioso";
  }
}

export function sessionState(status: SessionStatus | undefined): MascotState {
  if (status === "working") return "trabalhando";
  if (status === "waiting" || status === "approval" || status === "error") return "esperando";
  return "ocioso";
}

export interface Memory {
  base: MascotState;
  resets: Record<string, number>;
  /** Um estado de passagem (concluído ou reset) e até quando (ms). */
  transient: { state: MascotState; until: number } | null;
}

export const EMPTY_MEMORY: Memory = { base: "ocioso", resets: {}, transient: null };

/** Um passo: do que foi lembrado e do retrato novo, o que lembrar agora. */
export function step(prev: Memory, p: Pick<ProviderInfo, "state" | "working" | "waiting" | "errors" | "quotas">, now: number): Memory {
  const base = baseState(p);
  const resets: Record<string, number> = {};
  let transient = prev.transient && prev.transient.until > now ? prev.transient : null;
  for (const w of p.quotas?.windows ?? []) {
    const t = w.resets_at ? Date.parse(w.resets_at) : NaN;
    if (Number.isNaN(t)) continue;
    resets[w.id] = t;
    const ant = prev.resets[w.id];
    if (ant !== undefined && t - ant > RESET_JUMP_MS) transient = { state: "reset", until: now + RESET_MS };
  }
  // terminou: trabalhava e agora nenhuma sessão trabalha nem espera você
  if (prev.base === "trabalhando" && base === "ocioso") transient = { state: "concluido", until: now + DONE_MS };
  // voltou a trabalhar (ou a esperar): o concluído acaba; o reset dura os segundos dele
  if (transient?.state === "concluido" && base !== "ocioso") transient = null;
  return { base, resets, transient };
}

export function current(m: Memory, now: number): MascotState {
  if (m.transient && m.transient.until > now) return m.transient.state;
  return m.base;
}

export const STATE_LABEL: Record<MascotState, string> = {
  ocioso: "ocioso",
  trabalhando: "trabalhando",
  esperando: "esperando você",
  alerta: "perto do limite",
  reset: "limite reiniciado",
  concluido: "concluído",
};
