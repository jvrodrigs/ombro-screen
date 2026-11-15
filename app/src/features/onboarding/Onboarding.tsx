import { useMemo, useState } from "react";
import { Check, ChevronLeft, Lock, MonitorSmartphone } from "lucide-react";
import { CloudMark } from "../../components/CloudMark";
import { Badge, Button, Card, Dialog, Notice, Spinner, Toggle } from "../../components/ui";
import { useAction, useAsync } from "../../hooks/useAsync";
import { useOmbro } from "../../hooks/useOmbro";
import { errorMessage } from "../../services/api";
import { LAYOUT_NAMES, PROVIDER_NAMES } from "../../lib/format";
import type { CheckStatus, IntegrationCheck, ProviderId } from "../../types/ombro";
import { Preview } from "../display/Preview";

const STEPS = ["Boas-vindas", "Ambiente", "Agentes", "Tela", "Preferências", "Pronto"];
const ICON: Record<CheckStatus, string> = { PASS: "ok", WARN: "atenção", FAIL: "falha", SKIP: "não se aplica" };

export function Onboarding() {
  const { prefs, setPrefs, api } = useOmbro();
  const [step, setStep] = useState(0);
  const concluir = useAction(async () => setPrefs(await api.prefsSet({ onboarding_completed: true })));
  const next = () => setStep((s) => Math.min(STEPS.length - 1, s + 1));
  const back = () => setStep((s) => Math.max(0, s - 1));

  return (
    <div className="onboarding">
      <div className="onboarding-drag" data-tauri-drag-region />
      <ol className="steps" aria-label="Etapas">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? "current" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}>
            <span className="step-dot" aria-hidden>
              {i < step ? <Check size={11} /> : i + 1}
            </span>
            {s}
          </li>
        ))}
      </ol>
      <div className="onboarding-body">
        {step === 0 && <Welcome onNext={next} />}
        {step === 1 && <Environment />}
        {step === 2 && <AgentsStep />}
        {step === 3 && <DisplayStep />}
        {step === 4 && <PrefsStep />}
        {step === 5 && <Done />}
      </div>
      {step > 0 && (
        <div className="onboarding-footer">
          <Button variant="ghost" icon={<ChevronLeft size={14} />} onClick={back}>
            Voltar
          </Button>
          {step < STEPS.length - 1 ? (
            <Button variant="primary" onClick={next}>
              Continuar
            </Button>
          ) : (
            <Button variant="primary" pending={concluir.pending} disabled={!prefs} onClick={() => concluir.run()}>
              Abrir o painel
            </Button>
          )}
        </div>
      )}
      {concluir.state.status === "error" && <Notice tone="critical">{concluir.state.message}</Notice>}
    </div>
  );
}

function Welcome({ onNext }: { onNext: () => void }) {
  return (
    <div className="welcome">
      <CloudMark size={72} />
      <h1>Ombro</h1>
      <p className="lead">Acompanhe os seus agentes de IA e o seu Mac, na tela do computador ou numa telinha USB.</p>
      <ul className="welcome-points">
        <li>
          <Lock size={14} aria-hidden /> Tudo local: sem conta, sem nuvem, sem telemetria.
        </li>
        <li>
          <Check size={14} aria-hidden /> Claude Code, Codex e Cursor, um de cada vez ou juntos.
        </li>
        <li>
          <MonitorSmartphone size={14} aria-hidden /> Sem a telinha USB, a prévia mostra o mesmo que ela mostraria.
        </li>
      </ul>
      <Button variant="primary" onClick={onNext}>
        Começar
      </Button>
    </div>
  );
}

function Environment() {
  const { api, info, snapshot } = useOmbro();
  const doctor = useAsync(() => api.doctorRun(), []);
  const checks = (doctor.data?.result.checks ?? []).filter((c) => c.category === "environment" || c.category === "service");
  const d = snapshot?.daemon;
  return (
    <section>
      <h1>Conferindo o seu Mac</h1>
      <p className="secondary lead-sm">O Ombro traz tudo o que precisa: não é preciso instalar Python, Homebrew ou outra coisa.</p>
      {info?.location_problem && <Notice tone="warning" title="Mova o Ombro para Aplicativos">{info.location_problem}</Notice>}
      <Card>
        <ul className="checks">
          <li className="check">
            <span className={`tone-${d?.state === "connected" ? "green" : "warning"}`}>●</span>
            <div className="check-body">
              <strong>Serviço do Ombro</strong>
              <p className="secondary">
                {d?.state === "connected" ? "Rodando e respondendo." : d?.state === "failed" ? `Não iniciou: ${d.reason}` : "Iniciando…"}
              </p>
            </div>
          </li>
          {doctor.loading && !doctor.data && <Spinner label="Verificando" />}
          {doctor.error && <Notice tone="critical">{doctor.error}</Notice>}
          {checks.map((c) => (
            <li key={c.id} className="check">
              <span className={`tone-${c.status === "PASS" ? "green" : c.status === "FAIL" ? "critical" : c.status === "WARN" ? "warning" : "muted"}`}>●</span>
              <div className="check-body">
                <div className="spread">
                  <strong>{c.title}</strong>
                  <span className="muted small">{ICON[c.status]}</span>
                </div>
                <p className="secondary">{c.message}</p>
              </div>
            </li>
          ))}
        </ul>
      </Card>
      {info?.legacy_installed && (
        <Notice tone="info" title="Encontrei uma instalação antiga do Ombro">
          As suas preferências e o histórico ficam onde estão. Dá para migrar o serviço antigo depois, em Ajustes.
        </Notice>
      )}
    </section>
  );
}

