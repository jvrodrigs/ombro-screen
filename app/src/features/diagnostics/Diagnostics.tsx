import { useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Copy, ExternalLink, MinusCircle, RefreshCw, XCircle } from "lucide-react";
import { Button, Card, Notice, PageHeader, Spinner } from "../../components/ui";
import { useAction, useAsync } from "../../hooks/useAsync";
import { useOmbro, type Page } from "../../hooks/useOmbro";
import type { CheckStatus, DoctorCheck } from "../../types/ombro";

const STATUS: Record<CheckStatus, { label: string; icon: typeof CheckCircle2; tone: string }> = {
  PASS: { label: "OK", icon: CheckCircle2, tone: "green" },
  WARN: { label: "Atenção", icon: AlertTriangle, tone: "warning" },
  FAIL: { label: "Falha", icon: XCircle, tone: "critical" },
  SKIP: { label: "Não se aplica", icon: MinusCircle, tone: "muted" },
};

const CATEGORIES: [string, string][] = [
  ["service", "Serviço"],
  ["providers", "Agentes"],
  ["display", "Tela USB"],
  ["storage", "Dados"],
  ["configuration", "Configuração"],
  ["environment", "Ambiente"],
];

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

/** A ação do app para uma verificação, só quando há uma recuperação conhecida e segura. */
function useCheckAction(c: DoctorCheck): ReactNode {
  const { api, navigate } = useOmbro();
  const reconectar = useAction(() => api.daemonStart());
  const ir = (p: Page, label: string) => (
    <Button size="sm" onClick={() => navigate(p)}>
      {label}
    </Button>
  );
  if (c.status === "PASS" || c.status === "SKIP") return null;
  if (c.id === "service.launchagent" || c.id === "service.instances") return ir("settings", "Ver migração e serviço");
  if (c.id === "service.daemon" || c.id === "service.socket")
    return (
      <Button size="sm" pending={reconectar.pending} onClick={() => reconectar.run()}>
        Reconectar serviço
      </Button>
    );
  if (c.id.startsWith("provider.")) return ir("agents", "Abrir Agentes");
  if (c.id.startsWith("display.")) return ir("display", "Abrir Tela");
  if (c.id.startsWith("config.")) return ir("settings", "Abrir Ajustes");
  return null;
}

function CheckRow({ c, dev }: { c: DoctorCheck; dev: boolean }) {
  const s = STATUS[c.status];
  const Icon = s.icon;
  const acao = useCheckAction(c);
  return (
    <li className={`check check-${c.status.toLowerCase()}`}>
      <Icon size={16} className={`tone-${s.tone}`} aria-hidden />
      <div className="check-body">
        <div className="spread">
          <strong>{c.title}</strong>
          <span className={`small tone-${s.tone}`}>{s.label}</span>
        </div>
        <p className="secondary selectable">{c.message}</p>
        {c.action && dev && <p className="muted small mono selectable">No terminal: {c.action}</p>}
        {acao && <div className="check-action">{acao}</div>}
      </div>
    </li>
  );
}

export function Diagnostics() {
  const { api, info } = useOmbro();
  const doctor = useAsync(() => api.doctorRun(), []);
  const [copiado, setCopiado] = useState<string | null>(null);
  const relatorio = useAction(async () => {
    const r = await api.doctorReport();
    const ok = await copyText(r);
    setCopiado(ok ? "Relatório copiado. Ele não leva nomes de projeto, prompts, credenciais nem o seu usuário." : "Não consegui copiar.");
    return r;
  });
  const r = doctor.data?.result;
  const dev = info?.mode === "development";

  return (
    <>
      <PageHeader
        title="Diagnóstico"
        subtitle="Confere a instalação inteira sem mudar nada."
        actions={
          <>
            <Button icon={<Copy size={14} />} pending={relatorio.pending} onClick={() => relatorio.run()}>
              Copiar relatório
            </Button>
            <Button variant="primary" icon={<RefreshCw size={14} />} pending={doctor.loading} onClick={() => doctor.reload()}>
              Verificar de novo
            </Button>
          </>
        }
      />
      {copiado && (
        <Notice
          tone="success"
          actions={
            <Button size="sm" variant="ghost" icon={<ExternalLink size={13} />} onClick={() => api.openLink("issues")}>
              Abrir issues no GitHub
            </Button>
          }
        >
          {copiado}
        </Notice>
      )}
      {relatorio.state.status === "error" && <Notice tone="critical">{relatorio.state.message}</Notice>}
      {doctor.error && (
        <Notice tone="critical" title="O diagnóstico não rodou">
          {doctor.error}
        </Notice>
      )}
      {!r && doctor.loading && <Spinner label="Verificando" />}

      {r && (
        <>
          <div className="grid grid-stats doctor-summary" role="list" aria-label="Resumo do diagnóstico">
            {(["FAIL", "WARN", "PASS", "SKIP"] as CheckStatus[]).map((k) => {
              const s = STATUS[k];
              const Icon = s.icon;
              return (
                <div key={k} className="stat" role="listitem">
                  <div className="stat-label muted">{s.label}</div>
                  <div className={`stat-value mono tone-${s.tone}`}>
                    <Icon size={18} aria-hidden /> {r.summary[k.toLowerCase() as "pass"]}
                  </div>
                </div>
              );
            })}
          </div>

          {CATEGORIES.map(([cat, nome]) => {
            const checks = r.checks
              .filter((c) => c.category === cat)
              .sort((a, b) => ["FAIL", "WARN", "PASS", "SKIP"].indexOf(a.status) - ["FAIL", "WARN", "PASS", "SKIP"].indexOf(b.status));
            if (!checks.length) return null;
            return (
              <section key={cat}>
                <h2 className="section-title">{nome}</h2>
                <Card>
                  <ul className="checks">
                    {checks.map((c) => (
                      <CheckRow key={c.id} c={c} dev={dev} />
                    ))}
                  </ul>
                </Card>
              </section>
            );
          })}
          <p className="muted small" style={{ marginTop: 16 }}>
            Gerado {new Date(r.generated_at).toLocaleTimeString("pt-BR")} · Ombro {r.ombro_version} · protocolo IPC v{r.ipc_protocol}. O log do serviço
            fica em ~/Library/Logs/ombro-screen/ombrod.log.
          </p>
        </>
      )}
    </>
  );
}
