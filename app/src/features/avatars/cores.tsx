import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { Animador, Definicao, Forma, Mascotinho, RGB } from "mascotinhos";
import { useOmbro } from "../../hooks/useOmbro";

/**
 * A cor do corpo de cada avatar, por id (o `cores_avatares` do config.json e da IPC): "default" (a cor original do avatar
 * ou do agente), "rgb" (um ciclo de cores) ou "#RRGGBB". Sem entrada, o avatar herda o que valia antes. Só o corpo muda:
 * a forma, os olhos, os extras, a transparência e a pose são os da biblioteca (mascotinhos), que o app não refaz.
 */
export type EntradaCor = { modo: "default" } | { modo: "rgb" } | { modo: "custom"; cor: string };

/** Uma volta do ciclo, em segundos: o mesmo CICLO_RGB_S do mascote.py. */
export const CICLO_RGB_S = 12;
const LUZ = 0.58;
const SATURACAO = 0.78;
const HEX = /^#?[0-9a-fA-F]{6}$/;

/** "#RRGGBB" (maiúsculas) de um texto como "3d7a4f" ou "#3D7A4F"; null se não for uma cor completa. */
export function normalizarHex(texto: string): string | null {
  const t = texto.trim();
  return HEX.test(t) ? `#${t.replace(/^#/, "").toUpperCase()}` : null;
}

/** O valor que o ombrod entrega ("default" | "rgb" | "#RRGGBB") como entrada; qualquer outra coisa não é uma entrada. */
export function entradaDeValor(v: unknown): EntradaCor | null {
  if (typeof v !== "string") return null;
  const t = v.trim().toLowerCase();
  if (t === "default") return { modo: "default" };
  if (t === "rgb") return { modo: "rgb" };
  const cor = normalizarHex(v);
  return cor ? { modo: "custom", cor } : null;
}

/** O valor para gravar (config.update) de uma entrada. */
export function valorDeEntrada(e: EntradaCor): string {
  return e.modo === "custom" ? e.cor : e.modo;
}

// HLS -> RGB como o colorsys do Python (o mesmo do mascotinhos), para que o app e a telinha deem a mesma cor
function hls(h: number, l: number, s: number): [number, number, number] {
  const m2 = l <= 0.5 ? l * (1 + s) : l + s - l * s;
  const m1 = 2 * l - m2;
  const v = (x: number) => {
    const hue = ((x % 1) + 1) % 1;
    if (hue < 1 / 6) return m1 + (m2 - m1) * hue * 6;
    if (hue < 0.5) return m2;
    if (hue < 2 / 3) return m1 + (m2 - m1) * (2 / 3 - hue) * 6;
    return m1;
  };
  return [v(h + 1 / 3), v(h), v(h - 1 / 3)];
}

/** A cor do ciclo em `segundos` (um relógio monotônico): uma volta de matiz a cada CICLO_RGB_S, a mesma fase para todos. */
export function corDoCiclo(segundos: number): [number, number, number] {
  const fase = ((segundos % CICLO_RGB_S) + CICLO_RGB_S) % CICLO_RGB_S;
  const [r, g, b] = hls(fase / CICLO_RGB_S, LUZ, SATURACAO);
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}

export function hexDe(rgb: ArrayLike<number>): string {
  return `#${[0, 1, 2].map((i) => Math.round(rgb[i]).toString(16).padStart(2, "0")).join("")}`.toUpperCase();
}

/** O relógio do ciclo: monotônico e do app inteiro, então todos os mascotes giram na mesma fase. */
export const relogioDoCiclo = () => performance.now() / 1000;

/** O "reduzir movimento" do sistema (a media query do WebView): com ele, o ciclo fica parado na cor-base. */
export function movimentoReduzido(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Quem diz qual é a cor pedida para o avatar de um id (ou nenhuma). Lido a cada quadro, sempre com o valor mais novo. */
export type ResolverEntrada = (idDoAvatar: string) => EntradaCor | null | undefined;

const rgbDe = (hex: string): [number, number, number] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number];

/**
 * Liga a cor do corpo num mascote animado: em cada quadro, a cor vem da entrada do avatar que está REALMENTE desenhado
 * (`animador.forma`), então numa troca de avatar ela muda no meio, junto com a forma, e uma edição da cor do destino
 * (ou de qualquer id) vale no próximo quadro sem mexer na transição. Usa só os quadros que o player já desenha: sem
 * timer, sem laço, e sem ajustar({cor}) (que cortaria a troca de forma em andamento).
 *  - personalizada: a cor escolhida (também no "reduzir movimento", em que o player lê `animador.cor`);
 *  - rgb: o ciclo do relógio `relogio`, ou a cor-base parada com o "reduzir movimento";
 *  - padrão (explícito): a cor-base em todo quadro, sem o realce da troca de estado (o mesmo da telinha);
 *  - sem entrada: o que a biblioteca faz (com o realce), o caminho de antes.
 * A cor-base (a do agente) continua sendo a que o player recebeu. Devolve o que desliga.
 */
