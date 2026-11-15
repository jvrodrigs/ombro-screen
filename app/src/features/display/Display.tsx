import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, KeyValues, Notice, PageHeader, Toggle } from "../../components/ui";
import { useAction, useAsync, useInterval } from "../../hooks/useAsync";
import { useOmbro } from "../../hooks/useOmbro";
import { errorMessage } from "../../services/api";
import { DASH, LAYOUT_NAMES, ago } from "../../lib/format";
import type { ConfigUpdateResult, ConfigValue, DisplayStatus, SchemaOption } from "../../types/ombro";
import { Preview } from "./Preview";

const ORIENTATION: Record<string, string> = {
  portrait: "Retrato",
  reverse_portrait: "Retrato, girada 180°",
  landscape: "Paisagem",
  reverse_landscape: "Paisagem, girada 180°",
};
const REVISOES: Record<string, string> = {
  A: "A · Turing 3,5\" / UsbPCMonitor",
  B: "B · XuanFang 3,5\"",
  C: "C · Turing 5\"",
  D: "D · Kipye Qiye 3,5\"",
  WEACT_A: "WeAct 3,5\"",
};
const TRANSPORTES: Record<string, string> = { auto: "Automático", usb: "USB direto", serial: "Porta serial" };

function connection(d: DisplayStatus | null): { label: string; tone: "green" | "muted" | "warning" | "critical" | "blue" } {
  if (!d) return { label: DASH, tone: "muted" };
  if (d.kind === "simulator") return d.connection === "error" ? { label: "Prévia sem simulador", tone: "blue" } : { label: "Só a prévia (sem USB)", tone: "blue" };
  if (d.kind === "none") return { label: "Sem tela", tone: "muted" };
  const nomes: Record<string, { label: string; tone: "green" | "muted" | "warning" | "critical" }> = {
    connected: { label: "Conectada", tone: "green" },
    disconnected: { label: "Desconectada", tone: "muted" },
    error: { label: "Com erro", tone: "critical" },
    conflict: { label: "Em uso por outro Ombro", tone: "warning" },
  };
  return nomes[d.connection] ?? { label: d.connection, tone: "muted" };
}

/** O aviso de "vale ao reiniciar", com o botão que reinicia o monitoramento (o ombrod, não os agentes). */
export function RestartNotice({ keys, onDone }: { keys: string[]; onDone?: () => void }) {
  const { api, snapshot } = useOmbro();
  const restart = useAction(() => api.daemonRestart());
  const externo = snapshot?.daemon?.state === "connected" && snapshot.daemon.owner === "external";
  if (!keys.length) return null;
  if (restart.state.status === "done") return <Notice tone="success">Monitoramento reiniciado: as mudanças valem agora.</Notice>;
  return (
    <Notice
      tone="info"
      title="Gravado; vale depois de reiniciar o monitoramento"
      actions={
        !externo && (
          <Button size="sm" pending={restart.pending} onClick={() => restart.run().then(onDone)}>
            Reiniciar agora
          </Button>
        )
      }
    >
      {keys.join(", ")}. A tela pisca por um instante; os agentes não são afetados.
      {externo && " Este ombrod foi iniciado num terminal: reinicie-o por lá."}
      {restart.state.status === "error" && <div className="tone-critical">{restart.state.message}</div>}
    </Notice>
  );
}

