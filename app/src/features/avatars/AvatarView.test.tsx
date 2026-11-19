import { StrictMode } from "react";
import { render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as mq from "mascotinhos";
import type { Mascotinho } from "mascotinhos";
import { renderToStaticMarkup } from "react-dom/server";
import { biblioteca, FPS, PROPORCAO_PALCO } from "./animacoes";
import { act } from "@testing-library/react";
import { AvatarView } from "./AvatarView";
import { catalog } from "./catalog";
import baseCss from "../../styles/base.css?raw";

// os arquivos de fontes/ que existem (o glob só devolve o que está no disco)
const FONTES = import.meta.glob("../../../../fontes/{JetBrainsMono-Bold.ttf,OFL.txt}", { query: "?url", import: "default", eager: true });

// o player de verdade, só observado: cada montagem fica guardada para os testes olharem
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

const montar = vi.mocked(mq.montar);
const av = (id: string) => catalog().byId.get(id)!;

beforeEach(() => {
  montados.length = 0;
  montar.mockClear();
});
afterEach(() => vi.restoreAllMocks());

describe("AvatarView animado (o player do mascotinhos)", () => {
  it("monta uma vez, com a forma, a cor, o estado e as animações da telinha", () => {
    const { container } = render(<AvatarView definition={av("bolota").definition} color="#E07A57" state="trabalhando" live />);
    expect(montar).toHaveBeenCalledTimes(1);
    const op = montar.mock.calls[0][1]!;
    expect(op.forma).toBe(av("bolota").definition);
    expect(op.cor).toBe("#E07A57");
    expect(op.estado).toBe("trabalhando");
    expect(op.biblioteca).toBe(biblioteca()); // o vivo, com alerta e reset do clássico
    expect(op.fps).toBe(30); // o limite do app, pela opção da própria biblioteca
    expect(FPS).toBe(30);
    expect(op.enquadre).toBe("corpo"); // o padrão: a caixa justa no corpo (cartões, miniaturas, lista)
    expect(op.reduzirMovimento).toBeUndefined(); // segue o "reduzir movimento" do sistema
    expect(op).not.toHaveProperty("followPointer");
    expect(container.querySelector(".avatar-live svg")).not.toBeNull();
    expect(mq.montadosAgora()).toBe(1);
  });

  it("trocar estado, avatar e cor vira transição da biblioteca, sem montar de novo", () => {
    const { rerender, container } = render(<AvatarView definition={av("bolota").definition} color="#E07A57" state="ocioso" live />);
    const p = montados[0];
    const svg = container.querySelector("svg");
    const irPara = vi.spyOn(p, "irPara");
    const usarForma = vi.spyOn(p, "usarForma");
    const ajustar = vi.spyOn(p, "ajustar");

    rerender(<AvatarView definition={av("bolota").definition} color="#E07A57" state="esperando" live />);
    expect(irPara).toHaveBeenLastCalledWith("esperando");
    expect(p.estado).toBe("esperando");

    rerender(<AvatarView definition={av("fantasma").definition} color="#6FA8DC" state="esperando" live />);
    expect(usarForma).toHaveBeenLastCalledWith(av("fantasma").definition, "#6FA8DC");

    rerender(<AvatarView definition={av("fantasma").definition} color="#123456" state="esperando" live />);
    expect(ajustar).toHaveBeenLastCalledWith({ cor: "#123456" });

    rerender(<AvatarView definition={av("fantasma").definition} color="#123456" state="esperando" live extras={false} />);
    expect(ajustar).toHaveBeenLastCalledWith({ extras: false });

    expect(irPara).toHaveBeenCalledTimes(1); // nada de chamada repetida com as mesmas props
    expect(montar).toHaveBeenCalledTimes(1);
    expect(container.querySelector("svg")).toBe(svg); // o mesmo <svg>: só o que mudou vai para o DOM
  });

  it("re-render com as mesmas props não mexe no player", () => {
    const el = <AvatarView definition={av("broto").definition} color="#7FB77E" state="concluido" live />;
    const { rerender } = render(el);
    const p = montados[0];
    const espias = [vi.spyOn(p, "irPara"), vi.spyOn(p, "usarForma"), vi.spyOn(p, "ajustar")];
    rerender(<AvatarView definition={av("broto").definition} color="#7FB77E" state="concluido" live />);
    for (const e of espias) expect(e).not.toHaveBeenCalled();
  });

  it("desmontar destrói o player e tira o <svg>", () => {
    const { unmount, container } = render(<AvatarView definition={av("nova").definition} color="#E7C25E" live />);
    const destruir = vi.spyOn(montados[0], "destruir");
    unmount();
    expect(destruir).toHaveBeenCalledTimes(1);
    expect(container.querySelector("svg")).toBeNull();
    expect(mq.montadosAgora()).toBe(0); // nada fica no laço da biblioteca
  });

  it("StrictMode (monta, desmonta, monta): fica um player só", () => {
    const { unmount, container } = render(
      <StrictMode>
        <AvatarView definition={av("cubo").definition} color="#79A9D8" state="trabalhando" live />
      </StrictMode>,
    );
    expect(mq.montadosAgora()).toBe(1);
    expect(container.querySelectorAll("svg")).toHaveLength(1);
    expect(montados.at(-1)!.estado).toBe("trabalhando");
    unmount();
    expect(mq.montadosAgora()).toBe(0);
  });

  it("vários cartões dividem o laço da biblioteca e cada um some ao desmontar", () => {
    const ids = catalog().avatars.map((a) => a.id);
    const { unmount } = render(
      <>
        {ids.map((id) => (
          <AvatarView key={id} definition={av(id).definition} color={av(id).color} state="trabalhando" live />
        ))}
      </>,
    );
    expect(mq.montadosAgora()).toBe(12);
    unmount();
    expect(mq.montadosAgora()).toBe(0);
  });

  it("se o player não monta, mostra o desenho parado", () => {
    montar.mockImplementationOnce(() => {
      throw new Error("sem SVG");
    });
    const { container } = render(<AvatarView definition={av("pixel").definition} color="#C58AD8" state="esperando" live />);
    expect(container.querySelector(".avatar-body svg")).not.toBeNull();
    expect(mq.montadosAgora()).toBe(0);
  });
});

describe("AvatarView no palco (a prévia grande da galeria)", () => {
  it("o palco é o da biblioteca: 156 x 170, a proporção do Enquadramento padrão (o da telinha)", () => {
    expect(PROPORCAO_PALCO).toBeCloseTo(156 / 170, 6);
  });

  it("monta no palco, com o mesmo limite de quadros, numa caixa na proporção dele", () => {
    const { container } = render(<AvatarView definition={av("fantasma").definition} color="#6FA8DC" state="concluido" live enquadre="palco" />);
    expect(montar).toHaveBeenCalledTimes(1);
    const op = montar.mock.calls[0][1]!;
    expect(op.enquadre).toBe("palco");
    expect(op.fps).toBe(FPS); // o palco não muda o limite
    expect(op.biblioteca).toBe(biblioteca()); // nem as animações
    const caixa = container.querySelector<HTMLElement>(".avatar")!;
    expect(caixa.classList.contains("avatar-palco")).toBe(true);
    expect(caixa.style.width).toBe("100%"); // sem size: a largura do contêiner
    expect(caixa.style.height).toBe("");
    // o jsdom não guarda aspect-ratio no style: o HTML que o React gera mostra a proporção
    const html = renderToStaticMarkup(<AvatarView definition={av("fantasma").definition} color="#6FA8DC" state="concluido" enquadre="palco" />);
    expect(html).toContain(`width:100%;aspect-ratio:${PROPORCAO_PALCO}`);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 312 340"); // o palco inteiro, com o espaço dos extras
  });

  it("com size, a largura é o size e a altura segue o palco", () => {
    const { container } = render(<AvatarView definition={av("broto").definition} color="#7FB77E" size={200} live enquadre="palco" />);
    const caixa = container.querySelector<HTMLElement>(".avatar")!;
    expect(caixa.style.width).toBe("200px");
    const html = renderToStaticMarkup(<AvatarView definition={av("broto").definition} color="#7FB77E" size={200} enquadre="palco" />);
    expect(html).toContain(`width:200px;aspect-ratio:${PROPORCAO_PALCO}`);
    expect(html).not.toContain("height:");
  });

  it("trocar de enquadre ajusta o player, sem montar de novo", () => {
    const { rerender, container } = render(<AvatarView definition={av("bolota").definition} color="#E07A57" state="ocioso" live />);
    const p = montados[0];
    const ajustar = vi.spyOn(p, "ajustar");
    rerender(<AvatarView definition={av("bolota").definition} color="#E07A57" state="ocioso" live enquadre="palco" />);
    expect(ajustar).toHaveBeenLastCalledWith({ enquadre: "palco" });
    expect(montar).toHaveBeenCalledTimes(1);
    expect(container.querySelector("svg")!.getAttribute("viewBox")).toBe("0 0 312 340");
  });

  it("parado no palco é o SVG da biblioteca no palco", () => {
    const { container } = render(<AvatarView definition={av("nova").definition} color="#E7C25E" state="esperando" enquadre="palco" />);
    const esperado = document.createElement("span");
    esperado.innerHTML = mq.quadroSvg(av("nova").definition, "esperando", 1.2, { cor: "#E7C25E", biblioteca: biblioteca(), enquadre: "palco", extras: true, prefixo: "p-nova-" });
    expect(container.querySelector(".avatar-body")!.innerHTML).toBe(esperado.innerHTML);
  });

  it("os avatares pequenos seguem no corpo, quadrados", () => {
    const { container } = render(<AvatarView definition={av("cubo").definition} color="#79A9D8" size={56} />);
    const caixa = container.querySelector<HTMLElement>(".avatar")!;
    expect(caixa.classList.contains("avatar-palco")).toBe(false);
    expect([caixa.style.width, caixa.style.height]).toEqual(["56px", "56px"]);
    expect(container.querySelector("svg")!.getAttribute("viewBox")).not.toBe("0 0 312 340");
  });
});

describe("a fonte dos extras de texto", () => {
  it("o app carrega a JetBrains Mono Bold de fontes/ por @font-face local (a família e o peso que o mascotinhos pede)", () => {
    const face = baseCss.match(/@font-face\s*{[^}]*}/)?.[0] ?? "";
    expect(face).toMatch(/font-family:\s*"JetBrains Mono"/);
    expect(face).toMatch(/font-weight:\s*700/);
    const url = face.match(/url\("([^"]+)"\)/)?.[1] ?? "";
    expect(url).toBe("../../../fontes/JetBrainsMono-Bold.ttf"); // local, sem CDN
    expect(Object.keys(FONTES).sort()).toEqual(["../../../../fontes/JetBrainsMono-Bold.ttf", "../../../../fontes/OFL.txt"]); // a fonte e a licença ao lado
    const svg = mq.quadroSvg(av("sereno").definition, "ocioso", 2.0, { biblioteca: biblioteca(), enquadre: "palco" });
    expect(svg).toMatch(/font-family="JetBrains Mono,/); // é essa família que os extras de texto pedem
  });
});

// o laço da biblioteca com um relógio e um requestAnimationFrame de mentira: dá para contar os quadros desenhados
describe("AvatarView animado: o limite de quadros", () => {
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

  beforeEach(() => {
    agora = 1000;
    fila = new Map();
    vi.spyOn(performance, "now").mockImplementation(() => agora);
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      fila.set(proximo, cb);
      return proximo++;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => fila.delete(id));
  });
  afterEach(() => vi.unstubAllGlobals());

  // um segundo de quadros a 60 Hz: quantas vezes o desenho mudou
  const quadrosEmUmSegundo = (container: HTMLElement) => {
    let mudancas = 0;
    let ultimo = container.querySelector(".avatar-live")!.innerHTML;
    for (let i = 0; i < 60; i++) {
      passo(1000 / 60);
      const atual = container.querySelector(".avatar-live")!.innerHTML;
      if (atual !== ultimo) mudancas++;
      ultimo = atual;
    }
    return mudancas;
  };

  it("a 60 Hz, o player redesenha no máximo 30 vezes por segundo, no laço da própria biblioteca", () => {
    reduzir(false);
    const { container, unmount } = render(<AvatarView definition={av("broto").definition} color="#7FB77E" state="trabalhando" live />);
    const n = quadrosEmUmSegundo(container);
    expect(n).toBeGreaterThanOrEqual(25); // anima de verdade (o trabalhando muda a cada quadro)
    expect(n).toBeLessThanOrEqual(FPS + 1);
    expect(fila.size).toBe(1); // um pedido só por quadro: nenhum segundo laço
    unmount();
    expect(fila.size).toBe(0); // desmontou o último: o laço para
  });

  it("vários cartões dividem o mesmo laço, todos no limite", () => {
    reduzir(false);
    const { container, unmount } = render(
      <>
        {["bolota", "fantasma", "cubo"].map((id) => (
          <AvatarView key={id} definition={av(id).definition} color={av(id).color} state="trabalhando" live />
        ))}
      </>,
    );
    passo(1000 / 60);
    expect(fila.size).toBe(1);
    const cartoes = [...container.querySelectorAll<HTMLElement>(".avatar")];
    for (const c of cartoes) {
      const n = quadrosEmUmSegundo(c);
      expect(n).toBeLessThanOrEqual(FPS + 1);
    }
    unmount();
  });

  it("com \"reduzir movimento\" do sistema, fica parado na pose do estado (o limite não muda isso)", () => {
    reduzir(true);
    const { container, unmount } = render(<AvatarView definition={av("broto").definition} color="#7FB77E" state="trabalhando" live />);
    expect(quadrosEmUmSegundo(container)).toBe(0);
    expect(fila.size).toBe(0); // nada agendado
    unmount();
  });
});

