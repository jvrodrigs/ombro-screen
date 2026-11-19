import { useEffect, useRef, type ComponentType } from "react";
import { Activity, Bot, Monitor, Palette, Settings2, Stethoscope } from "lucide-react";
import { CloudMark } from "./components/CloudMark";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { PageBoundary } from "./components/ErrorBoundary";
import { Dashboard } from "./features/dashboard/Dashboard";
import { Agents } from "./features/agents/Agents";
import { DisplayPage } from "./features/display/Display";
import { Appearance } from "./features/appearance/Appearance";
import { Diagnostics } from "./features/diagnostics/Diagnostics";
import { Settings } from "./features/settings/Settings";
import { Onboarding } from "./features/onboarding/Onboarding";
import { PacoteDoOmbro } from "./features/avatars/pacote";
import { PAGES, useOmbro, type Page } from "./hooks/useOmbro";

const NAV: Record<Page, { label: string; icon: ComponentType<{ size?: number }>; component: ComponentType }> = {
  dashboard: { label: "Painel", icon: Activity, component: Dashboard },
  agents: { label: "Agentes", icon: Bot, component: Agents },
  display: { label: "Tela", icon: Monitor, component: DisplayPage },
  appearance: { label: "Aparência", icon: Palette, component: Appearance },
  diagnostics: { label: "Diagnóstico", icon: Stethoscope, component: Diagnostics },
  settings: { label: "Ajustes", icon: Settings2, component: Settings },
};

function Sidebar() {
  const { page, navigate, snapshot } = useOmbro();
  const st = snapshot?.status;
  const conectado = snapshot?.daemon?.state === "connected";
  const tom = !conectado ? "critical" : st?.daemon === "running" ? "green" : "warning";
  const texto = !snapshot ? "Conectando…" : !conectado ? "Serviço parado" : st?.daemon === "running" ? "Monitorando" : "Com avisos";
  return (
    <nav className="sidebar" aria-label="Seções">
      <div className="sidebar-drag" data-tauri-drag-region />
      <div className="brand" data-tauri-drag-region>
        <CloudMark />
        <span>Ombro</span>
      </div>
      <ul>
        {PAGES.map((p, i) => {
          const { label, icon: Icon } = NAV[p];
          return (
            <li key={p}>
              <button
                type="button"
                className={`nav-item ${page === p ? "active" : ""}`}
                aria-current={page === p ? "page" : undefined}
                onClick={() => navigate(p)}
                title={`${label} (⌘${i + 1})`}
              >
                <Icon size={16} />
                <span>{label}</span>
                {p === "dashboard" && (st?.waiting ?? 0) > 0 && (
                  <span className="nav-count" aria-label={`${st!.waiting} esperando você`}>
                    {st!.waiting}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="sidebar-footer">
        <span className={`badge tone-${tom}`}>
          <span className="dot" aria-hidden />
          {texto}
        </span>
      </div>
    </nav>
  );
}

export default function App() {
  const { page, navigate, prefs } = useOmbro();
  const main = useRef<HTMLElement>(null);
  useEffect(() => {
    if (main.current) main.current.scrollTop = 0; // cada seção abre do topo
  }, [page]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.metaKey || e.altKey || e.ctrlKey) return;
      const n = Number(e.key);
      if (n >= 1 && n <= PAGES.length) {
        e.preventDefault();
        navigate(PAGES[n - 1]);
      } else if (e.key === ",") {
        e.preventDefault();
        navigate("settings");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  if (prefs && !prefs.onboarding_completed) return <Onboarding />;

  const Current = NAV[page].component;
  return (
    // todos os mascotes do app seguem o pacote de animações em uso no ombrod (o mesmo da telinha)
    <PacoteDoOmbro>
    <div className="shell">
      <Sidebar />
      <main ref={main} className="content" aria-labelledby="page-title">
        <div className="content-drag" data-tauri-drag-region />
        <div className="content-inner">
          <ConnectionBanner />
          <PageBoundary key={page}>
            <Current />
          </PageBoundary>
        </div>
      </main>
    </div>
    </PacoteDoOmbro>
  );
}
