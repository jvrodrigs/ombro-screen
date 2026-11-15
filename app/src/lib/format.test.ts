import { describe, expect, it } from "vitest";
import { ago, level, percent, providerBadge, tokens, until } from "./format";

const agora = new Date(2026, 10, 15, 14, 0, 0);

describe("formatação", () => {
  it("tempo relativo", () => {
    expect(ago("2026-11-15T13:59:55", agora)).toBe("agora");
    expect(ago("2026-11-15T13:57:00", agora)).toBe("há 3 min");
    expect(ago("2026-11-15T11:00:00", agora)).toBe("há 3 h");
    expect(ago(null, agora)).toBe("—");
    expect(until("2026-11-15T16:14:00", agora)).toBe("em 2 h 14 min");
    expect(until("2026-11-17T19:00:00", agora)).toBe("em 2 d 5 h");
  });

  it("ausente é traço, nunca zero", () => {
    expect(percent(null)).toBe("—");
    expect(tokens(undefined)).toBe("—");
    expect(percent(0)).toBe("0%");
  });

  it("tokens legíveis", () => {
    expect(tokens(18_612_000)).toBe("18,6 M");
    expect(tokens(212_000)).toBe("212 mil");
    expect(tokens(950)).toBe("950");
  });

  it("os mesmos limites da telinha (75 e 90)", () => {
    expect(level(74.9)).toBe("normal");
    expect(level(75)).toBe("warning");
    expect(level(90)).toBe("critical");
    expect(level(null)).toBe("normal");
  });

  it("estado do provider separa desligado e erro de coleta", () => {
    expect(providerBadge({ id: "codex", enabled: false }).label).toBe("Desligado");
    expect(providerBadge({ id: "codex", enabled: true, collection: "error", sessions: 0 }).tone).toBe("critical");
    expect(providerBadge({ id: "claude", enabled: true, state: "aguardando" }).label).toBe("Esperando você");
  });
});
