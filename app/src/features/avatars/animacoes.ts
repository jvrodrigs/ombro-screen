// As animações do mascote no app: as mesmas da telinha (mascote.py), vindas do mascotinhos. O pacote "vivo" (o da
// demonstração da biblioteca) para ocioso, trabalhando, esperando e concluído; alerta e reset ainda no "classico", como
// na telinha. Nada de movimento é feito aqui: só qual pacote vale para cada estado.
import { bibliotecaDoPacote, caixaDoEnquadre, Enquadramento, type Biblioteca } from "mascotinhos";
import type { MascotState } from "./state";

/** O pacote da demonstração do mascotinhos (o mesmo PACOTE do mascote.py). */
export const PACOTE = "vivo";
/** Os estados que ainda usam o pacote "classico" (o mesmo DO_CLASSICO do mascote.py). */
export const DO_CLASSICO: readonly MascotState[] = ["alerta", "reset"];
/** O instante da pose parada (avatares fora de animação): o mesmo da folha do mascotinhos. */
export const T_PARADO = 1.2;
/**
 * O limite de quadros do player no app: a opção `fps` da própria biblioteca (ela só redesenha quando passa um quadro
 * de 1/30 s, no mesmo laço de sempre). As animações são as mesmas, só com menos quadros por segundo. A telinha não
 * passa por aqui: o mascote.py tem a frequência dele.
 */
export const FPS = 30;

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

let cache: Biblioteca | null = null;

/** O pacote vivo, com alerta e reset do clássico por cima (montada uma vez). */
export function biblioteca(): Biblioteca {
  if (!cache) {
    const classico = bibliotecaDoPacote("classico");
    cache = bibliotecaDoPacote(PACOTE).com(...DO_CLASSICO.map((e) => classico.obter(e)));
  }
  return cache;
}
