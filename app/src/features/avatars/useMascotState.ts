import { useEffect, useRef, useState } from "react";
import type { ProviderInfo } from "../../types/ombro";
import { current, EMPTY_MEMORY, step, type MascotState, type Memory } from "./state";

/** O estado do mascote de um agente, acompanhando os retratos do ombrod (com concluído e reset, como a telinha). */
export function useMascotState(p: Pick<ProviderInfo, "state" | "working" | "waiting" | "errors" | "quotas">): MascotState {
  const mem = useRef<Memory>(EMPTY_MEMORY);
  const primeiro = useRef(true);
  const [, refazer] = useState(0);
  const chave = `${p.state}|${p.working}|${p.waiting}|${p.errors}|${(p.quotas?.windows ?? []).map((w) => `${w.id}=${w.resets_at}`).join(",")}`;

  // o passo é feito durante o render quando o retrato muda (um ref, sem render a mais)
  const ultimaChave = useRef<string | null>(null);
  if (ultimaChave.current !== chave) {
    const agora = Date.now();
    // o primeiro retrato só é lembrado: abrir o app com tudo parado não é "terminou agora"
    mem.current = primeiro.current ? { ...step(EMPTY_MEMORY, p, agora), transient: null } : step(mem.current, p, agora);
    primeiro.current = false;
    ultimaChave.current = chave;
  }

  // um estado de passagem acaba sozinho: um render quando o prazo dele vence
  const ate = mem.current.transient?.until;
  useEffect(() => {
    if (!ate) return;
    const id = window.setTimeout(() => refazer((n) => n + 1), Math.max(0, ate - Date.now()) + 20);
    return () => window.clearTimeout(id);
  }, [ate]);

  return current(mem.current, Date.now());
}