export function DisplayPage() {
  const { api, snapshot } = useOmbro();
  const status = useAsync(() => api.displayStatus(), []);
  const config = useAsync(() => api.configGet(), []);
  const schema = useAsync(() => api.configSchema(), []);
  useInterval(() => status.reload(), 3000, snapshot?.daemon?.state === "connected");

  const opt = useMemo(() => new Map((schema.data?.options ?? []).map((o) => [o.key, o])), [schema.data]);
  const valores = config.data?.values ?? {};
  const d = status.data;
  const con = connection(d);

  // brilho: o valor mostrado acompanha o arraste; só vai ao ombrod ao soltar
  const [brilho, setBrilho] = useState<number | null>(null);
  const [brilhoMsg, setBrilhoMsg] = useState<string | null>(null);
  useEffect(() => {
    if (brilho == null && typeof valores.brilho === "number") setBrilho(valores.brilho);
  }, [valores.brilho, brilho]);
  const enviarBrilho = async (v: number) => {
    setBrilhoMsg(null);
    try {
      const r = await api.setBrightness(v, true);
      setBrilhoMsg(r.applied.includes("brilho") ? null : "Gravado; vale quando a tela estiver conectada.");
      status.reload();
    } catch (e) {
      setBrilhoMsg(errorMessage(e));
      config.reload();
    }
  };

  const [layoutRes, setLayoutRes] = useState<ConfigUpdateResult | null>(null);
  const layout = useAction(async (v: string) => {
    const r = await api.setLayout(v, true);
    setLayoutRes(r);
    config.reload();
    status.reload();
    return r;
  });

  const brilhoOpt = opt.get("brilho");
  const usb = valores.usb === true;

  return (
    <>
      <PageHeader title="Tela" subtitle="A telinha USB de 480×320 e a prévia do que ela mostra." />
      <div className="display-layout">
        <Card className="display-preview-card">
          <div className="card-title">
            <h2>Prévia</h2>
            <Badge tone={con.tone}>{con.label}</Badge>
          </div>
          <Preview />
          {d?.kind !== "usb" && (
            <p className="muted small" style={{ marginTop: 12 }}>
              Sem a tela USB, o Ombro desenha do mesmo jeito: a prévia é exatamente o que apareceria nela. Ligue a tela em
              "Hardware", abaixo.
            </p>
          )}
        </Card>

        <div className="stack">
          <Card>
            <div className="card-title">
              <h2>Estado</h2>
            </div>
            {status.error && <Notice tone="critical">{status.error}</Notice>}
            <KeyValues
              items={[
                ["Tipo", d ? (d.kind === "usb" ? "Tela USB" : d.kind === "simulator" ? "Simulador (prévia)" : "Nenhuma") : DASH],
                ["Conexão", con.label],
                ...(d?.kind === "usb"
                  ? ([
                      ["Revisão", d.revision ? (REVISOES[d.revision] ?? d.revision) : DASH],
                      ["Transporte", d.transport ? (TRANSPORTES[d.transport] ?? d.transport) : DASH],
                      ["Porta", <span className="mono">{d.port ?? DASH}</span>],
                      ["Brilho", d.brightness != null ? `${d.brightness}%` : DASH],
                      ["Orientação", d.orientation ? (ORIENTATION[d.orientation] ?? d.orientation) : DASH],
                      ["Conectada", d.connected_since ? ago(d.connected_since) : DASH],
                    ] as [string, React.ReactNode][])
                  : []),
                ["Resolução", "480×320"],
                ["Layout", d ? (LAYOUT_NAMES[d.layout] ?? d.layout) : DASH],
                ["Último quadro", d?.last_frame_at ? ago(d.last_frame_at) : DASH],
              ]}
            />
            {d?.last_error && (
              <Notice tone={d.kind === "usb" ? "warning" : "info"} title="Último erro">
                {d.last_error}
              </Notice>
            )}
          </Card>
        </div>
      </div>

      <h2 className="section-title">Layout</h2>
      <Card>
        <div className="layout-choices" role="radiogroup" aria-label="Layout da tela">
          {(opt.get("layout")?.choices ?? []).map((c) => {
            const v = String(c);
            const ativo = valores.layout === v;
            return (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={ativo}
                className={`choice ${ativo ? "active" : ""}`}
                disabled={layout.pending}
                onClick={() => layout.run(v)}
              >
                <strong>{LAYOUT_NAMES[v] ?? v}</strong>
                <span className="muted small">{LAYOUT_DESC[v] ?? ""}</span>
              </button>
            );
          })}
        </div>
        {layout.state.status === "error" && <Notice tone="critical">{layout.state.message}</Notice>}
        {layoutRes && <RestartNotice keys={layoutRes.restart_required} onDone={() => status.reload()} />}
      </Card>

      <h2 className="section-title">Brilho</h2>
      <Card>
        <div className="slider-row">
          <label htmlFor="brilho">Brilho da tela USB</label>
          <span className="mono">{brilho ?? DASH}%</span>
        </div>
        <input
          id="brilho"
          type="range"
          className="slider"
          min={brilhoOpt?.min ?? 0}
          max={brilhoOpt?.max ?? 100}
          step={5}
          value={brilho ?? 0}
          disabled={brilho == null}
          onChange={(e) => setBrilho(Number(e.target.value))}
          onPointerUp={(e) => enviarBrilho(Number((e.target as HTMLInputElement).value))}
          onKeyUp={(e) => enviarBrilho(Number((e.target as HTMLInputElement).value))}
          aria-describedby="brilho-d"
        />
        <p id="brilho-d" className="muted small">
          {valores.revisao === "A" && (brilho ?? 0) > 50 ? "Acima de 50% a revisão A esquenta. " : ""}
          {usb ? "Muda na hora e fica gravado." : "Fica gravado e vale quando a tela USB estiver ligada."}
        </p>
        {brilhoMsg && <Notice tone="info">{brilhoMsg}</Notice>}
      </Card>

      <h2 className="section-title">Hardware</h2>
      {config.data && schema.data && <HardwareForm values={valores} opt={opt} onSaved={() => config.reload()} />}
    </>
  );
}

