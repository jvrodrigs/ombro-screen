import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as mq from "mascotinhos";
import type { Mascotinho } from "mascotinhos";
import { AvatarView } from "./AvatarView";
import { catalog } from "./catalog";
import { CICLO_RGB_S, CoresContext, corDoCiclo, entradaDeValor, hexDe, ligarCorDoCorpo, normalizarHex, valorDeEntrada, type EntradaCor } from "./cores";

const montados: Mascotinho[] = [];
vi.mock("mascotinhos", async (original) => {
  const m = await original<typeof import("mascotinhos")>();
  return {
    ...m,
    montar: vi.fn((el: Element, op?: import("mascotinhos").OpcoesMontar) => {
      const p = m.montar(el, op);
      montados.push(p);
      return p;
    }),
  };
});

const av = (id: string) => catalog().byId.get(id)!;
const IDS = catalog().avatars.map((a) => a.id);
const RGB: EntradaCor = { modo: "rgb" };

describe("o ciclo de cores", () => {
  it("volta a cada 12 s, com os mesmos valores do mascote.py (tests/test_cores_avatares.py)", () => {
    expect(CICLO_RGB_S).toBe(12);
    const esperado: Record<number, [number, number, number]> = {
      0: [231, 64, 64], 1: [231, 148, 64], 3: [148, 231, 64], 6: [64, 231, 231], 9: [148, 64, 231], 12: [231, 64, 64], 30: [64, 231, 231],
    };
    for (const [t, cor] of Object.entries(esperado)) expect(corDoCiclo(Number(t)), `t=${t}`).toEqual(cor);
    expect(corDoCiclo(-3)).toEqual(corDoCiclo(9)); // o relógio nunca é negativo, mas a conta não quebra
  });

  it("lê e normaliza o valor do ombrod", () => {
    expect(normalizarHex("3d7a4f")).toBe("#3D7A4F");
    expect(normalizarHex(" #3d7a4f ")).toBe("#3D7A4F");
    for (const ruim of ["", "#3d7a4", "#3d7a4fa", "verde", "#ggg000", "#3d7a4g"]) expect(normalizarHex(ruim), ruim).toBeNull();
    expect(entradaDeValor("default")).toEqual({ modo: "default" });
    expect(entradaDeValor("RGB")).toEqual({ modo: "rgb" });
    expect(entradaDeValor("#abcdef")).toEqual({ modo: "custom", cor: "#ABCDEF" });
    for (const ruim of [null, undefined, 12, true, "azul", {}]) expect(entradaDeValor(ruim)).toBeNull();
    expect(valorDeEntrada({ modo: "custom", cor: "#ABCDEF" })).toBe("#ABCDEF");
    expect(valorDeEntrada(RGB)).toBe("rgb");
    expect(hexDe([1, 2, 255])).toBe("#0102FF");
  });

  it("ligarCorDoCorpo resolve pela forma desenhada, para com o reduzir movimento e se desfaz", () => {
    class Falso {
      cor: number[] = [10, 20, 30];
      forma = { avatar: "a" };
      corAtual() {
        return [9, 9, 9];
      }
    }
    const animador = new Falso();
    const fake = { animador } as unknown as Mascotinho;
    const mapa: Record<string, EntradaCor> = { a: RGB, b: { modo: "custom", cor: "#112233" } };
    let t = 3;
    let reduzido = false;
    const desligar = ligarCorDoCorpo(fake, (id) => mapa[id], () => t, () => reduzido);
    const corAtual = animador.corAtual as (agora: number) => number[];
    expect(corAtual(99)).toEqual(corDoCiclo(3)); // o `agora` do estado não conta: só o relógio do ciclo
    t = 15;
    expect(corAtual(0)).toEqual(corDoCiclo(3)); // 15 s = 3 s: uma volta a cada 12 s
    reduzido = true;
    expect(corAtual(0)).toEqual([10, 20, 30]); // parado na cor-base
    expect(animador.cor).toEqual([10, 20, 30]);
    animador.forma = { avatar: "b" }; // a forma desenhada mudou: vale a entrada dela, também no player parado (lê `cor`)
    expect(corAtual(0)).toEqual([17, 34, 51]);
    expect(animador.cor).toEqual([17, 34, 51]);
    animador.cor = [1, 2, 3]; // o Animador grava a cor-base: não aparece enquanto o avatar tem cor própria
    expect(animador.cor).toEqual([17, 34, 51]);
    animador.forma = { avatar: "c" }; // sem entrada: a biblioteca (o corAtual de antes) e a cor-base nova
    expect(corAtual(0)).toEqual([9, 9, 9]);
    expect(animador.cor).toEqual([1, 2, 3]);
    desligar();
    expect(Object.prototype.hasOwnProperty.call(animador, "corAtual")).toBe(false);
    expect(animador.cor).toEqual([1, 2, 3]);
  });
});

