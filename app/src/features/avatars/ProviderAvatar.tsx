import { AvatarView } from "./AvatarView";
import { catalog, chooseAvatar, colorFor, type RandomMode } from "./catalog";
import { sessionState, STATE_LABEL } from "./state";
import { useMascotState } from "./useMascotState";
import type { ProviderInfo, SessionStatus } from "../../types/ombro";

const SEM_DADOS = new Set(["nao_instalado", "nao_configurado", "indisponivel", "desconhecido", "incompativel", "conflito", "coletando", "sem_plugin", "nao_verificado", "desconectado"]);

type ProviderLike = Pick<ProviderInfo, "id" | "avatar" | "name"> &
  Partial<Pick<ProviderInfo, "state" | "working" | "waiting" | "errors" | "quotas" | "enabled" | "unconfirmed" | "collection">>;

/**
 * O avatar de um agente: o que o ombrod está usando (providers.list[].avatar), ou o padrão do catálogo. Animado
 * pelo estado real do agente (trabalhando, esperando você, perto do limite, concluído...), como o mascote da telinha.
 */
export function ProviderAvatar({ p, size = 32 }: { p: ProviderLike; size?: number }) {
  const cat = catalog();
  const av = (p.avatar && cat.byId.get(p.avatar.avatar)) || chooseAvatar(cat, "padrao", p.id);
  const color = p.avatar?.color ?? colorFor(cat, p.id, av);
  const estado = useMascotState(p);
  // sem dados do agente (desligado, não instalado, sem a integração): parado, sem extras
  const acompanha = p.enabled !== false && p.state !== undefined && !SEM_DADOS.has(p.state);
  return (
    <AvatarView
      definition={av.definition}
      color={color}
      size={size}
      live={acompanha}
      state={acompanha ? estado : "ocioso"}
      extras={acompanha}
      label={acompanha ? `${p.name ?? p.id}: ${av.name}, ${STATE_LABEL[estado]}` : `${p.name ?? p.id}: ${av.name}`}
    />
  );
}

/** O avatar de uma sessão: no modo "um por sessão", o sorteado para ela (o mesmo hash da telinha); senão, o do agente.
 * Parado (uma lista pode ser longa), na pose do estado da sessão, sem os extras (pequeno demais para eles). */
export function SessionAvatar({
  provider,
  sessionId,
  status,
  mode,
  size = 20,
}: {
  provider: ProviderInfo | undefined;
  sessionId: string;
  status?: SessionStatus;
  mode: RandomMode | undefined;
  size?: number;
}) {
  const cat = catalog();
  if (!provider) return null;
  const choice = provider.avatar?.choice;
  const av =
    choice === "aleatorio" && mode === "sessao"
      ? chooseAvatar(cat, "aleatorio", provider.id, "sessao", 0, sessionId)
      : (provider.avatar && cat.byId.get(provider.avatar.avatar)) || chooseAvatar(cat, "padrao", provider.id);
  return (
    <AvatarView
      definition={av.definition}
      color={provider.avatar?.color ?? colorFor(cat, provider.id, av)}
      size={size}
      state={sessionState(status)}
      extras={false}
      label={av.name}
    />
  );
}
