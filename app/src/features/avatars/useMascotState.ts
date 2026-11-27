import { useEffect, useRef, useState } from "react";
import { current, EMPTY_MEMORY, nextChange, step, type MascotState, type Memory, type Retrato } from "./state";

/** O estado do mascote de um agente, acompanhando os retratos do ombrod (com concluído e reset, como a telinha). */
export function useMascotState(p: Retrato): MascotState {
  const mem = useRef<Memory>(EMPTY_MEMORY);
  const primeiro = useRef(true);
  const [, refazer] = useState(0);
  const chave = `${p.state}|${p.working}|${p.waiting}|${p.errors}|${p.unconfirmed}|${p.collection}|${(p.quotas?.windows ?? []).map((w) => `${w.id}=${w.resets_at}`).join(",")}`;

  // o passo é feito durante o render quando o retrato muda (um ref, sem render a mais)
  const ultimaChave = useRef<string | null>(null);
  if (ultimaChave.current !== chave) {
    const agora = Date.now();
    // o primeiro retrato só é lembrado: abrir o app com tudo parado não é "terminou agora"
    mem.current = primeiro.current ? { ...step(EMPTY_MEMORY, p, agora), resetUntil: null, doneUntil: null } : step(mem.current, p, agora);
    primeiro.current = false;
    ultimaChave.current = chave;
  }

  // o reset e o concluído acabam sozinhos: um único timer, para o próximo prazo que vence (o fim do reset e, depois,
  // o do concluído, se ele ainda valer)
  const ate = nextChange(mem.current, Date.now());
  useEffect(() => {
    if (!ate) return;
    const id = window.setTimeout(() => refazer((n) => n + 1), Math.max(0, ate - Date.now()) + 20);
    return () => window.clearTimeout(id);
  }, [ate]);

  return current(mem.current, Date.now());
}