export function ligarCorDoCorpo(
  player: Mascotinho,
  resolver: ResolverEntrada,
  relogio: () => number = relogioDoCiclo,
  reduzido: () => boolean = movimentoReduzido,
): () => void {
  const a = player.animador;
  const doAnimador = Object.getPrototypeOf(a).corAtual as (this: typeof a, agora: number) => ArrayLike<number>;
  let base = a.cor;
  const entrada = () => resolver(a.forma.avatar);
  Object.defineProperty(a, "cor", {
    configurable: true,
    enumerable: true,
    get: () => {
      const e = entrada();
      return e?.modo === "custom" ? rgbDe(e.cor) : base;
    },
    set: (v) => {
      base = v; // o que o Animador grava (a cor-base ao aplicar uma forma, ao trocar de estado)
    },
  });
  a.corAtual = (agora: number) => {
    const e = entrada();
    if (e?.modo === "rgb") return (reduzido() ? base : corDoCiclo(relogio())) as typeof base;
    if (e?.modo === "custom") return rgbDe(e.cor);
    if (e?.modo === "default") return base;
    return doAnimador.call(a, agora) as typeof base;
  };
  return () => {
    delete (a as Partial<typeof a>).corAtual; // volta ao método da biblioteca
    Object.defineProperty(a, "cor", { value: base, writable: true, configurable: true, enumerable: true });
  };
}

type PorDentro = Animador & { _troca: [Forma, RGB, number] | null; _trocou: boolean; _aplicar(forma: Forma, cor: RGB): void };

/**
 * Muda a cor-base (a do agente) de um mascote animado SEM cortar nem reiniciar uma troca de avatar em andamento (a
 * biblioteca fixa o `mascotinhos` 0.2.0; o estado da troca é dela):
 *  - antes do ponto médio: o destino é refeito com a cor nova, no mesmo instante de início (a forma de agora continua até o
 *    meio, a fase não reinicia);
 *  - depois do ponto médio: a forma nova já vale; só a cor-base muda (no corpo e no destino pendente), e a volta do corpo
 *    ao tamanho continua;
 *  - sem troca: a cor muda na hora (ajustar), como sempre.
 * Nunca volta ao avatar anterior (o ajustar({cor}) de antes aplicava a forma de agora e zerava a troca).
 */
export function mudarCorBase(p: Mascotinho, definition: Definicao, cor: string): void {
  const a = p.animador as unknown as PorDentro;
  if (a._troca && !a._trocou) {
    p.usarForma(definition, cor);
  } else if (a._troca) {
    const rgb = rgbDe(cor);
    a._aplicar(a.forma, rgb);
    // o destino pendente também guarda a cor-base: o desenho do "reduzir movimento" reaplica essa tupla (runtime.js do
    // mascotinhos 0.2.0) e, com a cor antiga nela, desfaria a mudança
    a._troca = [a._troca[0], rgb, a._troca[2]];
    p.ajustar({});
  } else {
    p.ajustar({ cor });
  }
}

/** A cor efetiva de um avatar parado ou de base: a escolhida (custom) ou a que veio (padrão, rgb: a base parada). */
export function corEfetiva(base: string, entrada: EntradaCor | null | undefined): string {
  return entrada?.modo === "custom" ? entrada.cor : base;
}

export const CoresContext = createContext<Record<string, EntradaCor>>({});

/**
 * O mapa de cores por avatar que o ombrod publica (`avatar_colors` em providers.list), para todos os avatares do app.
 * Sem o ombrod, ou com um de antes desta opção: vazio (ninguém muda de cor).
 */
export function CoresDosAvatares({ children }: { children: ReactNode }) {
  const { snapshot } = useOmbro();
  const chave = JSON.stringify(snapshot?.providers?.avatar_colors ?? {}); // o objeto muda a cada retrato; o conteúdo, não
  const mapa = useMemo(() => {
    const m: Record<string, EntradaCor> = {};
    for (const [id, v] of Object.entries(JSON.parse(chave) as Record<string, unknown>)) {
      const e = entradaDeValor(v);
      if (e) m[id] = e;
    }
    return m;
  }, [chave]);
  return <CoresContext.Provider value={mapa}>{children}</CoresContext.Provider>;
}

export function useCoresDosAvatares(): Record<string, EntradaCor> {
  return useContext(CoresContext);
}
