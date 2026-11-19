import { useEffect, useMemo, useRef, useState } from "react";
import { montar, quadroSvg, type Definicao, type Mascotinho } from "mascotinhos";
import { biblioteca, FPS, momentoSeguro, PROPORCAO_PALCO, T_PARADO, trocarPacote, type Enquadre, type Pacote } from "./animacoes";
import { usePacoteAplicado } from "./pacote";
import type { MascotState } from "./state";

/** De quanto em quanto tempo um mascote que espera para trocar de pacote olha de novo (ms). */
const ESPERA_TROCA_MS = 400;

/**
 * Um avatar desenhado pelo mascotinhos, com as animações da telinha (ver animacoes.ts).
 *  - `live`: o player da biblioteca, animado no estado (movimento, olhos, piscadas, extras e a mistura de um estado
 *    no outro); trocar de avatar ou de cor passa pela transição dela. Ela tem um laço só para a página inteira, não
 *    desenha o que está fora da tela nem com a janela escondida, e fica parada no "reduzir movimento" do sistema.
 *    No app, a no máximo FPS quadros por segundo (a opção `fps` dela).
 *  - padrão: um SVG parado (a pose do estado), sem nenhum laço de animação: listas custam nada.
 * `enquadre`: "corpo" (padrão) é a caixa justa no corpo, quadrada, com os extras vazando por cima; "palco" é o palco
 * da biblioteca, com o espaço dos extras dentro da caixa (a prévia grande da galeria, que fica dentro de um diálogo que
 * rola e cortaria o que vaza). No palco, a largura é `size` (ou a do contêiner, sem `size`) e a altura segue a
 * proporção do palco.
 * `pacote`: o pacote de animações; sem ele, o EM USO no ombrod (o mesmo da telinha). Um mascote animado que representa
 * um agente só passa para o pacote novo no ponto seguro dele (ocioso ou alerta assentados, ou o concluído depois da
 * comemoração; nunca trabalhando, esperando ou reset); uma `previa` (o que você está escolhendo) troca na hora. A troca
 * é a mistura curta da biblioteca, sem remontar o player.
 * O SVG vem da própria biblioteca, que o monta a partir de números (nada de texto de fora vai cru).
 */
export function AvatarView({
  definition,
  color,
  size,
  state = "ocioso",
  live = false,
  extras = true,
  enquadre = "corpo",
  pacote,
  previa = false,
  label,
}: {
  definition: Definicao;
  color: string;
  /** O lado (corpo, padrão 48) ou a largura (palco, padrão: a do contêiner), em px. */
  size?: number;
  /** O estado do mascote (o mesmo da telinha). */
  state?: MascotState;
  live?: boolean;
  /** Os extras do estado (zzZ, "?", ✓...); a galeria os esconde para mostrar só o personagem. */
  extras?: boolean;
  enquadre?: Enquadre;
  /** O pacote de animações (padrão: o em uso no ombrod). */
  pacote?: Pacote;
  /** Uma prévia de escolha: troca de pacote na hora, sem esperar um ponto seguro. */
  previa?: boolean;
  label?: string;
}) {
  const nome = label ?? definition.nome;
  const emUso = usePacoteAplicado();
  const pac = pacote ?? emUso;
  const caixa = enquadre === "palco" ? { width: size ?? "100%", aspectRatio: PROPORCAO_PALCO } : { width: size ?? 48, height: size ?? 48 };
  return (
    <span className={enquadre === "palco" ? "avatar avatar-palco" : "avatar"} role="img" aria-label={nome} style={caixa}>
      {live ? (
        <LiveAvatar definition={definition} color={color} state={state} extras={extras} enquadre={enquadre} pacote={pac} previa={previa} />
      ) : (
        <StillAvatar definition={definition} color={color} state={state} extras={extras} enquadre={enquadre} pacote={pac} />
      )}
    </span>
  );
}

interface Props {
  definition: Definicao;
  color: string;
  state: MascotState;
  extras: boolean;
  enquadre: Enquadre;
  pacote: Pacote;
}

function StillAvatar({ definition, color, state, extras, enquadre, pacote }: Props) {
  // parado: não há movimento a interromper, então segue o pacote em uso na hora
  const svg = useMemo(
    () => quadroSvg(definition, state, T_PARADO, { cor: color, biblioteca: biblioteca(pacote), enquadre, extras, prefixo: `p-${definition.id}-` }),
    [definition, color, state, extras, enquadre, pacote],
  );
  // eslint-disable-next-line react/no-danger
  return <span className="avatar-body" aria-hidden dangerouslySetInnerHTML={{ __html: svg }} />;
}

function LiveAvatar({ definition, color, state, extras, enquadre, pacote, previa }: Props & { previa: boolean }) {
  const host = useRef<HTMLSpanElement>(null);
  const player = useRef<Mascotinho | null>(null);
  // o que o player está mostrando: as mudanças de props viram transições da biblioteca, nunca uma montagem nova
  const mostrado = useRef({ definition, color, state, extras, enquadre });
  const pacoteMostrado = useRef(pacote);
  const [falhou, setFalhou] = useState(false);

  useEffect(() => {
    if (!host.current) return;
    const atual = mostrado.current;
    try {
      player.current = montar(host.current, {
        forma: atual.definition,
        cor: atual.color,
        estado: atual.state,
        biblioteca: biblioteca(pacoteMostrado.current),
        enquadre: atual.enquadre,
        extras: atual.extras,
        fps: FPS, // o limite do app (ver animacoes.ts); o laço, a pausa e o "reduzir movimento" seguem os da biblioteca
      });
    } catch {
      player.current = null;
      setFalhou(true); // sem SVG no DOM (testes, navegador antigo): o desenho parado, a página segue
    }
    return () => {
      player.current?.destruir();
      player.current = null;
    };
  }, []);

  useEffect(() => {
    const p = player.current;
    const antes = mostrado.current;
    mostrado.current = { definition, color, state, extras, enquadre };
    if (!p) return;
    if (definition !== antes.definition) p.usarForma(definition, color); // encolhe, pisca e troca no meio
    else if (color !== antes.color) p.ajustar({ cor: color });
    if (state !== antes.state) p.irPara(state); // mistura com o estado anterior
    if (extras !== antes.extras) p.ajustar({ extras });
    if (enquadre !== antes.enquadre) p.ajustar({ enquadre });
  }, [definition, color, state, extras, enquadre]);

  // o pacote: troca quando for seguro para este mascote (uma prévia, na hora). Só o último pedido conta: um novo
  // cancela a espera do anterior, e voltar ao que já está na tela não faz nada.
  useEffect(() => {
    let espera: number | undefined;
    const tentar = () => {
      espera = undefined;
      const p = player.current;
      if (!p || pacoteMostrado.current === pacote) return;
      if (previa || momentoSeguro(p)) {
        trocarPacote(p, biblioteca(pacote));
        pacoteMostrado.current = pacote;
      } else {
        espera = window.setTimeout(tentar, ESPERA_TROCA_MS);
      }
    };
    tentar();
    return () => window.clearTimeout(espera);
  }, [pacote, previa]);

  if (falhou) return <StillAvatar definition={definition} color={color} state={state} extras={extras} enquadre={enquadre} pacote={pacote} />;
  return <span ref={host} className="avatar-body avatar-live" aria-hidden />;
}
