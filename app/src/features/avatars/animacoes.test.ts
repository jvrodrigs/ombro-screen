import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as mq from "mascotinhos";
import type { Mascotinho } from "mascotinhos";
import { biblioteca, DO_CLASSICO, FIM_DA_CONCLUSAO, momentoSeguro, PACOTE, PACOTES, pacoteValido, trocarPacote } from "./animacoes";
import { catalog } from "./catalog";
import type { MascotState } from "./state";

const STATES: MascotState[] = ["ocioso", "trabalhando", "esperando", "alerta", "reset", "concluido"];

const forma = () => catalog().byId.get("bolota")!.definition;

describe("os pacotes", () => {
  it("vivo é o padrão; só vivo e clássico dá para escolher (3D e Terminal em avaliação)", () => {
    expect(PACOTE).toBe("vivo");
    expect([...PACOTES]).toEqual(["vivo", "classico"]);
    expect(pacoteValido("classico")).toBe("classico");
    for (const fora of ["3d", "terminal", undefined, null, 7, "VIVO"]) expect(pacoteValido(fora)).toBe("vivo");
  });

  it("cada pacote tem os estados do Ombro, com alerta e reset do clássico", () => {
    const classico = mq.bibliotecaDoPacote("classico");
    for (const p of PACOTES) {
      const b = biblioteca(p);
      expect(biblioteca(p)).toBe(b); // montada uma vez
      const dela = mq.bibliotecaDoPacote(p);
      for (const e of STATES) {
        const fonte = DO_CLASSICO.includes(e) ? classico : dela;
        for (const t of [0, 0.7, 2.3, 6.1]) expect(b.parametros(e, t), `${p} ${e} ${t}`).toEqual(fonte.parametros(e, t));
      }
    }
    expect(biblioteca("vivo").parametros("ocioso", 1.3)).not.toEqual(biblioteca("classico").parametros("ocioso", 1.3));
  });

  it("um pacote que não carrega cai no padrão, sem derrubar o mascote", async () => {
    vi.resetModules();
    const real = await vi.importActual<typeof import("mascotinhos")>("mascotinhos");
    vi.doMock("mascotinhos", () => ({
      ...real,
      bibliotecaDoPacote: (p: string) => {
        if (p === "classico" && quebrar) throw new Error("dados sumiram");
        return real.bibliotecaDoPacote(p);
      },
    }));
    let quebrar = false;
    const m = await import("./animacoes");
    const vivo = m.biblioteca("vivo"); // monta o vivo (com o clássico) antes de quebrar
    quebrar = true;
    const aviso = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(m.biblioteca("classico")).toBe(vivo);
    expect(aviso).toHaveBeenCalledTimes(1);
    vi.doUnmock("mascotinhos");
    vi.resetModules();
  });
});

// um player de verdade, com um relógio de mentira (performance.now) e sem laço (requestAnimationFrame parado)
describe("a troca de pacote no player", () => {
  let agora = 0;
  const players: Mascotinho[] = [];
  const passar = (s: number) => (agora += s * 1000);
  const montar = (estado = "ocioso") => {
    const p = mq.montar(document.createElement("div"), { forma: forma(), cor: "#E07A57", estado, biblioteca: biblioteca("vivo"), reduzirMovimento: false });
    players.push(p);
    return p;
  };
  const pose = (p: Mascotinho) => p.animador.parametrosAtuais(p.relogio());

  beforeEach(() => {
    agora = 5000;
    vi.spyOn(performance, "now").mockImplementation(() => agora);
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });
  afterEach(() => {
    for (const p of players.splice(0)) p.destruir();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("o momento seguro: ocioso ou alerta assentados, o concluído depois da comemoração; nunca trabalhando, esperando ou reset", () => {
    const p = montar("ocioso");
    passar(1);
    expect(momentoSeguro(p)).toBe(true);
    for (const e of ["trabalhando", "esperando", "reset"]) {
      p.irPara(e);
      passar(30);
      expect(momentoSeguro(p), e).toBe(false);
    }
    p.irPara("concluido");
    passar(FIM_DA_CONCLUSAO - 0.1);
    expect(momentoSeguro(p)).toBe(false); // ainda comemorando
    passar(0.1);
    expect(momentoSeguro(p)).toBe(true);
    p.irPara("ocioso");
    passar(0.2);
    expect(momentoSeguro(p)).toBe(false); // no meio da mistura entre estados
    passar(0.3);
    expect(momentoSeguro(p)).toBe(true);
    p.irPara("alerta");
    passar(1);
    expect(momentoSeguro(p)).toBe(true);
  });

  it("troca misturando: nada salta, o estado não recomeça e nenhum extra some", () => {
    for (const estado of ["ocioso", "concluido"]) {
      const p = montar(estado);
      passar(7.3);
      const [antes] = pose(p);
      const inicio = p.animador.inicio;
      const ajustar = vi.spyOn(p, "ajustar");
      trocarPacote(p, biblioteca("classico"));
      expect(ajustar).toHaveBeenCalledWith({ biblioteca: biblioteca("classico") }); // a API do player
      expect(momentoSeguro(p)).toBe(false); // trocando
      const [logo, u] = pose(p);
      expect(logo).toEqual(antes); // no instante da troca, a pose é a de antes
      expect(u).toBe(1); // e os extras (já assentados no estado) seguem inteiros: nada pisca
      let anterior = logo;
      for (let i = 0; i < 10; i++) {
        passar(0.05);
        const [agoraP] = pose(p);
        for (const k of ["dx", "dy", "rot"] as const) expect(Math.abs(agoraP[k] - anterior[k]), `${estado} ${k}`).toBeLessThan(2.5);
        anterior = agoraP;
      }
      passar(0.01);
      const [depois, u2] = pose(p);
      expect(depois).toEqual(biblioteca("classico").parametros(estado, p.relogio() - inicio));
      expect(u2).toBe(1);
      expect(Object.prototype.hasOwnProperty.call(p.animador, "parametrosAtuais")).toBe(false); // voltou ao da biblioteca
      expect(p.animador.inicio).toBe(inicio);
      expect(p.estado).toBe(estado);
      passar(1);
      expect(momentoSeguro(p)).toBe(true);
    }
  });

  it("trocar para a mesma biblioteca não faz nada", () => {
    const p = montar();
    const ajustar = vi.spyOn(p, "ajustar");
    trocarPacote(p, biblioteca("vivo"));
    expect(ajustar).not.toHaveBeenCalled();
  });
});
