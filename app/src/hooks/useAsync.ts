import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "../services/api";

export interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
}

/** Carrega `fn` ao montar (e quando `deps` mudam); mantém o último dado bom enquanto recarrega. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const vivo = useRef(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fnRef.current();
      if (vivo.current) {
        setData(r);
        setError(null);
      }
    } catch (e) {
      if (vivo.current) setError(errorMessage(e));
    } finally {
      if (vivo.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    vivo.current = true;
    reload();
    return () => {
      vivo.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload };
}

export type ActionState<T> =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "done"; result: T }
  | { status: "error"; message: string };

/** Uma ação do usuário: "pendente" até o backend confirmar; o sucesso só aparece com a resposta real. */
export function useAction<A extends unknown[], T>(fn: (...args: A) => Promise<T>) {
  const [state, setState] = useState<ActionState<T>>({ status: "idle" });
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const run = useCallback(async (...args: A): Promise<T | undefined> => {
    setState({ status: "pending" });
    try {
      const result = await fnRef.current(...args);
      setState({ status: "done", result });
      return result;
    } catch (e) {
      setState({ status: "error", message: errorMessage(e) });
      return undefined;
    }
  }, []);
  const reset = useCallback(() => setState({ status: "idle" }), []);
  return { state, run, reset, pending: state.status === "pending" };
}

/** Chama `fn` a cada `ms` enquanto `enabled` e a janela está visível (nada roda com ela oculta). */
export function useInterval(fn: () => void, ms: number, enabled = true): void {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let t: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (t == null && !document.hidden) t = setInterval(() => fnRef.current(), ms);
    };
    const stop = () => {
      if (t != null) clearInterval(t);
      t = null;
    };
    const onVis = () => (document.hidden ? stop() : (fnRef.current(), start()));
    start();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [ms, enabled]);
}