describe("AvatarView parado", () => {
  it("é um SVG da biblioteca, sem player nem laço", () => {
    const { container } = render(<AvatarView definition={av("sereno").definition} color="#D97757" state="esperando" />);
    expect(montar).not.toHaveBeenCalled();
    expect(mq.montadosAgora()).toBe(0);
    const esperado = document.createElement("span");
    esperado.innerHTML = mq.quadroSvg(av("sereno").definition, "esperando", 1.2, { cor: "#D97757", biblioteca: biblioteca(), enquadre: "corpo", extras: true, prefixo: "p-sereno-" });
    expect(container.querySelector(".avatar-body")!.innerHTML).toBe(esperado.innerHTML);
  });

  it("sem extras, só o corpo e os olhos: os extras do estado somem", () => {
    const nos = (html: string) => (html.match(/<(path|text|circle|ellipse|rect|line|polyline|polygon)\b/g) ?? []).length;
    for (const state of ["ocioso", "trabalhando", "esperando", "concluido"] as const) {
      const com = render(<AvatarView definition={av("sereno").definition} color="#D97757" state={state} />).container.innerHTML;
      const sem = render(<AvatarView definition={av("sereno").definition} color="#D97757" state={state} extras={false} />).container.innerHTML;
      expect(nos(com), state).toBeGreaterThan(nos(sem));
    }
  });

  it("o rótulo vai para leitores de tela; o desenho fica escondido deles", () => {
    const { getByRole } = render(<AvatarView definition={av("maestro").definition} color="#E58FB0" label="Claude Code: Maestro" />);
    expect(getByRole("img", { name: "Claude Code: Maestro" })).toBeInTheDocument();
  });
});

