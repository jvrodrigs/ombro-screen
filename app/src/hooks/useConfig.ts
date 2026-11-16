import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { errorMessage } from "../services/api";
import type { ConfigInfo, ConfigSchema, ConfigUpdateResult, ConfigValue, SchemaOption } from "../types/ombro";
import { useOmbro } from "./useOmbro";

/**
 * As preferências do ombrod, com um caminho só para ler e mudar:
 *
 *   pedir a mudança -> o ombrod valida, põe em uso, grava e responde -> a tela mostra o que ele confirmou
 *
 * - `values` é o que está EM USO (confirmado pelo ombrod); `pending`, o que está gravado e só vale ao reiniciar.
 *   Um controle nunca mostra "ligado" só porque o usuário clicou: mostra o que voltou na resposta.
 * - As mudanças saem uma de cada vez, na ordem dos cliques (a fila), e o ombrod as atende nessa ordem.
 * - Toda resposta traz a revisão das preferências. Uma resposta com revisão menor que a já vista é velha
 *   (um config.get pedido antes de uma mudança e respondido depois) e é descartada.
 * - As revisões só se comparam dentro do mesmo ombrod (`instance`): quando ele reinicia, recomeçam, e tudo é lido
 *   de novo.
 */
export interface ConfigStore {
  config: ConfigInfo | null;
  schema: ConfigSchema | null;
  /** As opções do esquema, por chave. */
  options: Map<string, SchemaOption>;
  values: Record<string, unknown>;
  pending: Record<string, ConfigValue>;
  /** Falha ao ler as preferências (as mudanças devolvem o próprio erro em `update`). */
  error: string | null;
  /** Há uma mudança desta chave a caminho. */
  busy: (key: string) => boolean;
  /** Muda preferências. Resolve com a resposta do ombrod; rejeita se ele recusou ou não respondeu (nada muda aqui). */
  update: (changes: Record<string, ConfigValue>, persist?: boolean) => Promise<ConfigUpdateResult>;
  reload: () => Promise<void>;
}

function merge(c: ConfigInfo, r: ConfigUpdateResult): ConfigInfo {
  const values = { ...c.values };
  const providers = { ...c.providers_enabled };
  for (const [k, v] of Object.entries(r.values ?? {})) {
    if (k.startsWith("avatares.")) values.avatares = { ...(values.avatares as Record<string, unknown>), [k.slice(9)]: v };
    else if (k.startsWith("providers.")) providers[k.slice(10) as keyof typeof providers] = v === true;
    else values[k] = v;
  }
  return { ...c, values, providers_enabled: providers, pending: r.pending ?? c.pending, revision: r.revision ?? c.revision, instance: r.instance ?? c.instance };
}

export function useConfig(): ConfigStore {
  const { api, snapshot } = useOmbro();
  const [config, setConfig] = useState<ConfigInfo | null>(null);
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [emCurso, setEmCurso] = useState<Record<string, number>>({});
  const revisao = useRef(-1); // a maior revisão já vista deste ombrod
  const instancia = useRef<string | undefined>(undefined);
  const fila = useRef<Promise<unknown>>(Promise.resolve());
  const vivo = useRef(true);

  /** Esta resposta é a mais nova que já vimos? (Outro ombrod: sim, e as revisões recomeçam.) */
  const maisNova = useCallback((r: { revision?: number; instance?: string }) => {
    if (r.instance !== instancia.current) {
      instancia.current = r.instance;
      revisao.current = -1;
    }
    const n = r.revision ?? 0;
    if (n < revisao.current) return false; // velha: já vimos uma mudança mais nova
    revisao.current = n;
    return true;
  }, []);

  const aceitar = useCallback(
    (c: ConfigInfo) => {
      if (!maisNova(c)) return;
      setConfig(c);
      setError(null);
    },
    [maisNova],
  );

  const reload = useCallback(async () => {
    try {
      const c = await api.configGet();
      if (vivo.current) aceitar(c);
    } catch (e) {
      if (vivo.current) setError(errorMessage(e));
    }
  }, [api, aceitar]);

  // lê ao abrir a página e de novo quando o ombrod muda (reiniciou, ou voltou): as revisões recomeçam
  const conectado = snapshot?.daemon?.state === "connected";
  const pid = snapshot?.daemon?.state === "connected" ? snapshot.daemon.pid : null;
  useEffect(() => {
    vivo.current = true;
    reload();
    api
      .configSchema()
      .then((s) => vivo.current && setSchema(s))
      .catch(() => undefined);
    return () => {
      vivo.current = false;
    };
  }, [api, reload, conectado, pid]);

  const update = useCallback(
    (changes: Record<string, ConfigValue>, persist = true) => {
      const chaves = Object.keys(changes);
      const contar = (d: number) =>
        setEmCurso((m) => {
          const n = { ...m };
          for (const k of chaves) n[k] = Math.max(0, (n[k] ?? 0) + d);
          return n;
        });
      contar(1);
      const enviar = async () => {
        const r = await api.configUpdate(changes, persist);
        if (vivo.current) {
          if (r.values === undefined) reload(); // um ombrod de antes deste contrato: lê de novo
          else if (maisNova(r)) setConfig((c) => (c ? merge(c, r) : c));
        }
        return r;
      };
      // uma de cada vez, na ordem dos cliques; a falha de uma não impede a seguinte
      const p = fila.current.then(enviar, enviar);
      fila.current = p.catch(() => undefined);
      return p.finally(() => vivo.current && contar(-1));
    },
    [api, reload, maisNova],
  );

  const options = useMemo(() => new Map((schema?.options ?? []).map((o) => [o.key, o])), [schema]);
  const busy = useCallback((k: string) => (emCurso[k] ?? 0) > 0, [emCurso]);
  return { config, schema, options, values: config?.values ?? {}, pending: config?.pending ?? {}, error, busy, update, reload };
}
