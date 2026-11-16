import { describe, expect, it } from "vitest";
import { baseState, current, DONE_MS, EMPTY_MEMORY, RESET_MS, sessionState, step } from "./state";

const p = (state: string, extra: object = {}) => ({ state, working: 0, waiting: 0, errors: 0, ...extra }) as Parameters<typeof step>[1];

describe("estado do mascote no app (as regras da telinha)", () => {
  it("o estado de base vem do ombrod", () => {
    expect(baseState(p("aguardando"))).toBe("esperando");
    expect(baseState(p("aprovacao"))).toBe("esperando");
    expect(baseState(p("erro"))).toBe("esperando");
    expect(baseState(p("alerta"))).toBe("alerta");
    expect(baseState(p("trabalhando"))).toBe("trabalhando");
    expect(baseState(p("sem_sessoes"))).toBe("ocioso");
    expect(baseState(p("ocioso"))).toBe("ocioso");
  });

  it("terminou de trabalhar: concluído até voltar a trabalhar, ou por DONE_MS", () => {
    let m = step(EMPTY_MEMORY, p("trabalhando"), 0);
    m = step(m, p("ocioso"), 1000);
    expect(current(m, 1000)).toBe("concluido");
    expect(current(m, 1000 + DONE_MS + 1)).toBe("ocioso");
    m = step(m, p("trabalhando"), 2000);
    expect(current(m, 2000)).toBe("trabalhando");
  });

  it("de esperando direto para parado não é concluído (você cancelou)", () => {
    let m = step(EMPTY_MEMORY, p("aguardando"), 0);
    m = step(m, p("ocioso"), 1000);
    expect(current(m, 1000)).toBe("ocioso");
  });

  it("uma janela de limite que reiniciou: reset por uns segundos, por cima de tudo", () => {
    const q = (resets_at: string) => ({ quotas: { source: "", fetched_at: null, stale: false, windows: [{ id: "5h", label: "5h", used_percent: 10, resets_at }] } });
    let m = step(EMPTY_MEMORY, p("trabalhando", q("2026-11-16T10:00:00Z")), 0);
    m = step(m, p("trabalhando", q("2026-11-16T10:20:00Z")), 1000); // andou pouco: não é reset
    expect(current(m, 1000)).toBe("trabalhando");
    m = step(m, p("trabalhando", q("2026-11-16T15:00:00Z")), 2000);
    expect(current(m, 2000)).toBe("reset");
    expect(current(m, 2000 + RESET_MS + 1)).toBe("trabalhando");
  });

  it("sessões viram a pose do estado delas", () => {
    expect(sessionState("working")).toBe("trabalhando");
    expect(sessionState("approval")).toBe("esperando");
    expect(sessionState("idle")).toBe("ocioso");
  });
});
