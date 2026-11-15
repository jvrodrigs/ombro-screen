import { useEffect, useMemo, useState } from "react";
import { Card, Notice, PageHeader, Segmented, Toggle } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { useOmbro } from "../../hooks/useOmbro";
import { errorMessage } from "../../services/api";
import { PROVIDER_NAMES } from "../../lib/format";
import type { ConfigValue } from "../../types/ombro";
import { Preview } from "../display/Preview";

const VISTAS: Record<string, { label: string; desc: string }> = {
  overview: { label: "Visão geral", desc: "Os três agentes lado a lado" },
  focus: { label: "Foco", desc: "Um agente em destaque" },
  attention: { label: "Atenção", desc: "Quem precisa de você primeiro" },
};
const SUGESTOES = ["#DF8559", "#79A9D8", "#83B596", "#E7AF5E", "#C58AD8", "#3D7A4F"];

/** Preferências visuais da telinha, aplicadas pelo ombrod (fonte da verdade) e vistas na prévia real. */
export function Appearance() {
  const { api } = useOmbro();
  const config = useAsync(() => api.configGet(), []);
  const schema = useAsync(() => api.configSchema(), []);
  const opt = useMemo(() => new Map((schema.data?.options ?? []).map((o) => [o.key, o])), [schema.data]);
  const v = config.data?.values ?? {};
  const [salvando, setSalvando] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [cor, setCor] = useState<string>("#DF8559");
  const [rotacaoSeg, setRotacaoSeg] = useState<string>("");

  useEffect(() => {
    if (typeof v.cor_mascote === "string") setCor(v.cor_mascote);
    if (typeof v.intervalo_rotacao === "number") setRotacaoSeg(String(v.intervalo_rotacao));
  }, [v.cor_mascote, v.intervalo_rotacao]);

  const mudar = async (chave: string, valor: ConfigValue) => {
    setSalvando(chave);
    setErro(null);
    try {
      await api.configUpdate({ [chave]: valor }, true);
      await config.reload();
    } catch (e) {
      setErro(errorMessage(e));
    } finally {
      setSalvando(null);
    }
  };

  const fixa = typeof v.cor_mascote === "string";
  const rot = opt.get("intervalo_rotacao");
  const multi = v.layout === "multiagente";

  return (
    <>
      <PageHeader title="Aparência" subtitle="Como o cloud e o layout multiagente aparecem na tela. Tudo muda na hora." />
      {erro && <Notice tone="critical">{erro}</Notice>}
      {config.error && <Notice tone="critical">{config.error}</Notice>}

      <div className="appearance-layout">
        <div className="stack">
          <Card>
            <div className="card-title">
              <h2>Cloud</h2>
            </div>
            <Toggle
              label="Cor fixa"
              description="Desligado, o cloud ganha uma cor sorteada a cada troca de estado (o padrão)."
              checked={fixa}
              pending={salvando === "cor_mascote"}
              disabled={!config.data}
              onChange={(on) => mudar("cor_mascote", on ? cor.toUpperCase() : null)}
            />
            {fixa && (
              <div className="color-row">
                <label htmlFor="cor" className="secondary">
                  Cor
                </label>
                <input
                  id="cor"
                  type="color"
                  value={cor.toLowerCase()}
                  onChange={(e) => setCor(e.target.value)}
                  onBlur={() => cor.toUpperCase() !== v.cor_mascote && mudar("cor_mascote", cor.toUpperCase())}
                />
                <span className="mono">{cor.toUpperCase()}</span>
                <div className="swatches" role="group" aria-label="Sugestões de cor">
                  {SUGESTOES.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={`swatch ${s === v.cor_mascote ? "active" : ""}`}
                      style={{ background: s }}
                      aria-label={`Usar ${s}`}
                      aria-pressed={s === v.cor_mascote}
                      onClick={() => {
                        setCor(s);
                        mudar("cor_mascote", s);
                      }}
                    />
                  ))}
                </div>
              </div>
            )}
            <p className="muted small">A forma e as animações do cloud não mudam.</p>
          </Card>

          <Card>
            <div className="card-title">
              <h2>Layout multiagente</h2>
            </div>
            {!multi && (
              <p className="muted small" style={{ marginBottom: 12 }}>
                Estas opções valem quando o layout da tela é o Multiagente (página Tela).
              </p>
            )}
            <div className="field">
              <span className="field-label">Visão</span>
              <Segmented
                label="Visão do multiagente"
                value={String(v.vista ?? "overview")}
                disabled={salvando === "vista" || !config.data}
                onChange={(x) => mudar("vista", x)}
                options={(opt.get("vista")?.choices ?? ["overview", "focus", "attention"]).map((c) => ({
                  value: String(c),
                  label: VISTAS[String(c)]?.label ?? String(c),
                  title: VISTAS[String(c)]?.desc,
                }))}
              />
            </div>
            <div className="field">
              <label htmlFor="foco">Agente em foco</label>
              <select id="foco" value={String(v.foco ?? "")} disabled={salvando === "foco" || !config.data} onChange={(e) => mudar("foco", e.target.value || null)}>
                <option value="">Automático</option>
                {(opt.get("foco")?.choices ?? []).map((c) => (
                  <option key={String(c)} value={String(c)}>
                    {PROVIDER_NAMES[String(c)] ?? String(c)}
                  </option>
                ))}
              </select>
            </div>
            <Toggle
              label="Alternar sozinho"
              description="Passa pela visão geral e por cada agente, um de cada vez."
              checked={v.rotacao === true}
              pending={salvando === "rotacao"}
              disabled={!config.data}
              onChange={(x) => mudar("rotacao", x)}
            />
            {v.rotacao === true && (
              <div className="field">
                <label htmlFor="rot">Segundos em cada visão</label>
                <input
                  id="rot"
                  type="number"
                  className="mono"
                  min={rot?.min ?? 5}
                  max={rot?.max ?? 3600}
                  value={rotacaoSeg}
                  onChange={(e) => setRotacaoSeg(e.target.value)}
                  onBlur={() => {
                    const n = Number(rotacaoSeg);
                    if (Number.isFinite(n) && n !== v.intervalo_rotacao) mudar("intervalo_rotacao", n);
                  }}
                />
              </div>
            )}
          </Card>

          <Card>
            <div className="card-title">
              <h2>Tema do app</h2>
            </div>
            <p className="secondary">Escuro, com a mesma paleta da telinha. Um tema claro só entra quando estiver completo.</p>
          </Card>
        </div>

        <Card className="appearance-preview">
          <div className="card-title">
            <h2>Prévia</h2>
          </div>
          <Preview zoomable={false} />
        </Card>
      </div>
    </>
  );
}
