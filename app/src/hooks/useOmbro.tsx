import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { errorMessage, getApi, type OmbroApi } from "../services/api";
import type { AppInfo, AppPrefs, Snapshot } from "../types/ombro";

export type Page = "dashboard" | "agents" | "display" | "appearance" | "diagnostics" | "settings";
export const PAGES: Page[] = ["dashboard", "agents", "display", "appearance", "diagnostics", "settings"];

interface OmbroContext {
  api: OmbroApi;
  snapshot: Snapshot | null;
  info: AppInfo | null;
  prefs: AppPrefs | null;
  setPrefs: (p: AppPrefs) => void;
  reloadInfo: () => Promise<void>;
  page: Page;
  navigate: (p: Page) => void;
}

const Ctx = createContext<OmbroContext | null>(null);

export function useOmbro(): OmbroContext {
  const c = useContext(Ctx);
  if (!c) throw new Error("useOmbro fora do OmbroProvider");
  return c;
}

/** Carrega a API e assina o retrato do monitor (o único polling dos dados gerais fica no lado Rust). */
export function OmbroProvider({ children, initialPage = "dashboard" }: { children: ReactNode; initialPage?: Page }) {
  const [api, setApi] = useState<OmbroApi | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [prefs, setPrefs] = useState<AppPrefs | null>(null);
  const [page, setPage] = useState<Page>(initialPage);
  const [fatal, setFatal] = useState<string | null>(null);
  const seq = useRef(-1);

  useEffect(() => {
    let vivo = true;
    const desfazer: (() => void)[] = [];
    (async () => {
      try {
        const a = await getApi();
        if (!vivo) return;
        setApi(a);
        const aceitar = (s: Snapshot) => {
          // retratos chegam em ordem, mas o get_snapshot inicial pode chegar depois do primeiro evento
          if (s.seq >= seq.current) {
            seq.current = s.seq;
            setSnapshot(s);
          }
        };
        desfazer.push(await a.onSnapshot(aceitar));
        desfazer.push(
          await a.onNavigate((p) => {
            if ((PAGES as string[]).includes(p)) setPage(p as Page);
          }),
        );
        const [s, i, p] = await Promise.all([a.getSnapshot(), a.appInfo(), a.prefsGet()]);
        if (!vivo) return;
        aceitar(s);
        setInfo(i);
        setPrefs(p);
      } catch (e) {
        if (vivo) setFatal(errorMessage(e));
      }
    })();
    return () => {
      vivo = false;
      desfazer.forEach((f) => f());
    };
  }, []);

  const reloadInfo = useCallback(async () => {
    if (api) setInfo(await api.appInfo());
  }, [api]);

  const value = useMemo(
    () => (api ? { api, snapshot, info, prefs, setPrefs, reloadInfo, page, navigate: setPage } : null),
    [api, snapshot, info, prefs, reloadInfo, page],
  );

  if (fatal) {
    return (
      <div role="alert" style={{ padding: 32 }}>
        <h1>O Ombro não conseguiu iniciar a interface</h1>
        <p className="secondary" style={{ marginTop: 8 }}>
          {fatal}
        </p>
      </div>
    );
  }
  if (!value) return null;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
