import { useState } from "react";
import { Card, Segmented } from "../../components/ui";
import type { ConfigStore } from "../../hooks/useConfig";
import { useOmbro } from "../../hooks/useOmbro";
import { PROVIDER_NAMES } from "../../lib/format";
import { errorMessage } from "../../services/api";
import type { AnimationStatus } from "../../types/ombro";
import { PACOTE, PACOTES, pacoteValido, PROPORCAO_PALCO, type Pacote } from "../avatars/animacoes";
import { ESTADOS } from "../avatars/AvatarSettings";
import { AvatarView } from "../avatars/AvatarView";
import { catalog, chooseAvatar } from "../avatars/catalog";
import type { MascotState } from "../avatars/state";

const NOMES: Record<Pacote, { label: string; desc: string }> = {
  vivo: { label: "Vivo", desc: "Olhos que mudam com o estado, zzZ, engrenagem, “?” e um ✓ com faíscas. O padrão." },
  classico: { label: "Clássico", desc: "Os movimentos de antes, mais contidos: pulinhos, zZ e um ✓ simples." },
};
const EM_AVALIACAO: Record<string, string> = { "3d": "3D", terminal: "Terminal" };

function nomeDe(x: string): string {
  return NOMES[x as Pacote]?.label ?? x;
}

function agentes(ids: string[]): string {
  const nomes = ids.map((id) => PROVIDER_NAMES[id] ?? id);
  return nomes.length <= 1 ? (nomes[0] ?? "um agente") : `${nomes.slice(0, -1).join(", ")} e ${nomes[nomes.length - 1]}`;
}

/** Quando a escolha entra em uso: na hora, ou o que ela está esperando (o ombrod decide; ver ombro/animacoes.py). */
function situacao(a: AnimationStatus): string {
  if (a.failed && a.failed === a.desired) return `Não consegui carregar o ${nomeDe(a.failed)}; segue o ${nomeDe(a.applied)}.`;
  if (!a.pending) return `${nomeDe(a.applied)} em uso no app e na telinha.`;
  const ate = `Até lá, segue o ${nomeDe(a.applied)}.`;
  switch (a.waiting_for) {
    case "activity":
      return `O ${nomeDe(a.desired)} entra quando a atividade terminar (${agentes(a.busy_providers)}: trabalhando ou esperando você). ${ate}`;
    case "starting":
      return `O ${nomeDe(a.desired)} entra assim que o Ombro terminar de ler os agentes. ${ate}`;
    default:
      return `O ${nomeDe(a.desired)} entra em instantes, quando o mascote terminar a animação de agora. ${ate}`;
  }
}

/**
 * O pacote de animações do mascote, para o app e a telinha (uma escolha só, para todos os agentes). A escolha é
 * gravada na hora; ela entra em uso na hora se nada está em andamento, ou quando a atividade terminar. A prévia mostra
 * o pacote escolhido; os mascotes dos agentes mostram o que está em uso.
 */
export function AnimationSettings({ config }: { config: ConfigStore }) {
  const { snapshot } = useOmbro();
  const cat = catalog();
  const [estado, setEstado] = useState<MascotState>("ocioso");
  const [erro, setErro] = useState<string | null>(null);

  const opcao = config.options.get("pacote_animacao");
  const desejado = pacoteValido(config.values.pacote_animacao);
  // o retrato (a cada poucos segundos) é o mais novo, a não ser que ainda seja de antes da última escolha
  const doRetrato = snapshot?.providers?.animation;
  const anim = doRetrato && doRetrato.desired === desejado ? doRetrato : (config.config?.animation ?? doRetrato ?? null);
  const carregado = config.config !== null && config.schema !== null;
  const suportado = Boolean(opcao) && anim !== null;
  const ocupado = config.busy("pacote_animacao");

  const ombro = snapshot?.providers?.ombro_avatar;
  const av = (ombro && cat.byId.get(ombro.avatar)) || chooseAvatar(cat, "padrao", null);
  const cor = ombro?.color ?? av.color;

  const escolher = async (p: Pacote) => {
    setErro(null);
    try {
      await config.update({ pacote_animacao: p });
    } catch (e) {
      setErro(errorMessage(e));
    }
  };

  const fora = (anim?.unavailable ?? []).map((u) => EM_AVALIACAO[u.id] ?? u.id);
  return (
    <Card>
      <div className="card-title">
        <h2>Animações</h2>
      </div>
      <div className="animation-settings">
        <div className="animation-preview">
          <div className="avatar-picker-stage" style={{ aspectRatio: PROPORCAO_PALCO }}>
            <AvatarView definition={av.definition} color={cor} state={estado} live previa pacote={suportado ? desejado : PACOTE} enquadre="palco" label={`Prévia: ${av.name}`} />
          </div>
        </div>
        <div className="animation-states">
          <Segmented<MascotState> label="Estado da prévia" value={estado} onChange={setEstado} options={ESTADOS} />
        </div>
        <div className="animation-controls">
          <Segmented<Pacote>
            label="Pacote de animações"
            value={suportado ? desejado : PACOTE}
            disabled={!suportado || ocupado}
            onChange={escolher}
            options={PACOTES.filter((p) => !opcao?.choices || opcao.choices.includes(p)).map((p) => ({ value: p, label: NOMES[p].label, title: NOMES[p].desc }))}
          />
          <p className="muted small">{NOMES[suportado ? desejado : PACOTE].desc}</p>
          {suportado && anim ? (
            <p className={`small ${anim.pending ? "secondary" : "muted"}`} role="status">
              {situacao(anim)}
            </p>
          ) : carregado ? (
            <p className="muted small" role="status">
              O serviço do Ombro em uso ainda não tem esta opção: atualize o Ombro para escolher o pacote.
            </p>
          ) : null}
          {erro && (
            <p className="small tone-critical" role="alert">
              {erro}
            </p>
          )}
          <p className="muted small">
            Vale para todos os agentes, no app e na telinha. Com um agente trabalhando ou esperando você, a troca espera a atividade terminar.
            {fora.length > 0 && ` ${fora.join(" e ")} ainda estão em avaliação.`}
          </p>
        </div>
      </div>
    </Card>
  );
}