// o pacote de animações: um mascote animado só troca no ponto seguro dele; uma prévia troca na hora
describe("AvatarView animado: trocar de pacote", () => {
  // o player guarda uma cópia da biblioteca: o pacote dela se vê pelas poses (vivo e clássico diferem no ocioso)
  const pacoteDe = (p: Mascotinho) => {
    const pose = (b: { parametros(e: string, t: number): unknown }) => JSON.stringify(b.parametros("ocioso", 1.3));
    const achado = (["vivo", "classico"] as const).find((x) => pose(biblioteca(x)) === pose(p.animador.biblioteca));
    expect(pose(biblioteca("vivo"))).not.toBe(pose(biblioteca("classico")));
    return achado;
  };
  let agora = 0;
  const passar = (s: number) =>
    act(() => {
      agora += s * 1000;
      vi.advanceTimersByTime(s * 1000);
    });

  beforeEach(() => {
    agora = 1000;
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.spyOn(performance, "now").mockImplementation(() => agora);
    vi.stubGlobal("requestAnimationFrame", () => 1);
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const el = (state: import("./state").MascotState, pacote: "vivo" | "classico", previa = false) => (
    <AvatarView definition={av("bolota").definition} color="#E07A57" state={state} live pacote={pacote} previa={previa} />
  );

  it("trabalhando: o pacote novo espera; quando o mascote assenta no ocioso, entra sem montar de novo", () => {
    const { rerender, container } = render(el("trabalhando", "vivo"));
    const p = montados[0];
    const svg = container.querySelector("svg");
    passar(3);
    rerender(el("trabalhando", "classico"));
    passar(10);
    expect(pacoteDe(p)).toBe("vivo"); // segue animando no pacote de antes
    rerender(el("esperando", "classico"));
    passar(10);
    expect(pacoteDe(p)).toBe("vivo");
    rerender(el("ocioso", "classico"));
    passar(0.2);
    expect(pacoteDe(p)).toBe("vivo"); // ainda misturando os estados
    passar(0.8);
    expect(pacoteDe(p)).toBe("classico");
    expect(montar).toHaveBeenCalledTimes(1);
    expect(container.querySelector("svg")).toBe(svg);
  });

  it("concluído: espera a comemoração acabar", () => {
    const { rerender } = render(el("concluido", "vivo"));
    const p = montados[0];
    rerender(el("concluido", "classico"));
    passar(1);
    expect(pacoteDe(p)).toBe("vivo");
    passar(1.5);
    expect(pacoteDe(p)).toBe("classico");
  });

  it("várias escolhas seguidas: só a última conta", () => {
    const { rerender } = render(el("trabalhando", "vivo"));
    const p = montados[0];
    const ajustar = vi.spyOn(p, "ajustar");
    rerender(el("trabalhando", "classico"));
    passar(1);
    rerender(el("trabalhando", "vivo"));
    passar(1);
    rerender(el("ocioso", "vivo"));
    passar(5);
    expect(ajustar).not.toHaveBeenCalled(); // voltou ao que já mostrava: nada a trocar
    rerender(el("ocioso", "classico"));
    passar(1);
    expect(ajustar).toHaveBeenCalledTimes(1);
    expect(pacoteDe(p)).toBe("classico");
  });

  it("a prévia troca na hora, mesmo trabalhando", () => {
    const { rerender } = render(el("trabalhando", "vivo", true));
    const p = montados[0];
    rerender(el("trabalhando", "classico", true));
    expect(pacoteDe(p)).toBe("classico");
    expect(montar).toHaveBeenCalledTimes(1);
  });

  it("desmontar no meio da espera não deixa nada agendado", () => {
    const { rerender, unmount } = render(el("trabalhando", "vivo"));
    rerender(el("trabalhando", "classico"));
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("parado, segue o pacote na hora (não há movimento a interromper)", () => {
    const { container, rerender } = render(<AvatarView definition={av("sereno").definition} color="#D97757" state="ocioso" pacote="vivo" />);
    const vivo = container.querySelector(".avatar-body")!.innerHTML;
    rerender(<AvatarView definition={av("sereno").definition} color="#D97757" state="ocioso" pacote="classico" />);
    const esperado = document.createElement("span");
    esperado.innerHTML = mq.quadroSvg(av("sereno").definition, "ocioso", 1.2, { cor: "#D97757", biblioteca: biblioteca("classico"), enquadre: "corpo", extras: true, prefixo: "p-sereno-" });
    expect(container.querySelector(".avatar-body")!.innerHTML).toBe(esperado.innerHTML);
    expect(esperado.innerHTML).not.toBe(vivo);
  });
});
