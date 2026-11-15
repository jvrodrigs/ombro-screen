import { useState } from "react";
import { ExternalLink } from "lucide-react";
import { Button, Card, Dialog, KeyValues, Notice, PageHeader, Toggle } from "../../components/ui";
import { useAction, useAsync } from "../../hooks/useAsync";
import { useOmbro } from "../../hooks/useOmbro";
import { errorMessage } from "../../services/api";
import { DASH } from "../../lib/format";
import type { ServiceAction, ServiceRunResult } from "../../types/ombro";

type Op = Extract<ServiceAction, "install" | "uninstall" | "migrate" | "rollback">;
const OPS: Record<Op, { title: string; intro: string; confirm: string }> = {
  install: {
    title: "Monitorar em segundo plano",
    intro:
      "O Ombro instala um LaunchAgent do seu usuário (sem senha de administrador). O macOS passa a iniciar o serviço ao entrar no Mac e a reiniciá-lo se ele cair, mesmo com o app fechado. O monitoramento que o app iniciou fecha antes, para não haver dois.",
    confirm: "Instalar serviço",
  },
  uninstall: {
    title: "Parar o monitoramento em segundo plano",
    intro: "O serviço para e o LaunchAgent do Ombro é apagado. O monitoramento volta a rodar só com o app aberto. Configurações e histórico ficam.",
    confirm: "Remover serviço",
  },
  migrate: {
    title: "Migrar o Ombro antigo",
    intro:
      "O LaunchAgent de antes do Ombro Hub (com.ombro-screen) é guardado num backup e desligado, e o serviço novo é instalado no lugar, com as mesmas opções que ele entende. Se o novo não ligar, o antigo volta sozinho.",
    confirm: "Migrar",
  },
  rollback: {
    title: "Desfazer a migração",
    intro: "O serviço novo é desligado e o LaunchAgent antigo volta do backup, como estava antes.",
    confirm: "Desfazer migração",
  },
};

