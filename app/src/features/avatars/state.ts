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
  /** Havia sessão trabalhando no retrato anterior? (o _havia_trabalho da telinha) */
  working: boolean;
  /** O horário de reset de cada janela no retrato anterior (ms). */
  resets: Record<string, number>;
  /** Até quando (ms) o reset fica por cima de tudo; null = sem reset. Como o _reset_ate da telinha. */
  resetUntil: number | null;
  /** Até quando (ms) o concluído vale, se nada mais forte aparecer; null = não terminou nada. Como o _terminou_em +
   * DURACAO_CONCLUIDO da telinha. Separado do reset: um não apaga o outro quando chegam juntos. */
  doneUntil: number | null;
}

export const EMPTY_MEMORY: Memory = { base: "ocioso", working: false, resets: {}, resetUntil: null, doneUntil: null };

const vigente = (ate: number | null, now: number) => (ate !== null && ate > now ? ate : null);

/** Um passo: do que foi lembrado e do retrato novo, o que lembrar agora. */
export function step(prev: Memory, p: Pick<ProviderInfo, "state" | "working" | "waiting" | "errors" | "quotas">, now: number): Memory {
  const base = baseState(p);
  // só as janelas do retrato: uma janela (ou a fonte inteira) que some é esquecida, e a volta dela não é reset; assim
  // um reset antigo não reaparece depois de uma reconexão (a telinha faz o mesmo, ver Mascote.decidir)
  const resets: Record<string, number> = {};
  let resetUntil = vigente(prev.resetUntil, now);
  for (const w of p.quotas?.windows ?? []) {
    const t = w.resets_at ? Date.parse(w.resets_at) : NaN;
    if (Number.isNaN(t)) continue;
    const ant = prev.resets[w.id];
    resets[w.id] = t;
    // um reset novo começa agora; o que está em curso segue o prazo dele (retratos repetidos não o recomeçam)
    if (ant !== undefined && t - ant > RESET_JUMP_MS) resetUntil = now + RESET_MS;
  }
  // terminou: havia sessão trabalhando e agora nenhuma trabalha nem espera você (a regra da telinha, pelas sessões e
  // não pelo estado de base: do alerta com uma sessão trabalhando para parado também é "terminou"). Vale por DONE_MS a
  // partir de agora, mesmo com um reset por cima: o concluído aparece quando o reset acaba
  const working = base === "trabalhando" || (p.working ?? 0) > 0;
  let doneUntil = vigente(prev.doneUntil, now);
  if (prev.working && !working && !p.waiting && base === "ocioso") doneUntil = now + DONE_MS;
  // voltou a trabalhar, a esperar ou chegou perto do limite: o concluído acaba (os estados de base mandam)
  if (base !== "ocioso") doneUntil = null;
  return { base, working, resets, resetUntil, doneUntil };
}

/** reset (por cima de tudo, RESET_MS) > o estado de base, com o concluído no lugar do ocioso enquanto vale. */
export function current(m: Memory, now: number): MascotState {
  if (vigente(m.resetUntil, now) !== null) return "reset";
  if (m.base === "ocioso" && vigente(m.doneUntil, now) !== null) return "concluido";
  return m.base;
}

/** Quando (ms) o estado mostrado muda sozinho, sem retrato novo: o fim do reset ou do concluído. null = não muda. */
export function nextChange(m: Memory, now: number): number | null {
  const prazos = [vigente(m.resetUntil, now), vigente(m.doneUntil, now)].filter((t): t is number => t !== null);
  return prazos.length ? Math.min(...prazos) : null;
}

export const STATE_LABEL: Record<MascotState, string> = {
  ocioso: "ocioso",
  trabalhando: "trabalhando",
  esperando: "esperando você",
  alerta: "perto do limite",
  reset: "limite reiniciado",
  concluido: "concluído",
};
