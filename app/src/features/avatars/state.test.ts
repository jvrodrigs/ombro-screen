import { describe, expect, it } from "vitest";
import { baseState, current, DONE_MS, EMPTY_MEMORY, nextChange, RESET_MS, sessionState, step, type Memory } from "./state";

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

  describe("reset (a janela de limite que reiniciou), como na telinha", () => {
    const H = 3_600_000;
    const T0 = Date.parse("2026-11-16T10:00:00Z");
    const janela = (id: string, t: number, extra: object = {}) => ({ id, label: id, used_percent: 10, resets_at: new Date(t).toISOString(), ...extra });
    const com = (state: string, ...windows: object[]) => p(state, { quotas: { source: "statusline", fetched_at: null, stale: false, windows } });
    /** Passos seguidos: [ms, retrato]; devolve a memória. */
    const passos = (...ps: [number, Parameters<typeof step>[1]][]) => ps.reduce<Memory>((m, [t, r]) => step(m, r, t), EMPTY_MEMORY);

    it("reset isolado: RESET_MS por cima e volta ao estado de base", () => {
      const m = passos([0, com("ocioso", janela("5h", T0))], [1000, com("ocioso", janela("5h", T0 + 5 * H))]);
      expect(current(m, 1000)).toBe("reset");
      expect(current(m, 1000 + RESET_MS - 1)).toBe("reset");
      expect(current(m, 1000 + RESET_MS)).toBe("ocioso"); // expira em RESET_MS exatos
      expect(nextChange(m, 1000)).toBe(1000 + RESET_MS);
      expect(nextChange(m, 1000 + RESET_MS)).toBeNull();
    });

    it("reset junto com o fim do trabalho: o reset fica os 3 s e depois vem o concluído", () => {
      const m = passos([0, com("trabalhando", janela("5h", T0))], [1000, com("ocioso", janela("5h", T0 + 5 * H))]);
      expect(current(m, 1000)).toBe("reset");
      expect(current(m, 1000 + RESET_MS - 1)).toBe("reset");
      expect(current(m, 1000 + RESET_MS)).toBe("concluido");
      expect(current(m, 1000 + DONE_MS)).toBe("ocioso"); // o concluído conta do fim do trabalho, não do fim do reset
      expect(nextChange(m, 1000)).toBe(1000 + RESET_MS); // um prazo de cada vez: primeiro o do reset...
      expect(nextChange(m, 1000 + RESET_MS)).toBe(1000 + DONE_MS); // ...depois o do concluído
    });

    it("do alerta com uma sessão trabalhando para parado, junto com o reset: reset e depois concluído (o --demo)", () => {
      const m = passos([0, com("alerta", janela("5h", T0))], [1000, com("ocioso", janela("5h", T0 + 5 * H))]);
      expect(passos([0, { ...com("alerta", janela("5h", T0)), working: 1 }]).working).toBe(true);
      const m1 = passos([0, { ...com("alerta", janela("5h", T0)), working: 1 }], [1000, com("ocioso", janela("5h", T0 + 5 * H))]);
      expect(current(m1, 1000)).toBe("reset");
      expect(current(m1, 1000 + RESET_MS)).toBe("concluido");
      // sem sessão trabalhando no alerta, nada terminou: reset e depois ocioso
      expect(current(m, 1000 + RESET_MS)).toBe("ocioso");
    });

    it("o trabalho termina durante o reset: concluído quando o reset acaba", () => {
      const m = passos(
        [0, com("trabalhando", janela("5h", T0))],
        [1000, com("trabalhando", janela("5h", T0 + 5 * H))],
        [2000, com("ocioso", janela("5h", T0 + 5 * H))],
      );
      expect(current(m, 2000)).toBe("reset");
      expect(current(m, 1000 + RESET_MS)).toBe("concluido");
    });

    it("o concluído não volta se outro estado chegou durante o reset", () => {
      const m = passos(
        [0, com("trabalhando", janela("5h", T0))],
        [1000, com("ocioso", janela("5h", T0 + 5 * H))],
        [2000, com("trabalhando", janela("5h", T0 + 5 * H))],
      );
      expect(current(m, 2000)).toBe("reset");
      expect(current(m, 1000 + RESET_MS)).toBe("trabalhando");
    });

    it("retratos repetidos não recomeçam o reset nem o prolongam", () => {
      let m = passos([0, com("ocioso", janela("5h", T0))], [1000, com("ocioso", janela("5h", T0 + 5 * H))]);
      for (let t = 1100; t < 1000 + RESET_MS; t += 100) m = step(m, com("ocioso", janela("5h", T0 + 5 * H)), t);
      expect(m.resetUntil).toBe(1000 + RESET_MS);
      expect(current(m, 1000 + RESET_MS)).toBe("ocioso");
      m = step(m, com("ocioso", janela("5h", T0 + 5 * H)), 9000);
      expect(current(m, 9000)).toBe("ocioso");
    });

    it("as duas janelas reiniciando juntas são um reset só", () => {
      const m = passos(
        [0, com("ocioso", janela("5h", T0), janela("7d", T0 + 24 * H))],
        [1000, com("ocioso", janela("5h", T0 + 5 * H), janela("7d", T0 + 7 * 24 * H))],
      );
      expect(m.resetUntil).toBe(1000 + RESET_MS);
    });

    it("reconexão: a fonte (ou o ombrod) some e volta com outro horário; não reapresenta reset antigo", () => {
      const m = passos(
        [0, com("ocioso", janela("5h", T0))],
        [1000, p("ocioso")], // sem quotas: o ombrod caiu ou a fonte sumiu
        [2000, com("ocioso", janela("5h", T0 + 5 * H))],
      );
      expect(current(m, 2000)).toBe("ocioso");
      // e o app reaberto (memória nova, como o useMascotState faz no primeiro retrato) só lembra
      const novo = { ...step(EMPTY_MEMORY, com("trabalhando", janela("5h", T0 + 5 * H)), 3000), resetUntil: null, doneUntil: null };
      expect(current(novo, 3000)).toBe("trabalhando");
    });

    it("o ciclo da demonstração (2h14 → 30 min → 5 h) reseta a cada volta", () => {
      let m = EMPTY_MEMORY;
      for (let ciclo = 0; ciclo < 3; ciclo++) {
        const t = ciclo * 48_000;
        m = step(m, com("ocioso", janela("5h", T0 + t + 2 * H)), t);
        m = step(m, { ...com("alerta", janela("5h", T0 + t + 0.5 * H)), working: 1 }, t + 32_000);
        m = step(m, com("ocioso", janela("5h", T0 + t + 5 * H)), t + 40_000);
        expect(current(m, t + 40_000)).toBe("reset");
        expect(current(m, t + 40_000 + RESET_MS)).toBe("concluido"); // a sessão do alerta terminou
      }
    });

    it("janela expirada sem número novo: o reset vem uma vez, quando o número novo chega", () => {
      const m0 = passos([0, com("ocioso", janela("5h", T0))], [1000, com("ocioso", janela("5h", T0, { used_percent: null, expired: true }))]);
      expect(current(m0, 1000)).toBe("ocioso");
      const m1 = step(m0, com("ocioso", janela("5h", T0 + 5 * H)), 2000);
      expect(current(m1, 2000)).toBe("reset");
    });

    it("sem reset: o horário andou menos de 1 h, ou não há janelas", () => {
      const m = passos([0, com("ocioso", janela("5h", T0))], [1000, com("ocioso", janela("5h", T0 + H))]);
      expect(current(m, 1000)).toBe("ocioso");
      expect(m.resetUntil).toBeNull();
      expect(current(passos([0, p("trabalhando")], [1000, p("ocioso")]), 1000)).toBe("concluido");
    });

    it("o reset fica por cima de esperando, alerta e trabalhando, e eles voltam quando ele acaba", () => {
      for (const st of ["aguardando", "aprovacao", "alerta", "trabalhando"] as const) {
        const m = passos([0, com(st, janela("5h", T0))], [1000, com(st, janela("5h", T0 + 5 * H))]);
        expect(current(m, 1000)).toBe("reset");
        expect(current(m, 1000 + RESET_MS)).toBe(baseState(p(st)));
      }
    });

    it("as outras prioridades seguem: esperando, alerta e trabalhando apagam o concluído", () => {
      // [estado que chega, o que se vê com ele, o que se vê quando volta ao ocioso]
      const casos = [
        ["aguardando", "esperando", "ocioso"], // de esperando direto para parado: você cancelou
        ["alerta", "alerta", "ocioso"],
        ["trabalhando", "trabalhando", "concluido"], // terminou de novo: um concluído novo
      ] as const;
      for (const [st, com_ele, depois] of casos) {
        const m = passos([0, p("trabalhando")], [1000, p("ocioso")], [2000, p(st)]);
        expect(current(m, 2000)).toBe(com_ele);
        expect(m.doneUntil).toBeNull();
        expect(current(step(m, p("ocioso"), 3000), 3000)).toBe(depois);
      }
    });
  });

  it("sessões viram a pose do estado delas", () => {
    expect(sessionState("working")).toBe("trabalhando");
    expect(sessionState("approval")).toBe("esperando");
    expect(sessionState("idle")).toBe("ocioso");
  });
});
