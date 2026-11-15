import { useOmbro } from "../hooks/useOmbro";
import { useAction } from "../hooks/useAsync";
import { Button, Notice } from "./ui";

/** O aviso do topo de toda página quando o monitoramento não está normal. Uma falha nunca esconde a página. */
export function ConnectionBanner() {
  const { api, snapshot, info, navigate } = useOmbro();
  const iniciar = useAction(() => api.daemonStart());
  const servico = useAction(() => api.serviceRun("start", false));

  if (info?.backend_error) {
    return (
      <Notice tone="critical" title="O backend do Ombro não foi encontrado">
        {info.backend_error.message}. Reinstale o Ombro a partir do arquivo DMG.
      </Notice>
    );
  }
  if (!snapshot?.daemon) return null;
  const d = snapshot.daemon;

  if (d.state === "failed" && info?.legacy_installed) {
    return (
      <Notice
        tone="warning"
        title="O Ombro antigo está cuidando da tela"
        actions={
          <Button variant="primary" size="sm" onClick={() => navigate("settings")}>
            Ver a migração
          </Button>
        }
      >
        O LaunchAgent de antes do Ombro Hub (com.ombro-screen) está instalado. Para não haver dois Ombros disputando a tela, o
        app só começa a monitorar depois da migração, que é segura e reversível.
      </Notice>
    );
  }
  if (d.state === "failed") {
    const erro = iniciar.state.status === "error" ? iniciar.state.message : null;
    return (
      <Notice
        tone="critical"
        title="O Ombro não está monitorando"
        actions={
          <>
            <Button variant="primary" size="sm" pending={iniciar.pending} onClick={() => iniciar.run()}>
              Iniciar monitoramento
            </Button>
            <Button size="sm" variant="ghost" onClick={() => navigate("diagnostics")}>
              Ver diagnóstico
            </Button>
          </>
        }
      >
        O serviço local (ombrod) não está respondendo: {erro ?? d.reason}.
      </Notice>
    );
  }
  if (d.state === "waiting_for_service") {
    return (
      <Notice
        tone="warning"
        title="Esperando o serviço do Ombro"
        actions={
          <Button size="sm" pending={servico.pending} onClick={() => servico.run()}>
            Iniciar serviço
          </Button>
        }
      >
        O serviço em segundo plano está instalado, mas ainda não respondeu. Se ele foi parado, inicie de novo.
        {servico.state.status === "error" && <div className="tone-critical">{servico.state.message}</div>}
      </Notice>
    );
  }
  if (d.state === "incompatible") {
    return (
      <Notice tone="critical" title="Serviço de outra versão">
        {d.reason}. Feche os outros Ombros (ou atualize-os) e abra este de novo. O app não conversa com uma versão que não
        conhece.
      </Notice>
    );
  }

  const st = snapshot.status;
  const avisos = [];
  if ((st?.conflicts?.length ?? 0) > 0 || info?.legacy_installed) {
    avisos.push(
      <Notice
        key="legado"
        tone="warning"
        title="Há um Ombro antigo ligado"
        actions={
          <Button size="sm" onClick={() => navigate("settings")}>
            Ver migração
          </Button>
        }
      >
        O LaunchAgent de antes do Ombro Hub (com.ombro-screen) ainda está instalado. Os dois disputariam a tela USB; o
        ombrod espera ele sair. A migração é segura e reversível.
      </Notice>,
    );
  }
  if (st?.storage === "memory_only") {
    avisos.push(
      <Notice key="banco" tone="warning" title="Histórico só em memória">
        O banco local deu problema e o Ombro segue sem gravar o histórico. Veja o Diagnóstico.
      </Notice>,
    );
  }
  if (st?.demo) {
    avisos.push(
      <Notice key="demo" tone="info" title="Dados de demonstração">
        Este ombrod roda com --demo: nada aqui é dos seus agentes.
      </Notice>,
    );
  }
  return avisos.length ? <>{avisos}</> : null;
}
