import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DONE_MS, RESET_MS } from "./state";
import { useMascotState } from "./useMascotState";

type Retrato = Parameters<typeof useMascotState>[0];
const H = 3_600_000;
const T0 = Date.parse("2026-11-16T10:00:00Z");
const retrato = (state: string, resets: number | null, working = 0): Retrato =>
  ({
    state,
    working,
    waiting: 0,
    errors: 0,
    quotas:
      resets === null
        ? null
        : { source: "statusline", fetched_at: null, stale: false, windows: [{ id: "5h", label: "5h", used_percent: 10, resets_at: new Date(resets).toISOString() }] },
  }) as Retrato;

describe("useMascotState: o reset no app, ao longo dos renders", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-19T12:00:00Z"));
  });
  afterEach(() => vi.useRealTimers());

  it("reset junto com o fim do trabalho: 3 s de reset, depois concluído, sozinho (sem retrato novo)", () => {
    const { result, rerender } = renderHook((p: Retrato) => useMascotState(p), { initialProps: retrato("trabalhando", T0, 1) });
    expect(result.current).toBe("trabalhando");
    rerender(retrato("ocioso", T0 + 5 * H));
    expect(result.current).toBe("reset");
    act(() => vi.advanceTimersByTime(RESET_MS - 100));
    expect(result.current).toBe("reset");
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe("concluido");
    act(() => vi.advanceTimersByTime(DONE_MS));
    expect(result.current).toBe("ocioso");
  });

  it("renders e retratos repetidos não recomeçam o reset; um único timer por vez", () => {
    const { result, rerender } = renderHook((p: Retrato) => useMascotState(p), { initialProps: retrato("ocioso", T0) });
    rerender(retrato("ocioso", T0 + 5 * H));
    expect(result.current).toBe("reset");
    for (let i = 0; i < 25; i++) {
      act(() => vi.advanceTimersByTime(100));
      rerender(retrato("ocioso", T0 + 5 * H)); // o mesmo retrato (objeto novo), como a cada providers.list
    }
    expect(vi.getTimerCount()).toBe(1);
    act(() => vi.advanceTimersByTime(RESET_MS - 2500 + 100));
    expect(result.current).toBe("ocioso"); // acabou no prazo do primeiro retrato, não foi prolongado
    expect(vi.getTimerCount()).toBe(0);
  });

  it("abrir o app (ou remontar o avatar) com o horário já novo não mostra reset", () => {
    const { result } = renderHook((p: Retrato) => useMascotState(p), { initialProps: retrato("ocioso", T0 + 5 * H) });
    expect(result.current).toBe("ocioso");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("reconexão: o ombrod some (sem quotas) e volta com outro horário; não reapresenta reset", () => {
    const { result, rerender } = renderHook((p: Retrato) => useMascotState(p), { initialProps: retrato("ocioso", T0) });
    rerender(retrato("ocioso", null));
    rerender(retrato("ocioso", T0 + 5 * H));
    expect(result.current).toBe("ocioso");
  });
});