const LAYOUT_DESC: Record<string, string> = {
  auto: "O padrão (hoje, o layout do Ombro)",
  ombro: "O padrão: uso, tokens, sistema e o cloud",
  "minimal-v2": "O mesmo conteúdo, mais limpo, com CPU e RAM em barras",
  multiagente: "Claude Code, Codex e Cursor lado a lado",
  classico: "O painel antigo, com GPU, rede e disco",
};

const HW_KEYS = ["usb", "revisao", "transporte", "girar", "paisagem"] as const;

/** Preferências que só valem ao reiniciar: editadas aqui, gravadas juntas, com o "não salvo" à vista. */
function HardwareForm({ values, opt, onSaved }: { values: Record<string, unknown>; opt: Map<string, SchemaOption>; onSaved: () => void }) {
  const { api } = useOmbro();
  const efetivo = useMemo(() => Object.fromEntries(HW_KEYS.map((k) => [k, values[k] as ConfigValue])), [values]);
  const [form, setForm] = useState<Record<string, ConfigValue>>(efetivo);
  const [res, setRes] = useState<ConfigUpdateResult | null>(null);
  const salvar = useAction(async () => {
    const mudancas = Object.fromEntries(Object.entries(form).filter(([k, v]) => efetivo[k] !== v));
    const r = await api.configUpdate(mudancas, true);
    setRes(r);
    onSaved();
    return r;
  });
  useEffect(() => setForm(efetivo), [efetivo]);
  const sujo = HW_KEYS.some((k) => form[k] !== efetivo[k]);
  const set = (k: string, v: ConfigValue) => {
    setRes(null);
    setForm((f) => ({ ...f, [k]: v }));
  };

  return (
    <Card>
      <Toggle label="Usar a tela USB" description="Desligado, o Ombro só desenha a prévia." checked={form.usb === true} onChange={(v) => set("usb", v)} />
      <div className="field">
        <label htmlFor="revisao">Revisão do hardware</label>
        <select id="revisao" value={String(form.revisao)} onChange={(e) => set("revisao", e.target.value)} disabled={form.usb !== true}>
          {(opt.get("revisao")?.choices ?? []).map((c) => (
            <option key={String(c)} value={String(c)}>
              {REVISOES[String(c)] ?? String(c)}
            </option>
          ))}
        </select>
        {form.revisao !== "A" && form.usb === true && (
          <p className="muted small">
            As revisões B, C, D e WeAct usam a biblioteca turing-smart-screen-python, que o app não traz (licença GPL). Elas
            funcionam pelo Ombro do repositório; veja a documentação.
          </p>
        )}
      </div>
      <div className="field">
        <label htmlFor="transporte">Transporte</label>
        <select id="transporte" value={String(form.transporte)} onChange={(e) => set("transporte", e.target.value)} disabled={form.usb !== true}>
          {(opt.get("transporte")?.choices ?? []).map((c) => (
            <option key={String(c)} value={String(c)}>
              {TRANSPORTES[String(c)] ?? String(c)}
            </option>
          ))}
        </select>
        <p className="muted small">No Mac, a revisão A vai pelo USB direto (o automático já escolhe).</p>
      </div>
      <Toggle label="Girar 180°" description="Se a tela aparece de cabeça para baixo no case." checked={form.girar === true} onChange={(v) => set("girar", v)} />
      <Toggle
        label="Paisagem no layout clássico"
        description="Só o layout clássico tem modo deitado; os outros já são 480×320."
        checked={form.paisagem === true}
        onChange={(v) => set("paisagem", v)}
      />
      <div className="form-footer">
        <span className={sujo ? "tone-warning small" : "muted small"}>{sujo ? "Alterações não salvas" : "Sem alterações"}</span>
        <div className="row">
          <Button variant="ghost" disabled={!sujo || salvar.pending} onClick={() => setForm(efetivo)}>
            Descartar
          </Button>
          <Button variant="primary" disabled={!sujo} pending={salvar.pending} onClick={() => salvar.run()}>
            Salvar
          </Button>
        </div>
      </div>
      {salvar.state.status === "error" && <Notice tone="critical">{salvar.state.message}</Notice>}
      {res && <RestartNotice keys={res.restart_required} />}
    </Card>
  );
}
