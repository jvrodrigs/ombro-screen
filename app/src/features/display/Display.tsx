import { useState } from "react";
import { Badge, Button, Card, KeyValues, Notice, PageHeader, Toggle } from "../../components/ui";
import { useAction, useAsync, useInterval } from "../../hooks/useAsync";
import { useOmbro } from "../../hooks/useOmbro";
import { errorMessage } from "../../services/api";
import { DASH, LAYOUT_NAMES, ago } from "../../lib/format";
import { useConfig, type ConfigStore } from "../../hooks/useConfig";
import type { ConfigValue, DisplayStatus } from "../../types/ombro";
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

const PREF_NAMES: Record<string, string> = {
  usb: "usar a tela USB",
  revisao: "revisão do hardware",
  transporte: "transporte",
  porta: "porta serial",
  "providers.claude": "Claude Code",
  "providers.codex": "Codex",
  "providers.cursor": "Cursor",
  "providers.opencode": "OpenCode",
};

/**
 * O aviso de "gravado, vale ao reiniciar", com o botão que reinicia o monitoramento (o ombrod, não os agentes).
 * `keys` vem do `pending` do ombrod: o aviso fica enquanto houver algo gravado que ainda não está em uso.
 */
export function RestartNotice({ keys, onDone }: { keys: string[]; onDone?: () => void }) {
  const { api, snapshot } = useOmbro();
  const restart = useAction(() => api.daemonRestart());
  const externo = snapshot?.daemon?.state === "connected" && snapshot.daemon.owner === "external";
  if (!keys.length) return null;
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
      {keys.map((k) => PREF_NAMES[k] ?? k).join(", ")}. A tela pisca por um instante; os agentes não são afetados.
      {externo && " Este ombrod foi iniciado num terminal: reinicie-o por lá."}
      {restart.state.status === "error" && <div className="tone-critical">{restart.state.message}</div>}
    </Notice>
  );
}