function AgentsStep() {
  const { api, info } = useOmbro();
  const lista = useAsync(() => api.integrationsList(), []);
  const [sel, setSel] = useState<Set<ProviderId>>(new Set());
  const [confirm, setConfirm] = useState<{ diffs: { provider: ProviderId; file: string; diff: string }[]; error?: string } | null>(null);
  const [resultado, setResultado] = useState<{ provider: ProviderId; ok: boolean; msg: string }[]>([]);
  const instalar = useAction(async () => {
    const out = [];
    for (const p of sel) {
      try {
        const r = await api.integrationRun(p, "install", false);
        out.push(r.result.ok ? { provider: p, ok: true, msg: r.result.note || "Integrada." } : { provider: p, ok: false, msg: r.result.error });
      } catch (e) {
        out.push({ provider: p, ok: false, msg: errorMessage(e) });
      }
    }
    setResultado(out);
    setConfirm(null);
    setSel(new Set());
    lista.reload();
  });
  const integracoes = lista.data?.result.integrations ?? [];

  const preparar = async () => {
    const diffs = [];
    for (const p of sel) {
      try {
        const r = await api.integrationRun(p, "install", true);
        if (!r.result.ok) return setConfirm({ diffs: [], error: r.result.error });
        diffs.push({ provider: p, file: r.result.file, diff: r.result.diff });
      } catch (e) {
        return setConfirm({ diffs: [], error: errorMessage(e) });
      }
    }
    setConfirm({ diffs });
  };

  const linha = (c: IntegrationCheck) => {
    const pode = c.instalado && c.state !== "configured" && c.state !== "conflict" && c.state !== "invalid";
    return (
      <li key={c.provider} className="agent-pick">
        <label className={pode ? "" : "disabled"}>
          <input
            type="checkbox"
            disabled={!pode || !!info?.location_problem}
            checked={sel.has(c.provider)}
            onChange={(e) => {
              const n = new Set(sel);
              if (e.target.checked) n.add(c.provider);
              else n.delete(c.provider);
              setSel(n);
            }}
          />
          <strong>{PROVIDER_NAMES[c.provider]}</strong>
        </label>
        {!c.instalado ? (
          <Badge tone="muted">Não encontrado</Badge>
        ) : c.state === "configured" ? (
          <Badge tone="green">Já integrado</Badge>
        ) : c.provider === "claude" ? (
          <Badge tone="neutral">Funciona sem hooks</Badge>
        ) : (
          <Badge tone="neutral">Precisa da integração</Badge>
        )}
      </li>
    );
  };

  return (
    <section>
      <h1>Seus agentes</h1>
      <p className="secondary lead-sm">
        As integrações são opcionais. Elas acrescentam hooks do Ombro nas configurações do agente: o hook só anota que algo
        aconteceu (sessão, evento, horário, nome da pasta), nunca prompts, respostas ou credenciais. Nada muda sem a sua
        confirmação, e tudo tem backup.
      </p>
      {lista.loading && !lista.data && <Spinner label="Procurando agentes" />}
      {lista.error && <Notice tone="critical">{lista.error}</Notice>}
      <Card>
        <ul className="agent-picks">{integracoes.map(linha)}</ul>
        <div className="row" style={{ marginTop: 12 }}>
          <Button variant="primary" disabled={sel.size === 0} onClick={preparar}>
            Ver o que muda e instalar
          </Button>
          <span className="muted small">Ou continue sem integrar; dá para fazer depois em Agentes.</span>
        </div>
      </Card>
      {resultado.map((r) => (
        <Notice key={r.provider} tone={r.ok ? "success" : "critical"} title={`${PROVIDER_NAMES[r.provider]}: ${r.ok ? "integrado" : "nada foi alterado"}`}>
          {r.msg}
        </Notice>
      ))}
      <Dialog
        open={!!confirm}
        wide
        title="Confirmar as integrações"
        onClose={() => !instalar.pending && setConfirm(null)}
        actions={
          <>
            <Button variant="ghost" disabled={instalar.pending} onClick={() => setConfirm(null)}>
              Cancelar
            </Button>
            <Button variant="primary" pending={instalar.pending} disabled={!!confirm?.error} onClick={() => instalar.run()}>
              Instalar
            </Button>
          </>
        }
      >
        {confirm?.error && <Notice tone="critical">{confirm.error}</Notice>}
        {confirm?.diffs.map((d) => (
          <div key={d.provider} style={{ marginBottom: 12 }}>
            <strong>{PROVIDER_NAMES[d.provider]}</strong> <span className="mono muted">{d.file}</span>
            <pre className="code selectable">{d.diff || "(nada a mudar)"}</pre>
          </div>
        ))}
      </Dialog>
    </section>
  );
}

