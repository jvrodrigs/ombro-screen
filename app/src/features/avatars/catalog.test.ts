import { describe, expect, it } from "vitest";
import { renderAvatarSvg } from "open-bot-avatar";
import { buildCatalog, catalog, chooseAvatar, colorFor, fnv1a, moodFor } from "./catalog";

const IDS = ["ombro-cloud", "sereno", "orbita", "cubo", "scout", "pixel", "chevron", "maestro", "nova"];

describe("catálogo de avatares", () => {
  it("carrega os nove, todos aprovados pelo validador do open-bot-avatar", () => {
    const c = catalog();
    expect(c.invalid).toEqual([]);
    expect(c.avatars.map((a) => a.id)).toEqual(IDS);
    expect(c.providerDefault).toEqual({ claude: "ombro-cloud", codex: "orbita", cursor: "cubo" });
    expect(c.defaultId).toBe("ombro-cloud");
    expect(c.pool).toEqual([...IDS].sort());
  });

  it("uma definição inválida fica de fora, sem derrubar o resto", () => {
    const c = buildCatalog(
      { version: 1, default: "a", providers: {}, avatars: [{ id: "a", description: "", tags: [], color: "#ffffff", random: true }, { id: "b", description: "", tags: [], color: "#000000", random: true }] },
      { a: { version: 1, id: "a", name: "A", body: { path: "M20 20H80V80H20Z" }, eyes: { x: 50, y: 50, spacing: 15, scale: 1 } }, b: { version: 1, id: "b", name: "B", body: { path: "M0 0" }, eyes: { x: 1, y: 1, spacing: 1, scale: 1 }, color: "red" } },
    );
    expect(c.avatars.map((a) => a.id)).toEqual(["a"]);
    expect(c.invalid[0].id).toBe("b");
  });

  it("todos desenham com o motor, em todos os humores", () => {
    for (const a of catalog().avatars) {
      for (const motion of ["idle", "thinking", "happy"] as const) {
        expect(renderAvatarSvg(a.definition, { color: a.color, motion, size: 64 })).toContain("<svg");
      }
    }
  });
});

describe("a mesma escolha do backend (avatares.py)", () => {
  it("FNV-1a igual ao do Python", () => {
    const vetores: Record<string, number> = { "": 2166136261, a: 3826002220, "ombro:0": 4140119464, "codex:abc-123": 3675777911, "claude:sessão-ç": 3870169681, "cursor:4294967295": 3203207324 };
    for (const [texto, h] of Object.entries(vetores)) expect(fnv1a(texto)).toBe(h);
  });

  it("aleatório dá o mesmo avatar que a telinha", () => {
    const c = catalog();
    expect(chooseAvatar(c, "aleatorio", "codex", "sessao", 0, "s-1").id).toBe("scout");
    expect(chooseAvatar(c, "aleatorio", "codex", "sessao", 0, "s-2").id).toBe("cubo");
    expect(chooseAvatar(c, "aleatorio", "claude", "inicio", 12345).id).toBe("sereno");
    expect(chooseAvatar(c, "aleatorio", null, "inicio", 7).id).toBe("chevron");
  });

  it("padrão, específico e o que não existe", () => {
    const c = catalog();
    expect(chooseAvatar(c, "padrao", "codex").id).toBe("orbita");
    expect(chooseAvatar(c, undefined, "cursor").id).toBe("cubo");
    expect(chooseAvatar(c, "sereno", "claude").id).toBe("sereno");
    expect(chooseAvatar(c, "apagado", "cursor").id).toBe("cubo");
    expect(chooseAvatar(c, "padrao", null).id).toBe("ombro-cloud");
  });

  it("cor do agente, e a do avatar quando não há agente", () => {
    const c = catalog();
    expect(colorFor(c, "codex", c.byId.get("nova")!)).toBe("#83B596");
    expect(colorFor(c, null, c.byId.get("nova")!)).toBe("#E7C25E");
  });

  it("estados do mascote viram os humores do motor", () => {
    expect(moodFor("trabalhando")).toBe("thinking");
    expect(moodFor("concluido")).toBe("happy");
    expect(moodFor("esperando")).toBe("idle");
    expect(moodFor(undefined)).toBe("idle");
  });
});
