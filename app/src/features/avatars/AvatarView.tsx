import { useEffect, useMemo, useRef, useState } from "react";
import { renderAvatarSvg, type AvatarDefinition, type AvatarMotion } from "open-bot-avatar";
import { mountAvatar, type MountedAvatar } from "open-bot-avatar/dom";
import type { MascotState } from "./state";

/**
 * Um avatar desenhado pelo motor do open-bot-avatar.
 *  - `live`: animado (pisca, olha em volta, muda de humor). O motor tem um laço só para a página inteira, não
 *    desenha o que está fora da tela e respeita o "reduzir movimento" do sistema.
 *  - padrão: um SVG estático (`renderAvatarSvg`), sem nenhum laço de animação: listas custam nada.
 *  - `state`: os extras do estado do mascote, como na telinha ("?" esperando, gota no alerta, ✓ concluído,
 *    pontinhos trabalhando, confete no reset, z no ocioso). Só CSS, parados no "reduzir movimento".
 * O SVG vem do próprio motor, que remonta o path a partir de números validados (nada de texto de fora vai cru).
 */
export function AvatarView({
  definition,
  color,
  size = 48,
  mood = "idle",
  live = false,
  follow = false,
  state,
  label,
}: {
  definition: AvatarDefinition;
  color: string;
  size?: number;
  mood?: AvatarMotion;
  live?: boolean;
  /** Os olhos seguem o ponteiro (só na prévia grande: medir a posição a cada quadro custa). */
  follow?: boolean;
  state?: MascotState;
  label?: string;
}) {
  const nome = label ?? definition.name;
  const corpo = live ? (
    <LiveAvatar definition={definition} color={color} size={size} mood={mood} follow={follow} />
  ) : (
    <StillAvatar definition={definition} color={color} size={size} mood={mood} />
  );
  return (
    <span className="avatar" role="img" aria-label={nome} style={{ width: size, height: size, fontSize: size, ["--avatar-color" as string]: color }}>
      {corpo}
      {state && <AvatarExtra state={state} />}
    </span>
  );
}

function StillAvatar({ definition, color, size, mood }: { definition: AvatarDefinition; color: string; size: number; mood: AvatarMotion }) {
  const svg = useMemo(() => renderAvatarSvg(definition, { color, motion: mood, size, time: 0.6 }), [definition, color, mood, size]);
  // eslint-disable-next-line react/no-danger
  return <span className="avatar-body" aria-hidden dangerouslySetInnerHTML={{ __html: svg }} />;
}

function LiveAvatar({ definition, color, size, mood, follow }: { definition: AvatarDefinition; color: string; size: number; mood: AvatarMotion; follow: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const avatar = useRef<MountedAvatar | null>(null);
  const [falhou, setFalhou] = useState(false);
  useEffect(() => {
    if (!host.current) return;
    try {
      avatar.current = mountAvatar(host.current, { definition, color, mood, label: "", followPointer: follow });
    } catch {
      avatar.current = null;
      setFalhou(true); // sem canvas (testes, navegador antigo): o desenho parado, a página segue
    }
    return () => avatar.current?.destroy();
    // a montagem acontece uma vez; as mudanças vão pelo update abaixo
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    avatar.current?.update({ definition, color, mood, followPointer: follow });
  }, [definition, color, mood, follow]);
  if (falhou) return <StillAvatar definition={definition} color={color} size={size} mood={mood} />;
  return <div ref={host} className="avatar-body avatar-live" aria-hidden />;
}

/** Os extras de cada estado, presos ao canto de cima do avatar. */
function AvatarExtra({ state }: { state: MascotState }) {
  switch (state) {
    case "esperando":
      return <span className="avatar-extra extra-esperando" aria-hidden>?</span>;
    case "alerta":
      return (
        <span className="avatar-extra extra-alerta" aria-hidden>
          <svg viewBox="0 0 10 14">
            <path d="M5 0Q0 7 0 9.5A5 4.5 0 0 0 10 9.5Q10 7 5 0Z" />
          </svg>
        </span>
      );
    case "concluido":
      return (
        <span className="avatar-extra extra-concluido" aria-hidden>
          <svg viewBox="0 0 12 10">
            <path d="M1.5 5.5L4.5 8.5L10.5 1.5" />
          </svg>
        </span>
      );
    case "trabalhando":
      return (
        <span className="avatar-extra extra-trabalhando" aria-hidden>
          <i />
          <i />
          <i />
        </span>
      );
    case "reset":
      return (
        <span className="avatar-extra extra-reset" aria-hidden>
          <i />
          <i />
          <i />
          <i />
          <i />
          <i />
        </span>
      );
    default:
      return <span className="avatar-extra extra-ocioso" aria-hidden>z</span>;
  }
}