describe("AvatarView com a cor do corpo", () => {
  let agora = 0;
  let fila = new Map<number, FrameRequestCallback>();
  let proximo = 1;
  const passo = (ms: number) => {
    agora += ms;
    const cbs = [...fila.values()];
    fila = new Map();
    for (const cb of cbs) cb(agora);
  };
  const reduzir = (sim: boolean) =>
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: sim && q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }));
  const corpo = (c: HTMLElement) => c.querySelector("svg path")!.getAttribute("fill")!.toUpperCase();

  beforeEach(() => {
    agora = 1000;
    fila = new Map();
    montados.length = 0;
    vi.spyOn(performance, "now").mockImplementation(() => agora);
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      fila.set(proximo, cb);
      return proximo++;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => fila.delete(id));
    reduzir(false);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("rgb: o corpo gira pelo relógio, sem timer, sem ajustar({cor}) por quadro e com um laço só", () => {
    const setIntervalo = vi.spyOn(globalThis, "setInterval");
    const { container, unmount } = render(<AvatarView definition={av("sereno").definition} color="#7FB77E" state="trabalhando" live entrada={RGB} />);
    const ajustar = vi.spyOn(montados[0], "ajustar");
    const vistas = new Set<string>();
    for (let i = 0; i < 12 * 30; i++) {
      passo(1000 / 30);
      vistas.add(corpo(container));
      expect(corpo(container), `quadro ${i}`).toBe(hexDe(corDoCiclo(performance.now() / 1000)));
    }
    expect(vistas.size).toBeGreaterThan(40); // girou por muitas cores numa volta
    expect(ajustar).not.toHaveBeenCalled();
    expect(setIntervalo).not.toHaveBeenCalled();
    expect(fila.size).toBe(1);
    unmount();
    expect(fila.size).toBe(0); // desmontou: o laço para e nada fica agendado
    expect(mq.montadosAgora()).toBe(0);
  });

  it("rgb: a fase não depende de quando o avatar ou o estado começou", () => {
    const a = render(<AvatarView definition={av("sereno").definition} color="#7FB77E" state="trabalhando" live entrada={RGB} />);
    passo(5000);
    const b = render(<AvatarView definition={av("cubo").definition} color="#7FB77E" state="ocioso" live entrada={RGB} />);
    passo(1000 / 30);
    expect(corpo(a.container)).toBe(corpo(b.container)); // todos na mesma fase
    a.unmount();
    b.unmount();
  });

  it("rgb com reduzir movimento: parado na cor-base, nada agendado, e o rgb salvo continua", () => {
    reduzir(true);
    const { container, unmount } = render(<AvatarView definition={av("sereno").definition} color="#7FB77E" state="trabalhando" live entrada={RGB} />);
    for (let i = 0; i < 30; i++) passo(100);
    expect(corpo(container)).toBe("#7FB77E");
    expect(fila.size).toBe(0);
    unmount();
  });

  it("alternar rgb, personalizada e padrão: cada volta é a cor certa, e o padrão volta ao original", () => {
    const def = av("sereno").definition;
    const { container, rerender, unmount } = render(<AvatarView definition={def} color="#7FB77E" state="ocioso" live entrada={null} />);
    passo(100);
    expect(corpo(container)).toBe("#7FB77E");
    rerender(<AvatarView definition={def} color="#7FB77E" state="ocioso" live entrada={RGB} />);
    passo(100);
    expect(corpo(container)).toBe(hexDe(corDoCiclo(performance.now() / 1000)));
    rerender(<AvatarView definition={def} color="#7FB77E" state="ocioso" live entrada={{ modo: "custom", cor: "#112233" }} />);
    passo(100);
    expect(corpo(container)).toBe("#112233");
    passo(3000);
    expect(corpo(container)).toBe("#112233"); // fixa: não gira
    rerender(<AvatarView definition={def} color="#7FB77E" state="ocioso" live entrada={{ modo: "default" }} />);
    passo(100);
    expect(corpo(container)).toBe("#7FB77E");
    expect(mq.montadosAgora()).toBe(1); // nunca montou de novo
    unmount();
  });

  it("personalizada no reduzir movimento: o player parado mostra a cor escolhida; rgb mostra a cor-base", () => {
    reduzir(true);
    const def = av("sereno").definition;
    const { container, rerender, unmount } = render(<AvatarView definition={def} color="#7FB77E" state="ocioso" live entrada={{ modo: "custom", cor: "#112233" }} />);
    expect(corpo(container)).toBe("#112233");
    rerender(<AvatarView definition={def} color="#7FB77E" state="ocioso" live entrada={RGB} />);
    expect(corpo(container)).toBe("#7FB77E");
    unmount();
  });

  it("troca de avatar: a cor é a da forma desenhada (a do destino só entra no ponto médio) e a transição não é cortada", () => {
    const sereno = av("sereno").definition;
    const cubo = av("cubo").definition;
    const { container, rerender, unmount } = render(<AvatarView definition={sereno} color="#7FB77E" state="ocioso" live entrada={null} />);
    passo(100);
    const ajustar = vi.spyOn(montados[0], "ajustar");
    const usarForma = vi.spyOn(montados[0], "usarForma");
    rerender(<AvatarView definition={cubo} color="#6FA8DC" state="ocioso" live entrada={{ modo: "custom", cor: "#AA0000" }} />);
    expect(usarForma).toHaveBeenCalledTimes(1);
    passo(1000 / 30);
    expect(montados[0].animador.forma.avatar).toBe("sereno");
    expect(corpo(container)).toBe("#7FB77E"); // ainda o corpo de antes, na cor de antes
    // o destino muda de cor no meio da troca: vale a mais nova, sem refazer nem cortar a transição
    rerender(<AvatarView definition={cubo} color="#6FA8DC" state="ocioso" live entrada={{ modo: "custom", cor: "#00AA00" }} />);
    for (let i = 0; i < 10; i++) passo(1000 / 30); // passa do ponto médio (0,3 s)
    expect(montados[0].animador.forma.avatar).toBe("cubo");
    expect(corpo(container)).toBe("#00AA00");
    for (let i = 0; i < 10; i++) passo(1000 / 30);
    expect(corpo(container)).toBe("#00AA00");
    expect(usarForma).toHaveBeenCalledTimes(1); // nenhuma troca nova
    expect(ajustar).not.toHaveBeenCalledWith(expect.objectContaining({ cor: expect.anything() }));
    unmount();
  });

  it("editar a cor do avatar de agora no meio da troca vale já e a transição segue; o destino rgb só começa no meio", () => {
    const sereno = av("sereno").definition;
    const cubo = av("cubo").definition;
    const mapa = (m: Record<string, EntradaCor>) => (
      <CoresContext.Provider value={m}>
        <AvatarView definition={cubo} color="#6FA8DC" state="ocioso" live />
      </CoresContext.Provider>
    );
    const { container, rerender, unmount } = render(
      <CoresContext.Provider value={{}}>
        <AvatarView definition={sereno} color="#7FB77E" state="ocioso" live />
      </CoresContext.Provider>,
    );
    passo(100);
    rerender(mapa({ cubo: RGB }));
    passo(1000 / 30);
    expect(montados[0].animador.forma.avatar).toBe("sereno");
    rerender(mapa({ cubo: RGB, sereno: { modo: "custom", cor: "#0000AA" } })); // o de agora, no meio da troca
    passo(1000 / 30);
    expect(corpo(container)).toBe("#0000AA");
    for (let i = 0; i < 10; i++) passo(1000 / 30);
    expect(montados[0].animador.forma.avatar).toBe("cubo");
    expect(corpo(container)).toBe(hexDe(corDoCiclo(performance.now() / 1000))); // o rgb do destino, a partir do meio
    unmount();
  });

  it("rgb não atrapalha a troca de forma em andamento", () => {
    const { container, rerender, unmount } = render(<AvatarView definition={av("sereno").definition} color="#7FB77E" state="ocioso" live entrada={RGB} />);
    passo(100);
    const ajustar = vi.spyOn(montados[0], "ajustar");
    const usarForma = vi.spyOn(montados[0], "usarForma");
    rerender(<AvatarView definition={av("cubo").definition} color="#7FB77E" state="ocioso" live entrada={RGB} />);
    expect(usarForma).toHaveBeenCalledTimes(1);
    for (let i = 0; i < 30; i++) passo(1000 / 30);
    expect(ajustar).not.toHaveBeenCalledWith(expect.objectContaining({ cor: expect.anything() }));
    expect(montados[0].animador.forma.avatar).toBe("cubo"); // a troca foi até o fim, no meio dela a forma mudou
    expect(corpo(container)).toBe(hexDe(corDoCiclo(performance.now() / 1000))); // e o ciclo seguiu girando
    unmount();
  });

  it("R8-01: mudar a cor-base do destino antes do ponto médio refaz o destino sem cancelar, voltar nem reiniciar a troca", () => {
    const sereno = av("sereno").definition;
    const cubo = av("cubo").definition;
    const { container, rerender, unmount } = render(<AvatarView definition={sereno} color="#7FB77E" state="ocioso" live />);
    passo(100);
    const a = montados[0].animador as unknown as { _troca: [unknown, unknown, number] | null; _trocou: boolean; forma: { avatar: string } };
    const ajustar = vi.spyOn(montados[0], "ajustar");
    rerender(<AvatarView definition={cubo} color="#6FA8DC" state="ocioso" live />);
    const inicio = a._troca![2];
    const formas: string[] = [];
    passo(1000 / 30);
    formas.push(a.forma.avatar);
    rerender(<AvatarView definition={cubo} color="#112233" state="ocioso" live />); // a base do destino muda antes do meio
    expect(a._troca).not.toBeNull(); // a troca segue
    expect(a._troca![2]).toBe(inicio); // a fase não reiniciou
    expect(a.forma.avatar).toBe("sereno");
    for (let i = 0; i < 12; i++) {
      passo(1000 / 30);
      formas.push(a.forma.avatar);
    }
    expect(formas).toEqual([...Array(formas.indexOf("cubo")).fill("sereno"), ...Array(formas.length - formas.indexOf("cubo")).fill("cubo")]); // sereno até o meio e cubo depois: nunca voltou
    expect(formas.indexOf("cubo")).toBeGreaterThan(0);
    expect(formas.indexOf("cubo") * (1000 / 30)).toBeLessThanOrEqual(0.4 * 1000); // o meio chegou no tempo de sempre (0,3 s)
    expect(corpo(container)).toBe("#112233"); // e o corpo novo está na cor-base nova
    expect(ajustar).not.toHaveBeenCalledWith(expect.objectContaining({ cor: expect.anything() }));
    for (let i = 0; i < 12; i++) passo(1000 / 30);
    expect(a._troca).toBeNull(); // terminou sozinha
    unmount();
  });

  it("R8-01: depois do ponto médio só a cor-base muda: a forma fica, a transição continua sem reiniciar", () => {
    const sereno = av("sereno").definition;
    const cubo = av("cubo").definition;
    const { container, rerender, unmount } = render(<AvatarView definition={sereno} color="#7FB77E" state="ocioso" live />);
    passo(100);
    const a = montados[0].animador as unknown as { _troca: [unknown, unknown, number] | null; _trocou: boolean; forma: { avatar: string } };
    rerender(<AvatarView definition={cubo} color="#6FA8DC" state="ocioso" live />);
    const troca = a._troca!;
    for (let i = 0; i < 10; i++) passo(1000 / 30); // passou do meio (0,3 s) e ainda não acabou (0,6 s)
    expect(a.forma.avatar).toBe("cubo");
    expect(a._trocou).toBe(true);
    expect(a._troca).toBe(troca);
    const ajustar = vi.spyOn(montados[0], "ajustar");
    const usarForma = vi.spyOn(montados[0], "usarForma");
    rerender(<AvatarView definition={cubo} color="#223344" state="ocioso" live />);
    expect(a._troca).not.toBeNull(); // a troca segue: o mesmo destino e o mesmo instante de início, só a cor-base nova
    expect(a._troca![0]).toBe(troca[0]);
    expect(a._troca![2]).toBe(troca[2]);
    expect(a._trocou).toBe(true);
    expect(a.forma.avatar).toBe("cubo");
    expect(usarForma).not.toHaveBeenCalled();
    expect(ajustar).not.toHaveBeenCalledWith(expect.objectContaining({ cor: expect.anything() }));
    passo(1000 / 30);
    expect(corpo(container)).toBe("#223344");
    for (let i = 0; i < 12; i++) passo(1000 / 30);
    expect(a._troca).toBeNull();
    expect(a.forma.avatar).toBe("cubo");
    unmount();
  });

  it("R8-01: ligar o reduzir movimento entre o ponto médio e o fim, logo após editar a cor-base, mantém a cor nova", () => {
    const sereno = av("sereno").definition;
    const cubo = av("cubo").definition;
    const { container, rerender, unmount } = render(<AvatarView definition={sereno} color="#7FB77E" state="ocioso" live />);
    passo(100);
    const a = montados[0].animador as unknown as { _troca: [unknown, number[], number] | null; _trocou: boolean; forma: { avatar: string }; corBase: number[] };
    rerender(<AvatarView definition={cubo} color="#6FA8DC" state="ocioso" live />);
    for (let i = 0; i < 10; i++) passo(1000 / 30); // depois do meio, antes do fim
    expect(a._trocou).toBe(true);
    expect(a._troca).not.toBeNull();
    rerender(<AvatarView definition={cubo} color="#223344" state="ocioso" live />);
    expect(a._troca![1]).toEqual([0x22, 0x33, 0x44]); // o destino pendente também tem a cor nova
    montados[0].ajustar({ reduzirMovimento: true }); // o desenho estático reaplica o destino pendente
    expect(a._troca).toBeNull();
    expect(a.forma.avatar).toBe("cubo");
    expect(a.corBase).toEqual([0x22, 0x33, 0x44]);
    expect(corpo(container)).toBe("#223344");
    montados[0].ajustar({ reduzirMovimento: false });
    passo(1000 / 30);
    expect(corpo(container)).toBe("#223344");
    unmount();
  });

  it("R8-01: reduzir movimento ligado antes do ponto médio, logo após editar a cor-base do destino, também mantém a cor nova", () => {
    const sereno = av("sereno").definition;
    const cubo = av("cubo").definition;
    const { container, rerender, unmount } = render(<AvatarView definition={sereno} color="#7FB77E" state="ocioso" live />);
    passo(100);
    rerender(<AvatarView definition={cubo} color="#6FA8DC" state="ocioso" live />);
    passo(1000 / 30);
    rerender(<AvatarView definition={cubo} color="#223344" state="ocioso" live />);
    montados[0].ajustar({ reduzirMovimento: true });
    expect(montados[0].animador.forma.avatar).toBe("cubo"); // parado: a troca termina na hora, no destino
    expect(corpo(container)).toBe("#223344");
    unmount();
  });

  it("R8-01: sem troca, a cor-base do mesmo avatar muda na hora; no reduzir movimento também", () => {
    for (const reduzido of [false, true]) {
      reduzir(reduzido);
      const sereno = av("sereno").definition;
      const { container, rerender, unmount } = render(<AvatarView definition={sereno} color="#7FB77E" state="ocioso" live />);
      passo(100);
      rerender(<AvatarView definition={sereno} color="#445566" state="ocioso" live />);
      passo(100);
      expect(corpo(container), `reduzido=${reduzido}`).toBe("#445566");
      expect(montados[0].animador.forma.avatar).toBe("sereno");
      unmount();
      montados.length = 0;
    }
  });

  it("R8-02: o rascunho de um avatar vale até a troca efetiva para outro, e edições simultâneas por id não se misturam", () => {
    const sereno = av("sereno").definition;
    const cubo = av("cubo").definition;
    const vista = (def: typeof sereno, entradas: Record<string, EntradaCor | null>) => <AvatarView definition={def} color="#7FB77E" state="ocioso" live entradas={entradas} />;
    const { container, rerender, unmount } = render(vista(sereno, { sereno: { modo: "custom", cor: "#AA0000" } }));
    passo(100);
    expect(corpo(container)).toBe("#AA0000");
    rerender(vista(cubo, { sereno: { modo: "custom", cor: "#AA0000" }, cubo: { modo: "custom", cor: "#00AA00" } })); // seleciona o Cubo
    passo(1000 / 30);
    expect(montados[0].animador.forma.avatar).toBe("sereno");
    expect(corpo(container)).toBe("#AA0000"); // ainda o Sereno desenhado: continua no rascunho dele
    rerender(vista(cubo, { sereno: { modo: "custom", cor: "#BB1111" }, cubo: { modo: "custom", cor: "#11BB11" } })); // os dois mudam juntos
    passo(1000 / 30);
    expect(corpo(container)).toBe("#BB1111"); // o do Sereno vale no Sereno
    for (let i = 0; i < 10; i++) passo(1000 / 30);
    expect(montados[0].animador.forma.avatar).toBe("cubo");
    expect(corpo(container)).toBe("#11BB11"); // e o do Cubo, só depois da troca efetiva
    unmount();
  });

  it("R8-05: default explícito mantém a cor-base em todo quadro (sem pulso); sem entrada, o realce de antes", () => {
    const def = av("sereno").definition;
    const base = "#7FB77E";
    const rodar = (entrada: EntradaCor | null) => {
      const { container, rerender, unmount } = render(<AvatarView definition={def} color={base} state="ocioso" live entrada={entrada} />);
      passo(1000);
      rerender(<AvatarView definition={def} color={base} state="trabalhando" live entrada={entrada} />); // troca de estado
      const cores = new Set<string>();
      for (let i = 0; i < 15; i++) {
        passo(1000 / 30);
        cores.add(corpo(container));
      }
      unmount();
      montados.length = 0;
      return cores;
    };
    expect([...rodar({ modo: "default" })]).toEqual([base]); // a cor-base em todo quadro
    const legado = rodar(null);
    expect(legado.size).toBeGreaterThan(1); // o pulso de meio segundo da biblioteca, como a telinha sem entrada
    expect(legado.has(base)).toBe(true);
  });

  it("o valor que o ombrod publica vale por id (sem entrada nem prop, nada muda)", () => {
    const { container } = render(<AvatarView definition={av("sereno").definition} color="#7FB77E" state="ocioso" />);
    expect(corpo(container)).toBe("#7FB77E");
  });

  it("só o corpo muda, nos doze avatares e nos dois pacotes (parado e animado)", () => {
    expect(IDS).toHaveLength(12);
    for (const pacote of ["vivo", "classico"] as const) {
      for (const id of IDS) {
        const def = av(id).definition;
        const base = render(<AvatarView definition={def} color="#7FB77E" state="esperando" pacote={pacote} />);
        const custom = render(<AvatarView definition={def} color="#7FB77E" state="esperando" pacote={pacote} entrada={{ modo: "custom", cor: "#AA2244" }} />);
        const igual = render(<AvatarView definition={def} color="#AA2244" state="esperando" pacote={pacote} />);
        // a cor personalizada é exatamente passar essa cor ao desenho: o resto (forma, olhos, extras, ids) é o mesmo
        expect(custom.container.innerHTML, `${id}/${pacote}`).toBe(igual.container.innerHTML);
        expect(custom.container.innerHTML).not.toBe(base.container.innerHTML);
        const semCor = (h: string) => h.replace(/#[0-9a-f]{6}/gi, "#");
        expect(semCor(custom.container.innerHTML), `${id}/${pacote}: só a cor do corpo muda`).toBe(semCor(base.container.innerHTML));
        base.unmount();
        custom.unmount();
        igual.unmount();
      }
    }
  });

  it("miniaturas e listas (paradas) ficam na cor-base no rgb, sem player nem laço", () => {
    const { container } = render(<AvatarView definition={av("pixel").definition} color="#7FB77E" size={44} entrada={RGB} />);
    expect(corpo(container)).toBe("#7FB77E");
    expect(montados).toHaveLength(0);
    expect(fila.size).toBe(0);
  });
});
