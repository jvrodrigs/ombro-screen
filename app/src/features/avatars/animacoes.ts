// As animações do mascote no app: as mesmas da telinha (mascote.py), vindas do mascotinhos, no pacote em uso (a
// preferência "pacote_animacao": o "vivo", o padrão, ou o "classico"), com os seis estados do próprio pacote. Nada de
// movimento é feito aqui: só qual pacote vale, e QUANDO um mascote animado pode passar de um pacote para o outro.
import {
  bibliotecaDoPacote,
  caixaDoEnquadre,
  DURACAO_MISTURA,
  Enquadramento,
  misturar,
  suave,
  type Animador,
  type Biblioteca,
  type Mascotinho,
  type Pose,
} from "mascotinhos";
import type { MascotState } from "./state";

/** Os pacotes que dá para escolher (o mesmo config.PACOTES_ANIMACAO do ombrod), na ordem da interface. */
export const PACOTES = ["vivo", "classico"] as const;
export type Pacote = (typeof PACOTES)[number];
/** O padrão: o da demonstração do mascotinhos (o mesmo PACOTE do mascote.py). Também o de um ombrod sem esta opção. */
export const PACOTE: Pacote = "vivo";
/** O instante da pose parada (avatares fora de animação): o mesmo da folha do mascotinhos. */
export const T_PARADO = 1.2;
/**
 * O limite de quadros do player no app: a opção `fps` da própria biblioteca (ela só redesenha quando passa um quadro
 * de 1/30 s, no mesmo laço de sempre). As animações são as mesmas, só com menos quadros por segundo. A telinha não
 * passa por aqui: o mascote.py tem a frequência dele.
 */
export const FPS = 30;
/** A comemoração do concluído (pulo, ✓ se desenhando, faíscas) acaba antes disto, em segundos (o FIM_DA_CONCLUSAO do
 * mascote.py). */
export const FIM_DA_CONCLUSAO = 2.0;
/** Os estados em que um mascote pode trocar de pacote sem cortar nada (o TROCA_SEGURA do mascote.py). */
export const TROCA_SEGURA: readonly MascotState[] = ["ocioso", "alerta", "concluido"];

/**
 * Como o desenho é enquadrado (o `enquadre` da biblioteca):
 *  - "corpo": a caixa justa no corpo, para os avatares pequenos (cartões, miniaturas, lista); os extras vazam por cima.
 *  - "palco": o palco da telinha, com o espaço dos extras (zzZ, "?", ✓) dentro da caixa; o da prévia grande da galeria.
 */
export type Enquadre = "corpo" | "palco";

/** A proporção (largura / altura) do palco: a do próprio mascotinhos (o Enquadramento padrão, o da telinha). */
export const PROPORCAO_PALCO: number = (() => {
  const [, , w, h] = caixaDoEnquadre(new Enquadramento(), "palco");
  return w / h;
})();

/** Um nome de pacote vindo de fora (a IPC): um dos PACOTES, ou o padrão. */
export function pacoteValido(x: unknown): Pacote {
  return (PACOTES as readonly unknown[]).includes(x) ? (x as Pacote) : PACOTE;
}

const cache = new Map<Pacote, Biblioteca>();

/**
 * Os seis estados de um pacote, como a biblioteca os traz (carregada uma vez por pacote). Um pacote que não carrega cai
 * no padrão: o mascote continua desenhando.
 */
export function biblioteca(pacote: Pacote = PACOTE): Biblioteca {
  let b = cache.get(pacote);
  if (!b) {
    try {
      b = bibliotecaDoPacote(pacote);
    } catch (e) {
      if (pacote === PACOTE) throw e;
      console.warn(`mascotinhos: o pacote ${pacote} não carregou; usando o ${PACOTE}`, e);
      b = biblioteca(PACOTE);
    }
    cache.set(pacote, b);
  }
  return b;
}

type ComTroca = Animador & { parametrosAtuais: (agora: number) => [Pose, number] };
const trocando = new WeakMap<Animador, true>();

/**
 * Trocar o pacote deste mascote agora não corta nada do que ele mostra? Só num estado de TROCA_SEGURA, já assentado
 * (fora da mistura de uma troca de estado ou de pacote) e, no concluído, depois da comemoração. A mesma regra do
 * Mascote.momento_seguro da telinha, no relógio do player.
 */
export function momentoSeguro(p: Mascotinho): boolean {
  const a = p.animador;
  if (!TROCA_SEGURA.includes(a.estado as MascotState) || trocando.has(a)) return false;
  const t = p.relogio() - a.inicio;
  return t >= (a.estado === "concluido" ? FIM_DA_CONCLUSAO : DURACAO_MISTURA);
}

/**
 * Passa o mascote para outra biblioteca com a mesma mistura de DURACAO_MISTURA s que a biblioteca faz entre dois
 * estados (o mesmo Mascote.usar_pacote da telinha): a pose do pacote de antes vira a do novo no mesmo instante do
 * estado (nada recomeça). Só a pose do quadro passa por aqui (a do Animador, misturada com `misturar`); o desenho, o
 * laço e o limite de fps continuam os do player. Os olhos e os extras do novo entram inteiros já no começo (o `u` é o
 * do estado): o player desenha uma camada só, então não há como cruzar os extras dos dois como a telinha faz, e fazê-
 * los surgir do zero faria o ✓, o zzZ ou o "?" piscarem.
 */
export function trocarPacote(p: Mascotinho, nova: Biblioteca): void {
  const a = p.animador as ComTroca;
  const antiga = a.biblioteca;
  if (antiga === nova) return;
  const proprio = Object.getPrototypeOf(a).parametrosAtuais as ComTroca["parametrosAtuais"];
  const desde = p.relogio();
  trocando.set(a, true);
  a.parametrosAtuais = (agora: number) => {
    const [pose, u] = proprio.call(a, agora);
    const v = (agora - desde) / DURACAO_MISTURA;
    if (v >= 1) {
      delete (a as Partial<ComTroca>).parametrosAtuais; // volta ao da biblioteca
      trocando.delete(a);
      return [pose, u];
    }
    const atual = a.biblioteca;
    a.biblioteca = antiga;
    let antes: Pose;
    try {
      [antes] = proprio.call(a, agora);
    } finally {
      a.biblioteca = atual;
    }
    const w = suave(Math.max(0, v));
    return [misturar(antes, pose, w), u];
  };
  p.ajustar({ biblioteca: nova }); // redesenha já, misturando
}
