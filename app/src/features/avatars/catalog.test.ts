import { describe, expect, it } from "vitest";
import { exemplos, quadroSvg } from "mascotinhos";
import { biblioteca } from "./animacoes";
import { buildCatalog, catalog, chooseAvatar, colorFor, fnv1a } from "./catalog";

const NOVE = ["ombro-cloud", "sereno", "orbita", "cubo", "scout", "pixel", "chevron", "maestro", "nova"];
const IDS = [...NOVE, "bolota", "broto", "fantasma"];
const ESTADOS = ["ocioso", "trabalhando", "esperando", "alerta", "reset", "concluido"] as const;

describe("catálogo de avatares", () => {
  it("carrega os doze, todos aprovados pelo validador do mascotinhos", () => {
    const c = catalog();
    expect(c.invalid).toEqual([]);
    expect(c.avatars.map((a) => a.id)).toEqual(IDS); // os nove de antes primeiro, na mesma ordem
    expect(c.providerDefault).toEqual({ claude: "ombro-cloud", codex: "orbita", cursor: "cubo", opencode: "nova" });
    expect(c.defaultId).toBe("ombro-cloud");
    expect(c.pool).toEqual([...IDS].sort());
  });

  it("as doze formas são as do mascotinhos, com o nome e a cor sugerida dele", () => {
    const ex = exemplos();
    const c = catalog();
    expect(Object.keys(ex).sort()).toEqual([...IDS].sort());
    for (const a of c.avatars) {
      expect(a.definition.path, a.id).toBe(ex[a.id].path);
      expect(a.definition.olhos, a.id).toEqual(ex[a.id].olhos);
      expect(a.name, a.id).toBe(ex[a.id].nome);
    }
    expect(c.byId.get("bolota")!.color).toBe("#E07A57");
    expect(c.byId.get("broto")!.color).toBe("#7FB77E");
    expect(c.byId.get("fantasma")!.color).toBe("#6FA8DC");
  });

  it("uma definição inválida fica de fora, sem derrubar o resto", () => {
    const c = buildCatalog(
      { version: 1, default: "a", providers: {}, avatars: [{ id: "a", description: "", tags: [], color: "#ffffff", random: true }, { id: "b", description: "", tags: [], color: "#000000", random: true }, { id: "c", description: "", tags: [], color: "#000000", random: true }] },
      { a: { version: 1, id: "a", name: "A", body: { path: "M20 20H80V80H20Z" }, eyes: { x: 50, y: 50, spacing: 15, scale: 1 } }, b: { version: 1, id: "b", name: "B", body: { path: "M0 0" }, eyes: { x: 1, y: 1, spacing: 1, scale: 1 }, color: "red" } },
    );
    expect(c.avatars.map((a) => a.id)).toEqual(["a"]);
    expect(c.invalid.map((i) => i.id)).toEqual(["b", "c"]); // c: sem o arquivo
  });

  it("os doze desenham com o mascotinhos em todos os estados", () => {
    for (const a of catalog().avatars) {
      for (const e of ESTADOS) {
        const svg = quadroSvg(a.definition, e, 1.2, { cor: a.color, biblioteca: biblioteca(), enquadre: "corpo" });
        expect(svg, `${a.id}/${e}`).toContain("<svg");
        expect(svg, `${a.id}/${e}`).toContain(`fill="${a.color.toLowerCase()}"`); // o corpo na cor do avatar
      }
    }
  });
});

describe("a mesma escolha do backend (avatares.py)", () => {
  it("FNV-1a igual ao do Python", () => {
    const vetores: Record<string, number> = { "": 2166136261, a: 3826002220, "ombro:0": 4140119464, "codex:abc-123": 3675777911, "claude:sessão-ç": 3870169681, "cursor:4294967295": 3203207324 };
    for (const [texto, h] of Object.entries(vetores)) expect(fnv1a(texto)).toBe(h);
  });

  it("aleatório dá o mesmo avatar que a telinha (com os doze no sorteio)", () => {
    const c = catalog();
    expect(chooseAvatar(c, "aleatorio", "codex", "sessao", 0, "s-1").id).toBe("broto");
    expect(chooseAvatar(c, "aleatorio", "codex", "sessao", 0, "s-2").id).toBe("fantasma");
    expect(chooseAvatar(c, "aleatorio", "claude", "inicio", 12345).id).toBe("chevron");
    expect(chooseAvatar(c, "aleatorio", null, "inicio", 7).id).toBe("pixel");
  });

  it("padrão, específico e o que não existe", () => {
    const c = catalog();
    expect(chooseAvatar(c, "padrao", "codex").id).toBe("orbita");
    expect(chooseAvatar(c, undefined, "cursor").id).toBe("cubo");
    expect(chooseAvatar(c, "sereno", "claude").id).toBe("sereno");
    expect(chooseAvatar(c, "fantasma", "claude").id).toBe("fantasma");
    expect(chooseAvatar(c, "apagado", "cursor").id).toBe("cubo");
    expect(chooseAvatar(c, "padrao", null).id).toBe("ombro-cloud");
  });

  it("cor do agente, e a do avatar quando não há agente", () => {
    const c = catalog();
    expect(colorFor(c, "codex", c.byId.get("nova")!)).toBe("#83B596");
    expect(colorFor(c, null, c.byId.get("nova")!)).toBe("#E7C25E");
  });
});