function DisplayStep() {
  const { api } = useOmbro();
  const config = useAsync(() => api.configGet(), []);
  const [usb, setUsb] = useState<boolean | null>(null);
  const salvar = useAction(async (v: boolean) => {
    await api.configUpdate({ usb: v }, true);
    await api.daemonRestart();
    setUsb(v);
    config.reload();
  });
  const atual = usb ?? config.data?.values.usb === true;
  return (
    <section>
      <h1>A telinha USB</h1>
      <p className="secondary lead-sm">
        Com a telinha de 3,5" (revisão A, a mais comum), ligue abaixo. Sem ela, o Ombro funciona do mesmo jeito: a prévia mostra
        o que a tela mostraria.
      </p>
      <div className="onboarding-split">
        <Card>
          <Toggle
            label="Tenho a telinha USB"
            description="Conecte o cabo antes. O Ombro reinicia o monitoramento para abrir a tela; brilho, layout e revisão ficam na página Tela."
            checked={atual}
            pending={salvar.pending}
            disabled={!config.data}
            onChange={(v) => salvar.run(v)}
          />
          {salvar.state.status === "error" && <Notice tone="critical">{salvar.state.message}</Notice>}
          {!atual && <p className="muted small">Modo prévia: nada a configurar.</p>}
        </Card>
        <Card>
          <Preview zoomable={false} />
        </Card>
      </div>
    </section>
  );
}

function PrefsStep() {
  const { api, prefs, setPrefs } = useOmbro();
  const config = useAsync(() => api.configGet(), []);
  const schema = useAsync(() => api.configSchema(), []);
  const [erro, setErro] = useState<string | null>(null);
  const layouts = useMemo(
    () => (schema.data?.options.find((o) => o.key === "layout")?.choices ?? []).map(String).filter((l) => l !== "classico"),
    [schema.data],
  );
  const run = async (f: () => Promise<void>) => {
    setErro(null);
    try {
      await f();
    } catch (e) {
      setErro(errorMessage(e));
    }
  };
  return (
    <section>
      <h1>Preferências</h1>
      <p className="secondary lead-sm">Tudo isso muda depois, em Ajustes.</p>
      {erro && <Notice tone="warning">{erro}</Notice>}
      <Card>
        <Toggle
          label="Abrir o Ombro ao entrar no Mac"
          description="Começa só na Menu Bar, sem janela."
          checked={prefs?.autostart ?? false}
          disabled={!prefs}
          onChange={(v) =>
            run(async () => {
              const autostart = await api.autostartSet(v);
              if (prefs) setPrefs({ ...prefs, autostart });
            })
          }
        />
        <Toggle
          label="Notificações"
          description="Quando uma sessão espera você, pede aprovação ou para com erro. O macOS vai pedir permissão."
          checked={prefs?.notifications ?? false}
          disabled={!prefs}
          onChange={(v) => run(async () => setPrefs(await api.prefsSet({ notifications: v })))}
        />
        <div className="field">
          <label htmlFor="layout-padrao">Layout da tela</label>
          <select
            id="layout-padrao"
            value={String(config.data?.values.layout ?? "auto")}
            disabled={!config.data}
            onChange={(e) =>
              run(async () => {
                await api.setLayout(e.target.value, true);
                await config.reload();
              })
            }
          >
            {layouts.map((l) => (
              <option key={l} value={l}>
                {LAYOUT_NAMES[l] ?? l}
              </option>
            ))}
          </select>
        </div>
        <p className="muted small" style={{ marginTop: 8 }}>
          Fechar a janela mantém o Ombro na Menu Bar. Para monitorar até com o app fechado, ligue o serviço em segundo plano em
          Ajustes.
        </p>
      </Card>
    </section>
  );
}

function Done() {
  const { snapshot } = useOmbro();
  const provs = snapshot?.providers?.providers ?? [];
  const ativos = provs.filter((p) => p.enabled && p.installed && p.integration === "ok");
  const tela = snapshot?.status?.display;
  return (
    <section className="welcome">
      <CloudMark size={56} />
      <h1>Tudo pronto</h1>
      <Card className="done-card">
        <ul className="done-list">
          <li>
            <Check size={14} className="tone-green" aria-hidden /> Agentes: {ativos.length ? ativos.map((p) => p.name ?? PROVIDER_NAMES[p.id]).join(", ") : "nenhum integrado ainda"}
          </li>
          <li>
            <Check size={14} className="tone-green" aria-hidden /> Serviço: {snapshot?.daemon?.state === "connected" ? "monitorando" : "parado (veja o Diagnóstico)"}
          </li>
          <li>
            <Check size={14} className="tone-green" aria-hidden /> Tela:{" "}
            {tela?.kind === "usb" ? (tela.connection === "connected" ? "conectada" : "USB ligada, esperando a tela") : "modo prévia"}
          </li>
        </ul>
      </Card>
    </section>
  );
}
