import { useState } from "react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { Badge, Button, Card, Dialog, KeyValues, Notice, PageHeader, Spinner, Toggle } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { useConfig } from "../../hooks/useConfig";
import { useOmbro } from "../../hooks/useOmbro";
import { errorMessage } from "../../services/api";
import { ago, providerBadge, providerName, PROVIDER_NAMES } from "../../lib/format";
import { PROVIDER_IDS, type IntegrationCheck, type IntegrationRunResult, type IntegrationState, type ProviderId } from "../../types/ombro";
import { ProviderAvatar } from "../avatars/ProviderAvatar";
import { RestartNotice } from "../display/Display";

const CAPS: Record<string, string> = {
  sessions: "Sessões",
  attention: "Quem espera você",
  token_usage: "Tokens",
  quota_windows: "Limites de uso",
  events: "Eventos na hora",
  context_usage: "Contexto",
  costs: "Custo",
};

const INTEGRATION: Record<IntegrationState, { label: string; tone: "green" | "warning" | "critical" | "neutral" }> = {
  configured: { label: "Integrada", tone: "green" },
  partial: { label: "Incompleta", tone: "warning" },
  not_configured: { label: "Não instalada", tone: "neutral" },
  conflict: { label: "Conflito", tone: "critical" },
  invalid: { label: "Arquivo inválido", tone: "critical" },
};

const PURPOSE: Record<ProviderId, string> = {
  claude:
    "O Claude Code já funciona sem hooks (o Ombro lê os arquivos locais). Os hooks acrescentam quando uma sessão pede aprovação ou para com erro.",
  codex: "Sem os hooks, o Ombro não vê as sessões do Codex. Depois de instalar, confie nos hooks no Codex (/hooks).",
  cursor: "Sem os hooks, o Ombro não vê as sessões do Cursor. O Cursor recarrega o hooks.json sozinho.",
};

type Op = "install" | "uninstall" | "rollback";
const OP_TEXT: Record<Op, { title: (n: string) => string; confirm: string; done: string }> = {
  install: { title: (n) => `Integrar o ${n}`, confirm: "Instalar integração", done: "Integração instalada" },
  uninstall: { title: (n) => `Remover a integração do ${n}`, confirm: "Remover integração", done: "Integração removida" },
  rollback: { title: (n) => `Desfazer a última mudança no ${n}`, confirm: "Restaurar backup", done: "Arquivo restaurado do backup" },
};

function Diff({ text }: { text: string }) {
  return (
    <pre className="code selectable" aria-label="O que muda no arquivo">
      {text.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@") ? "meta" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}>
          {l || " "}
        </div>
      ))}
    </pre>
  );
}

interface Flow {
  provider: ProviderId;
  op: Op;
  stage: "preview" | "running" | "done" | "error";
  preview?: Extract<IntegrationRunResult, { ok: true }>;
  result?: Extract<IntegrationRunResult, { ok: true }>;
  error?: string;
}