export function Settings() {
  const { api, info, prefs, setPrefs, snapshot, reloadInfo } = useOmbro();
  const servico = useAsync(() => api.serviceStatus(), []);
  const [op, setOp] = useState<{ op: Op; stage: "preview" | "running" | "done" | "error"; steps?: string[]; error?: string } | null>(null);
  const [prefErro, setPrefErro] = useState<string | null>(null);
  const autostart = useAction((v: boolean) => api.autostartSet(v));
  const notif = useAction((v: boolean) => api.prefsSet({ notifications: v }));

  const s = servico.data?.result;
  const legado = s?.legacy.installed ?? info?.legacy_installed ?? false;
  const instalado = s?.installed ?? info?.service_installed ?? false;
  const d = snapshot?.daemon;
  const dono =
    d?.state === "connected"
      ? d.owner === "app"
        ? "O app iniciou o monitoramento: ele roda enquanto o Ombro estiver aberto (na janela ou na Menu Bar)."
        : d.owner === "launchd"
          ? "O serviço em segundo plano mantém o monitoramento, mesmo com o app fechado."
          : "Um ombrod iniciado fora do app (num terminal) está monitorando."
      : "O monitoramento não está rodando agora.";

  const abrir = async (o: Op) => {
    setOp({ op: o, stage: "preview" });
    try {
      const r = await api.serviceRun(o, true);
      setOp(r.result.ok ? { op: o, stage: "preview", steps: r.result.steps } : { op: o, stage: "error", error: r.result.error });
    } catch (e) {
      setOp({ op: o, stage: "error", error: errorMessage(e) });
    }
  };
  const confirmar = async () => {
    if (!op) return;
    setOp({ ...op, stage: "running" });
    try {
      const r = await api.serviceRun(op.op, false);
      const res: ServiceRunResult = r.result;
      setOp(res.ok ? { op: op.op, stage: "done", steps: res.steps } : { op: op.op, stage: "error", error: res.error, steps: res.steps });
    } catch (e) {
      setOp({ op: op.op, stage: "error", error: errorMessage(e) });
    }
    servico.reload();
    reloadInfo();
    api.refresh();
  };

  const mudarAutostart = async (v: boolean) => {
    const r = await autostart.run(v);
    if (r !== undefined && prefs) setPrefs({ ...prefs, autostart: r });
  };
  const mudarNotif = async (v: boolean) => {
    setPrefErro(null);
    const r = await notif.run(v);
    if (r) setPrefs(r);
    else setPrefErro(notif.state.status === "error" ? notif.state.message : null);
  };

  return (
    <>
      <PageHeader title="Ajustes" />

      <h2 className="section-title">Monitoramento</h2>
      {legado && (
        <Notice
          tone="warning"
          title="O Ombro antigo está instalado"
          actions={
            <Button variant="primary" size="sm" disabled={!!info?.location_problem} onClick={() => abrir("migrate")}>
              Ver a migração
            </Button>
          }
        >
          O LaunchAgent com.ombro-screen (tela.py) disputaria a tela USB com o serviço novo. A migração guarda um backup e é
          reversível.
          {info?.location_problem && <div className="tone-warning">{info.location_problem}</div>}
        </Notice>
      )}
      <Card>
        <p className="secondary" style={{ marginBottom: 8 }}>
          {dono}
        </p>
        <Toggle
          label="Continuar monitorando depois de sair do Ombro"
          description="Instala o serviço em segundo plano (LaunchAgent): a tela e as notificações do sistema seguem mesmo com o app fechado, e o serviço inicia ao entrar no Mac."
          checked={instalado}
          disabled={legado || !!info?.location_problem || servico.loading}
          onChange={(v) => abrir(v ? "install" : "uninstall")}
        />
        {info?.location_problem && !legado && <p className="tone-warning small">{info.location_problem}</p>}
        {(s?.backups?.length ?? 0) > 0 && !legado && (
          <div style={{ marginTop: 8 }}>
            <Button size="sm" variant="ghost" onClick={() => abrir("rollback")}>
              Desfazer a migração do Ombro antigo
            </Button>
          </div>
        )}
        {servico.error && <Notice tone="critical">{servico.error}</Notice>}
      </Card>

      <h2 className="section-title">App</h2>
      <Card>
        <Toggle
          label="Abrir o Ombro ao entrar no Mac"
          description="O app abre direto na Menu Bar, sem janela. Ele não inicia um segundo serviço se o de segundo plano já estiver instalado."
          checked={prefs?.autostart ?? false}
          pending={autostart.pending}
          disabled={!prefs}
          onChange={mudarAutostart}
        />
        <Toggle
          label="Notificações"
          description="Avisa quando uma sessão espera você, pede aprovação ou para com erro, e se o serviço cair. Sem nome de projeto nem conteúdo."
          checked={prefs?.notifications ?? false}
          pending={notif.pending}
          disabled={!prefs}
          onChange={mudarNotif}
        />
        {prefErro && (
          <Notice
            tone="warning"
            actions={
              <Button size="sm" onClick={() => api.openLink("notification_settings")}>
                Abrir Ajustes do Sistema
              </Button>
            }
          >
            {prefErro}
          </Notice>
        )}
        {autostart.state.status === "error" && <Notice tone="critical">{autostart.state.message}</Notice>}
        <p className="muted small" style={{ marginTop: 8 }}>
          Fechar a janela deixa o Ombro na Menu Bar. Para sair, use "Sair do Ombro" no menu do cloud (⌘Q).
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <Button size="sm" variant="ghost" onClick={async () => prefs && setPrefs(await api.prefsSet({ onboarding_completed: false }))}>
            Abrir o assistente de primeiro uso
          </Button>
        </div>
      </Card>

      <h2 className="section-title">Atualizações</h2>
      <Card>
        <div className="spread">
          <div>
            <p>
              Versão instalada <span className="mono">{info?.version ?? DASH}</span>
            </p>
            <p className="muted small">
              A atualização automática depende de versões assinadas e ainda não está ligada. As versões novas saem no GitHub.
            </p>
          </div>
          <Button icon={<ExternalLink size={14} />} onClick={() => api.openLink("releases")}>
            Ver versões
          </Button>
        </div>
      </Card>

      <h2 className="section-title">Sobre</h2>
      <Card>
        <KeyValues
          items={[
            ["Ombro.app", <span className="mono">{info?.version ?? DASH}</span>],
            ["Serviço (ombrod)", <span className="mono">{snapshot?.status?.version ?? DASH}</span>],
            ["Protocolo IPC", <span className="mono">v{info?.protocol ?? DASH}</span>],
            ["Backend", info?.mode === "bundled" ? "Embarcado no app" : info?.mode === "development" ? "Repositório (desenvolvimento)" : DASH],
            ["Sistema", `macOS ${info?.os_version ?? DASH} · ${info?.arch === "aarch64" ? "Apple Silicon" : (info?.arch ?? DASH)}`],
            ["Dados", <span className="mono">~/.ombro-screen</span>],
          ]}
        />
        <p className="muted small" style={{ marginTop: 12 }}>
          Tudo local: sem conta, sem nuvem, sem telemetria. O Ombro só lê metadados dos agentes; nunca prompts, respostas ou
          credenciais.
        </p>
        <div className="row" style={{ marginTop: 12 }}>
          <Button size="sm" icon={<ExternalLink size={13} />} onClick={() => api.openLink("docs")}>
            Documentação
          </Button>
          <Button size="sm" icon={<ExternalLink size={13} />} onClick={() => api.openLink("repository")}>
            GitHub
          </Button>
          <Button size="sm" icon={<ExternalLink size={13} />} onClick={() => api.openLink("troubleshooting")}>
            Problemas comuns
          </Button>
          <Button size="sm" variant="ghost" onClick={() => api.openLink("third_party_notices").catch((e) => setPrefErro(errorMessage(e)))}>
            Avisos de terceiros
          </Button>
        </div>
      </Card>

      <Dialog
        open={!!op}
        wide
        title={op ? OPS[op.op].title : ""}
        onClose={() => op?.stage !== "running" && setOp(null)}
        actions={
          op?.stage === "done" || op?.stage === "error" ? (
            <Button variant="primary" onClick={() => setOp(null)}>
              Fechar
            </Button>
          ) : (
            <>
              <Button variant="ghost" disabled={op?.stage === "running"} onClick={() => setOp(null)}>
                Cancelar
              </Button>
              <Button
                variant={op?.op === "uninstall" ? "danger" : "primary"}
                pending={op?.stage === "running"}
                disabled={!op?.steps}
                onClick={confirmar}
              >
                {op ? OPS[op.op].confirm : ""}
              </Button>
            </>
          )
        }
      >
        {op && (
          <>
            {op.stage === "error" && <Notice tone="critical" title="Não deu certo">{op.error}</Notice>}
            {op.stage === "done" && <Notice tone="success" title="Pronto">O serviço foi atualizado.</Notice>}
            {(op.stage === "preview" || op.stage === "running") && <p>{OPS[op.op].intro}</p>}
            {op.steps && op.steps.length > 0 && (
              <>
                <p className="small" style={{ marginTop: 12 }}>
                  {op.stage === "done" ? "O que foi feito:" : "O que vai ser feito:"}
                </p>
                <pre className="code selectable">{op.steps.join("\n")}</pre>
              </>
            )}
          </>
        )}
      </Dialog>
    </>
  );
}