export function DisplayPage() {
  const { api, snapshot } = useOmbro();
  const status = useAsync(() => api.displayStatus(), []);
  const cfg = useConfig();
  const conectado = snapshot?.daemon?.state === "connected";
  useInterval(() => status.reload(), 3000, conectado);

  const valores = cfg.values;
  const d = status.data;
  const con = connection(d);
  const usb = valores.usb === true;
  const telaFora = d?.kind === "usb" && d.connection !== "connected";

  // Uma mudança: o ombrod responde com o que entrou em uso; o erro aparece e nada muda na tela.
  const [erro, setErro] = useState<{ onde: string; texto: string } | null>(null);
  const [adiado, setAdiado] = useState<string[]>([]);
  const mudar = async (onde: string, mudancas: Record<string, ConfigValue>) => {
    setErro(null);
    try {
      const r = await cfg.update(mudancas);
      setAdiado(r.deferred ?? []);
      status.reload();
      return r;
    } catch (e) {
      setErro({ onde, texto: errorMessage(e) });
      status.reload();
      return undefined;
    }
  };

  // layout: o marcado é o que o ombrod está usando; o clicado fica "trocando" até a resposta
  const [alvo, setAlvo] = useState<string | null>(null);
  const trocando = cfg.busy("layout");
  const escolherLayout = (v: string) => {
    if (v === valores.layout && !trocando) return;
    setAlvo(v);
    mudar("layout", { layout: v });
  };
  const layoutAtual = typeof valores.layout === "string" ? valores.layout : null;
  const classico = layoutAtual === "classico";

  // brilho: o valor mostrado acompanha o arraste; ao soltar vai ao ombrod, e depois volta a mostrar o que ele confirmou
  const [arraste, setArraste] = useState<number | null>(null);
  const brilho = arraste ?? (typeof valores.brilho === "number" ? valores.brilho : null);
  const enviarBrilho = async (v: number) => {
    if (v !== valores.brilho) await mudar("brilho", { brilho: v });
    setArraste(null);
  };

  const brilhoOpt = cfg.options.get("brilho");
  const pendentes = Object.keys(cfg.pending);
  const w = d?.width ?? 480;
  const h = d?.height ?? 320;

  return (
    <>
      <PageHeader title="Tela" subtitle="A telinha USB de 480×320 e a prévia do que ela mostra." />
      {cfg.error && <Notice tone="critical">{cfg.error}</Notice>}
      <div className="display-layout">
        <Card className="display-preview-card">
          <div className="card-title">
            <h2>Prévia</h2>
            <Badge tone={con.tone}>{con.label}</Badge>
          </div>
          <Preview intervalMs={200} />
          {d?.kind !== "usb" && (
            <p className="muted small" style={{ marginTop: 12 }}>
              Sem a tela USB, o Ombro desenha do mesmo jeito: a prévia é exatamente o que apareceria nela. Ligue a tela em
              "Hardware", abaixo.
            </p>
          )}
          {telaFora && (
            <p className="muted small" style={{ marginTop: 12 }}>
              A tela USB está desconectada: as mudanças já valem e aparecem na prévia; a tela recebe ao reconectar.
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
                ["Resolução", `${w}×${h}`],
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
          {(cfg.options.get("layout")?.choices ?? []).map((c) => {
            const v = String(c);
            const ativo = layoutAtual === v;
            const indo = trocando && alvo === v && !ativo;
            return (
              <button
                key={v}
                type="button"
                role="radio"
                aria-checked={ativo}
                aria-busy={indo}
                className={`choice ${ativo ? "active" : ""} ${indo ? "pending" : ""}`}
                disabled={!cfg.config}
                onClick={() => escolherLayout(v)}
              >
                <strong>{LAYOUT_NAMES[v] ?? v}</strong>
                <span className="muted small">{indo ? "Trocando…" : (LAYOUT_DESC[v] ?? "")}</span>
              </button>
            );
          })}
        </div>
        <Toggle
          label="Layout clássico deitado"
          description={
            classico
              ? "Deitado (480×320) em vez de em pé (320×480). Muda na hora."
              : `Só o layout clássico tem os dois modos. O ${LAYOUT_NAMES[layoutAtual ?? ""] ?? "atual"} é sempre deitado (480×320).`
          }
          checked={valores.paisagem === true}
          pending={cfg.busy("paisagem")}
          disabled={!cfg.config || !classico}
          onChange={(v) => mudar("layout", { paisagem: v })}
        />
        {erro?.onde === "layout" && <Notice tone="critical">{erro.texto}</Notice>}
        <p className="muted small">O layout muda na hora, sem reiniciar o monitoramento, e fica gravado.</p>
      </Card>

      <h2 className="section-title">Orientação</h2>
      <Card>
        <Toggle
          label="Girar 180°"
          description="Se a tela aparece de cabeça para baixo no case. Muda na hora."
          checked={valores.girar === true}
          pending={cfg.busy("girar")}
          disabled={!cfg.config}
          onChange={(v) => mudar("girar", { girar: v })}
        />
        {erro?.onde === "girar" && <Notice tone="critical">{erro.texto}</Notice>}
        {adiado.length > 0 && telaFora && (
          <Notice tone="info">Gravado e em uso. A tela USB está desconectada e recebe a mudança ao reconectar.</Notice>
        )}
        <p className="muted small">
          Girar muda só a tela física, não os dados nem o desenho: a prévia mostra o quadro como você o vê, de pé.
          {!usb && " Sem a tela USB, o giro fica gravado e vale quando ela for ligada."}
        </p>
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
          onChange={(e) => setArraste(Number(e.target.value))}
          onPointerUp={(e) => enviarBrilho(Number((e.target as HTMLInputElement).value))}
          onKeyUp={(e) => enviarBrilho(Number((e.target as HTMLInputElement).value))}
          aria-describedby="brilho-d"
        />
        <p id="brilho-d" className="muted small">
          {valores.revisao === "A" && (brilho ?? 0) > 50 ? "Acima de 50% a revisão A esquenta. " : ""}
          {usb ? "Muda na hora e fica gravado." : "Fica gravado e vale quando a tela USB estiver ligada."}
        </p>
        {erro?.onde === "brilho" && <Notice tone="critical">{erro.texto}</Notice>}
      </Card>

      <h2 className="section-title">Hardware</h2>
      {cfg.config && cfg.schema && <HardwareForm cfg={cfg} />}
      {pendentes.length > 0 && <RestartNotice key={pendentes.join(",")} keys={pendentes} onDone={() => cfg.reload()} />}
    </>
  );
}

const LAYOUT_DESC: Record<string, string> = {
  auto: "O padrão (hoje, o layout do Ombro)",
  ombro: "O padrão: uso, tokens, sistema e o cloud",
  "minimal-v2": "O mesmo conteúdo, mais limpo, com CPU e RAM em barras",
  multiagente: "Claude Code, Codex, Cursor e OpenCode juntos",
  classico: "O painel antigo, com GPU, rede e disco; em pé ou deitado",
};

const HW_KEYS = ["usb", "revisao", "transporte"] as const;

/**
 * O que escolhe a tela (USB ou prévia, revisão, transporte): só vale ao reiniciar o monitoramento, então é editado
 * aqui e gravado junto. Três coisas ficam separadas: o que você está editando (`edits`), o que está gravado
 * (`pending`, se ainda não entrou em uso) e o que está em uso (`values`). Uma releitura das preferências nunca
 * apaga o que você está editando.
 */
function HardwareForm({ cfg }: { cfg: ConfigStore }) {
  const gravado = (k: string): ConfigValue => (k in cfg.pending ? cfg.pending[k] : (cfg.values[k] as ConfigValue));
  const [edits, setEdits] = useState<Record<string, ConfigValue>>({});
  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);
  const valor = (k: string): ConfigValue => (k in edits ? edits[k] : gravado(k));
  const mudancas = Object.fromEntries(HW_KEYS.filter((k) => k in edits && edits[k] !== gravado(k)).map((k) => [k, edits[k]]));
  const sujo = Object.keys(mudancas).length > 0;
  const set = (k: string, v: ConfigValue) => {
    setErro(null);
    setEdits((e) => ({ ...e, [k]: v }));
  };
  const salvar = async () => {
    setSalvando(true);
    setErro(null);
    try {
      await cfg.update(mudancas);
      setEdits({}); // gravado: o formulário volta a mostrar o que o ombrod confirmou
    } catch (e) {
      setErro(errorMessage(e)); // nada foi gravado: o que você editou continua aqui
    } finally {
      setSalvando(false);
    }
  };
  const esperando = HW_KEYS.some((k) => k in cfg.pending);
  const opt = cfg.options;

  return (
    <Card>
      <Toggle label="Usar a tela USB" description="Desligado, o Ombro só desenha a prévia." checked={valor("usb") === true} onChange={(v) => set("usb", v)} />
      <div className="field">
        <label htmlFor="revisao">Revisão do hardware</label>
        <select id="revisao" value={String(valor("revisao"))} onChange={(e) => set("revisao", e.target.value)} disabled={valor("usb") !== true}>
          {(opt.get("revisao")?.choices ?? []).map((c) => (
            <option key={String(c)} value={String(c)}>
              {REVISOES[String(c)] ?? String(c)}
            </option>
          ))}
        </select>
        {valor("revisao") !== "A" && valor("usb") === true && (
          <p className="muted small">
            As revisões B, C, D e WeAct usam a biblioteca turing-smart-screen-python, que o app não traz (licença GPL). Elas
            funcionam pelo Ombro do repositório; veja a documentação.
          </p>
        )}
      </div>
      <div className="field">
        <label htmlFor="transporte">Transporte</label>
        <select id="transporte" value={String(valor("transporte"))} onChange={(e) => set("transporte", e.target.value)} disabled={valor("usb") !== true}>
          {(opt.get("transporte")?.choices ?? []).map((c) => (
            <option key={String(c)} value={String(c)}>
              {TRANSPORTES[String(c)] ?? String(c)}
            </option>
          ))}
        </select>
        <p className="muted small">No Mac, a revisão A vai pelo USB direto (o automático já escolhe).</p>
      </div>
      <div className="form-footer">
        <span className={sujo ? "tone-warning small" : "muted small"}>
          {sujo ? "Alterações não salvas" : esperando ? "Gravado; vale depois de reiniciar o monitoramento" : "Sem alterações"}
        </span>
        <div className="row">
          <Button variant="ghost" disabled={!sujo || salvando} onClick={() => setEdits({})}>
            Descartar
          </Button>
          <Button variant="primary" disabled={!sujo} pending={salvando} onClick={salvar}>
            Salvar
          </Button>
        </div>
      </div>
      {erro && <Notice tone="critical">{erro}</Notice>}
    </Card>
  );
}
