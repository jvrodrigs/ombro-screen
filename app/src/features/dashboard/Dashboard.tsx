import { useEffect, useState } from "react";
import { Hand, RefreshCw } from "lucide-react";
import { Badge, Card, Empty, PageHeader, Button, UsageBar } from "../../components/ui";
import { useOmbro } from "../../hooks/useOmbro";
import { DASH, PROVIDER_NAMES, SESSION_STATUS, ago, bytes, percent } from "../../lib/format";
import type { DaemonStatus, Session } from "../../types/ombro";
import { ProviderCard } from "./ProviderCard";
import { SessionAvatar } from "../avatars/ProviderAvatar";

const URGENTES = new Set(["approval", "waiting", "error"]);
const ORDEM: Record<string, number> = { error: 0, approval: 1, waiting: 2, working: 3, idle: 4, unknown: 5 };

function displayText(st: DaemonStatus | null | undefined): { label: string; tone: "green" | "muted" | "warning" | "critical" | "blue" } {
  const d = st?.display;
  if (!d) return { label: DASH, tone: "muted" };
  if (d.kind === "usb") {
    if (d.connection === "connected") return { label: "Conectada", tone: "green" };
    if (d.connection === "conflict") return { label: "Em uso por outro Ombro", tone: "warning" };
    if (d.connection === "error") return { label: "Com erro", tone: "critical" };
    return { label: "Desconectada", tone: "muted" };
  }
  if (d.kind === "simulator") return { label: "Só a prévia", tone: "blue" };
  return { label: "Sem tela", tone: "muted" };
}

function useNow(ms = 15_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export function SessionList({ sessions, now }: { sessions: Session[]; now: Date }) {
  const { snapshot } = useOmbro();
  const provs = new Map((snapshot?.providers?.providers ?? []).map((p) => [p.id, p]));
  const modo = snapshot?.providers?.random_mode;
  return (
    <ul className="session-list">
      {sessions.map((s) => {
        const st = SESSION_STATUS[s.status] ?? SESSION_STATUS.unknown;
        return (
          <li key={`${s.provider}:${s.session_id}`}>
            <span className="session-project">
              <SessionAvatar provider={provs.get(s.provider)} sessionId={s.session_id} status={s.status} mode={modo} />
              {s.project ?? "projeto sem nome"}
            </span>
            <span className="muted">{PROVIDER_NAMES[s.provider] ?? s.provider}</span>
            <Badge tone={st.tone}>{st.label}</Badge>
            <span className="muted mono session-since">{ago(s.status_since ?? s.updated_at, now)}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function Dashboard() {
  const { snapshot, api, navigate } = useOmbro();
  const now = useNow();
  const [girando, setGirando] = useState(false);
  const st = snapshot?.status;
  const provs = snapshot?.providers?.providers ?? [];
  const sessoes = [...(snapshot?.sessions?.sessions ?? [])].sort((a, b) => (ORDEM[a.status] ?? 9) - (ORDEM[b.status] ?? 9));
  const urgentes = sessoes.filter((s) => URGENTES.has(s.status));
  const trabalhando = provs.reduce((n, p) => n + (p.working ?? 0), 0);
  const tela = displayText(st);
  const sys = st?.system;
  const ramPct = sys ? (sys.ram_used_bytes / sys.ram_total_bytes) * 100 : null;

  const atualizar = async () => {
    setGirando(true);
    await api.refresh();
    setTimeout(() => setGirando(false), 600);
  };

  return (
    <>
      <PageHeader
        title="Painel"
        subtitle={st?.collected_at ? `Coletado ${ago(st.collected_at, now)}` : "Esperando a primeira coleta"}
        actions={
          <Button variant="ghost" icon={<RefreshCw size={14} className={girando ? "spin" : ""} />} onClick={atualizar}>
            Atualizar
          </Button>
        }
      />

      <div className="grid grid-stats" role="list" aria-label="Resumo">
        <Stat label="Sessões abertas" value={st ? String(st.sessions) : DASH} />
        <Stat label="Trabalhando" value={st ? String(trabalhando) : DASH} tone={trabalhando ? "orange" : undefined} />
        <Stat label="Esperando você" value={st ? String(st.waiting) : DASH} tone={st?.waiting ? "warning" : undefined} />
        <Stat label="Tela" value={tela.label} tone={tela.tone} small />
        <Stat
          label="CPU · RAM"
          value={sys ? `${percent(sys.cpu_percent)} · ${percent(ramPct)}` : DASH}
          hint={sys ? `${bytes(sys.ram_used_bytes)} de ${bytes(sys.ram_total_bytes)}${sys.memory_pressure && sys.memory_pressure !== "normal" ? ` · pressão ${sys.memory_pressure === "critical" ? "crítica" : "alta"}` : ""}` : undefined}
          small
        />
      </div>

      {urgentes.length > 0 && (
        <>
          <h2 className="section-title">Precisa de você</h2>
          <Card className="attention-card">
            <SessionList sessions={urgentes} now={now} />
          </Card>
        </>
      )}

      <h2 className="section-title">Agentes</h2>
      {provs.length === 0 ? (
        <Card>
          <Empty title={snapshot?.status ? "Primeira coleta em andamento" : "Sem dados dos agentes"}>
            {snapshot?.status ? "Os agentes aparecem em alguns segundos." : "Os agentes aparecem quando o serviço do Ombro estiver rodando."}
          </Empty>
        </Card>
      ) : (
        <div className="grid grid-3">
          {provs.map((p) => (
            <ProviderCard key={p.id} p={p} now={now} onConfigure={() => navigate("agents")} />
          ))}
        </div>
      )}

      <h2 className="section-title">Sessões</h2>
      <Card>
        {sessoes.length ? (
          <SessionList sessions={sessoes} now={now} />
        ) : (
          <Empty icon={<Hand size={22} />} title="Nenhuma sessão aberta">
            Quando você usar o Claude Code, o Codex ou o Cursor, as sessões aparecem aqui.
          </Empty>
        )}
      </Card>

      {sys && (
        <>
          <h2 className="section-title">Mac</h2>
          <Card className="mac-card">
            <UsageBar label="CPU" value={sys.cpu_percent} />
            <UsageBar label="RAM" value={ramPct} accent="blue" hint={`${bytes(sys.ram_used_bytes)} de ${bytes(sys.ram_total_bytes)}`} />
          </Card>
        </>
      )}
    </>
  );
}

function Stat({ label, value, tone, hint, small }: { label: string; value: string; tone?: string; hint?: string; small?: boolean }) {
  return (
    <div className="stat" role="listitem">
      <div className="stat-label muted">{label}</div>
      <div className={`stat-value ${small ? "small" : "mono"} ${tone ? `tone-${tone}` : ""}`}>{value}</div>
      {hint && <div className="stat-hint muted">{hint}</div>}
    </div>
  );
}