/** Instalar, conferir e remover os hooks: sempre pelo gerenciador da Fase 4 (CLI), com dry-run e confirmação. */
export function Agents() {
  const { api, snapshot, info } = useOmbro();
  const lista = useAsync(() => api.integrationsList(), []);
  const doctor = useAsync(() => api.doctorRun(), []);
  const config = useConfig();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);
  const [toggleMsg, setToggleMsg] = useState<string | null>(null);

  const checks = new Map((lista.data?.result.integrations ?? []).map((c) => [c.provider, c]));
  const provs = new Map((snapshot?.providers?.providers ?? []).map((p) => [p.id, p]));
  const versoes = new Map(
    (doctor.data?.result.checks ?? [])
      .filter((c) => c.id.startsWith("provider."))
      .map((c) => [c.id.slice(9), (c.details?.version as string | null | undefined) ?? null]),
  );

  const abrir = async (provider: ProviderId, op: Op) => {
    setFlow({ provider, op, stage: "preview" });
    try {
      const r = await api.integrationRun(provider, op, true);
      if (!r.result.ok) setFlow({ provider, op, stage: "error", error: r.result.error });
      else setFlow({ provider, op, stage: "preview", preview: r.result });
    } catch (e) {
      setFlow({ provider, op, stage: "error", error: errorMessage(e) });
    }
  };

  const confirmar = async () => {
    if (!flow) return;
    setFlow({ ...flow, stage: "running" });
    try {
      const r = await api.integrationRun(flow.provider, flow.op, false);
      if (!r.result.ok) setFlow({ ...flow, stage: "error", error: r.result.error });
      else setFlow({ ...flow, stage: "done", result: r.result });
    } catch (e) {
      setFlow({ ...flow, stage: "error", error: errorMessage(e) });
    }
    lista.reload();
  };

  const conferir = async (provider: ProviderId) => {
    await api.integrationRun(provider, "check", false).catch(() => undefined);
    lista.reload();
  };

  const ligar = async (provider: ProviderId, v: boolean) => {
    setToggling(provider);
    setToggleMsg(null);
    try {
      await config.update({ [`providers.${provider}`]: v });
    } catch (e) {
      setToggleMsg(errorMessage(e));
    } finally {
      setToggling(null);
    }
  };

  const pendentes = Object.keys(config.pending).filter((k) => k.startsWith("providers."));
  const nome = flow ? PROVIDER_NAMES[flow.provider] : "";
  const texto = flow ? OP_TEXT[flow.op] : null;

  return (
    <>
      <PageHeader
        title="Agentes"
        subtitle="As integrações são opcionais e só mudam com a sua confirmação."
        actions={
          <Button variant="ghost" icon={<RefreshCw size={14} />} pending={lista.loading} onClick={() => lista.reload()}>
            Conferir de novo
          </Button>
        }
      />
      {info?.location_problem && <Notice tone="warning" title="Instalar integrações está bloqueado">{info.location_problem}</Notice>}
      {lista.error && <Notice tone="critical" title="Não consegui ler as integrações">{lista.error}</Notice>}
      {toggleMsg && <Notice tone="critical">{toggleMsg}</Notice>}
      {pendentes.length > 0 && <RestartNotice key={pendentes.join(",")} keys={pendentes} onDone={() => config.reload()} />}
      {!lista.data && lista.loading && <Spinner label="Conferindo os agentes" />}

      <div className="stack">
        {PROVIDER_IDS.map((id) => {
          const c: IntegrationCheck | undefined = checks.get(id);
          const p = provs.get(id);
          const integ = c ? INTEGRATION[c.state] : null;
          // o switch mostra o que está gravado (o que você escolheu); se ainda não está em uso, o aviso acima diz
          const gravado = config.pending[`providers.${id}`];
          const ligado = typeof gravado === "boolean" ? gravado : (config.config?.providers_enabled?.[id] ?? p?.enabled ?? true);
          const desatualizada = c && (c.desatualizados.length > 0 || c.sobrando.length > 0);
          const badge = p ? providerBadge(p) : null;
          return (
            <Card key={id} className="agent-card" aria-label={PROVIDER_NAMES[id]}>
              <div className="card-title">
                <h2>
                  <ProviderAvatar p={p ?? { id, name: PROVIDER_NAMES[id] }} />
                  {p ? providerName(p) : PROVIDER_NAMES[id]}
                </h2>
                <div className="row">
                  {integ && <Badge tone={integ.tone}>{integ.label}</Badge>}
                  {badge && p?.enabled && p.state !== "nao_configurado" && p.state !== "nao_instalado" && <Badge tone={badge.tone}>{badge.label}</Badge>}
                </div>
              </div>
              <div className="agent-grid">
                <KeyValues
                  items={[
                    ["Instalado", c ? (c.instalado ? "Sim" : "Não encontrado neste Mac") : "—"],
                    ["Versão", doctor.loading && !doctor.data ? "conferindo…" : (versoes.get(id) ?? "—")],
                    ["Hooks", c ? `${c.presentes.length} de ${c.eventos.length} eventos` : "—"],
                    ["Arquivo", <span className="mono selectable">{c?.file ?? "—"}</span>],
                    ["Sessões agora", p?.enabled ? String(p.sessions ?? 0) : "—"],
                    ["Última atualização", p?.enabled ? ago(p.last_event_at ?? p.last_collected_at) : "—"],
                    [
                      "Informa",
                      (p?.capabilities ?? []).length ? (
                        <span className="caps">
                          {p!.capabilities!.map((k) => (
                            <span key={k} className="cap">
                              {CAPS[k] ?? k}
                            </span>
                          ))}
                        </span>
                      ) : (
                        "—"
                      ),
                    ],
                  ]}
                />
                <div className="agent-side">
                  <p className="secondary small">{PURPOSE[id]}</p>
                  {c?.erro && <Notice tone="critical">{c.erro}</Notice>}
                  {c?.state === "conflict" && (
                    <Notice tone="critical" title="Entradas que o Ombro não reconhece">
                      {c.file} já cita o ombro_hook.py de um jeito diferente ({c.ambiguos.join(", ")}). Para não apagar algo seu,
                      o Ombro não mexe: ajuste à mão.
                    </Notice>
                  )}
                  {desatualizada && <Notice tone="warning">Algumas entradas apontam para outro caminho ou eventos antigos: repare para atualizar.</Notice>}
                  <div className="row">
                    {c && c.state === "not_configured" && (
                      <Button variant="primary" disabled={!c.instalado || !!info?.location_problem} onClick={() => abrir(id, "install")}>
                        Instalar integração
                      </Button>
                    )}
                    {c && (c.state === "partial" || desatualizada) && (
                      <Button variant="primary" disabled={!!info?.location_problem} onClick={() => abrir(id, "install")}>
                        Reparar integração
                      </Button>
                    )}
                    {c && c.state !== "not_configured" && c.state !== "invalid" && (
                      <Button onClick={() => conferir(id)} icon={<ShieldCheck size={14} />}>
                        Conferir
                      </Button>
                    )}
                    {c && (c.state === "configured" || c.state === "partial") && (
                      <Button variant="danger" onClick={() => abrir(id, "uninstall")}>
                        Remover
                      </Button>
                    )}
                    {c && c.existe && (
                      <Button variant="ghost" onClick={() => abrir(id, "rollback")}>
                        Desfazer última mudança
                      </Button>
                    )}
                  </div>
                  <Toggle
                    label="Acompanhar este agente"
                    description="Desligado, o Ombro não coleta nada dele (vale ao reiniciar o monitoramento)."
                    checked={ligado}
                    pending={toggling === id}
                    onChange={(v) => ligar(id, v)}
                  />
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      <Dialog
        open={!!flow}
        wide
        title={flow && texto ? texto.title(nome) : ""}
        onClose={() => flow?.stage !== "running" && setFlow(null)}
        actions={
          flow?.stage === "done" || flow?.stage === "error" ? (
            <>
              {flow.stage === "error" && <Button onClick={() => abrir(flow.provider, flow.op)}>Tentar de novo</Button>}
              <Button variant="primary" onClick={() => setFlow(null)}>
                Fechar
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" disabled={flow?.stage === "running"} onClick={() => setFlow(null)}>
                Cancelar
              </Button>
              <Button
                variant={flow?.op === "uninstall" ? "danger" : "primary"}
                pending={flow?.stage === "running"}
                disabled={!flow?.preview || !flow.preview.changed}
                onClick={confirmar}
              >
                {texto?.confirm}
              </Button>
            </>
          )
        }
      >
        {flow && <FlowBody flow={flow} nome={nome} />}
      </Dialog>
    </>
  );
}

function FlowBody({ flow, nome }: { flow: Flow; nome: string }) {
  if (flow.stage === "error") return <Notice tone="critical" title="Nada foi alterado">{flow.error}</Notice>;
  if (flow.stage === "done" && flow.result) {
    const r = flow.result;
    return (
      <>
        <Notice tone="success" title={r.changed ? OP_TEXT[flow.op].done : "Nada a mudar"}>
          {r.changed ? `Arquivo: ${r.file}` : "O arquivo já estava assim."}
        </Notice>
        {r.backup && (
          <p>
            Backup do arquivo anterior: <span className="mono selectable">{r.backup}</span>
          </p>
        )}
        {r.note && <p>{r.note}</p>}
      </>
    );
  }
  if (!flow.preview) return <Spinner label="Calculando o que muda (nada é gravado)" />;
  const p = flow.preview;
  if (!p.changed) return <Notice tone="info" title="Nada a mudar">{p.file} já está assim.</Notice>;
  return (
    <>
      {flow.op === "install" && (
        <>
          <p>
            O Ombro vai acrescentar os <strong>hooks</strong> dele em <span className="mono">{p.file}</span>. O {nome} passa a
            chamar um script pequeno do Ombro em alguns eventos (início e fim de sessão, aprovação, erro).
          </p>
          <ul>
            <li>O hook guarda só: o agente, a sessão, o evento, o horário, o nome da pasta do projeto e um status.</li>
            <li>Nunca guarda prompts, respostas, comandos, conteúdo de arquivos, caminhos completos ou credenciais.</li>
            <li>Não usa rede e nunca bloqueia o agente. O resto do arquivo fica como está; antes, um backup é feito.</li>
          </ul>
        </>
      )}
      {flow.op === "uninstall" && <p>Só as entradas do Ombro saem de {p.file}; os seus outros hooks ficam. Antes, um backup é feito.</p>}
      {flow.op === "rollback" && <p>O arquivo volta para o último backup do Ombro. O estado de agora vira um backup (dá para desfazer).</p>}
      <Diff text={p.diff || "(arquivo novo)"} />
    </>
  );
}
