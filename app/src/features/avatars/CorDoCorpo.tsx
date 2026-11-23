import { useId } from "react";
import { Button, Notice, Segmented } from "../../components/ui";
import { entradaDeValor, normalizarHex, valorDeEntrada, type EntradaCor } from "./cores";

export type ModoCor = "default" | "custom" | "rgb";
/** O rascunho da cor de um avatar: o modo e o texto do campo #RRGGBB. Vale na prévia assim que é válido. */
export interface RascunhoCor {
  modo: ModoCor;
  texto: string;
  /** A última entrada válida do rascunho: a prévia fica nela enquanto o texto está inválido. */
  ultima: EntradaCor | null;
}

/** O rascunho depois de uma edição: guarda a última entrada válida (se a nova é válida, é ela). */
export function editarRascunho(anterior: RascunhoCor, mudanca: Partial<Pick<RascunhoCor, "modo" | "texto">>): RascunhoCor {
  const r = { ...anterior, ...mudanca };
  return { ...r, ultima: entradaDoRascunho(r) ?? anterior.ultima };
}

/** O rascunho de partida: o que está gravado para o avatar (sem entrada: "Padrão"). */
export function rascunhoDe(valor: string | undefined, base: string): RascunhoCor {
  const e = entradaDeValor(valor);
  if (e?.modo === "custom") return { modo: "custom", texto: e.cor, ultima: e };
  if (e?.modo === "rgb") return { modo: "rgb", texto: base, ultima: e };
  return { modo: "default", texto: base, ultima: e };
}

/** A entrada que o rascunho representa, ou null se ainda não vale (um #RRGGBB incompleto). */
export function entradaDoRascunho(r: RascunhoCor): EntradaCor | null {
  if (r.modo === "default") return { modo: "default" };
  if (r.modo === "rgb") return { modo: "rgb" };
  const cor = normalizarHex(r.texto);
  return cor ? { modo: "custom", cor } : null;
}

/**
 * A cor do corpo de um avatar: Padrão (a cor original), Personalizada (um seletor e o campo #RRGGBB, com a amostra) ou
 * RGB (o ciclo de cores). O rascunho aparece na prévia ao lado enquanto é válido; "Aplicar" grava (persistente) pelo
 * ombrod; "Usar a cor global" remove a entrada e o avatar volta a seguir a cor das preferências gerais.
 */
export function CorDoCorpo({
  nome,
  rascunho,
  gravado,
  corBase,
  pendente,
  erro,
  onMudar,
  onAplicar,
  onHerdar,
}: {
  nome: string;
  rascunho: RascunhoCor;
  /** O valor gravado para o avatar ("default" | "rgb" | "#RRGGBB"), ou undefined se ele herda a cor global. */
  gravado: string | undefined;
  /** A cor original do avatar (a do agente), para o seletor começar nela. */
  corBase: string;
  pendente: boolean;
  erro: string | null;
  onMudar: (r: RascunhoCor) => void;
  onAplicar: (valor: string) => void;
  onHerdar: () => void;
}) {
  const id = useId();
  const entrada = entradaDoRascunho(rascunho);
  const custom = rascunho.modo === "custom";
  const invalido = custom && entrada === null;
  const salva = entradaDeValor(gravado);
  const igual = entrada !== null && salva !== null && valorDeEntrada(entrada) === valorDeEntrada(salva);
  return (
    <div className="avatar-color" role="group" aria-label={`Cor do corpo: ${nome}`}>
      <strong className="small">Cor do corpo</strong>
      <Segmented<ModoCor>
        label="Modo da cor do corpo"
        value={rascunho.modo}
        disabled={pendente}
        onChange={(modo) => onMudar(editarRascunho(rascunho, { modo }))}
        options={[
          { value: "default", label: "Padrão", title: "A cor original do avatar (a do agente)" },
          { value: "custom", label: "Personalizada", title: "Uma cor só, que você escolhe" },
          { value: "rgb", label: "RGB", title: "O corpo passa por todas as cores, uma volta a cada 12 s" },
        ]}
      />
      {custom && (
        <div className="color-row">
          <input
            type="color"
            aria-label="Escolher a cor do corpo"
            value={(normalizarHex(rascunho.texto) ?? corBase).toLowerCase()}
            disabled={pendente}
            onChange={(e) => onMudar(editarRascunho(rascunho, { modo: "custom", texto: e.target.value.toUpperCase() }))}
          />
          <label htmlFor={`${id}-hex`} className="secondary">
            Cor
          </label>
          <input
            id={`${id}-hex`}
            className="mono avatar-color-hex"
            type="text"
            inputMode="text"
            spellCheck={false}
            autoComplete="off"
            value={rascunho.texto}
            disabled={pendente}
            aria-invalid={invalido}
            aria-describedby={invalido ? `${id}-erro` : undefined}
            placeholder="#RRGGBB"
            onChange={(e) => onMudar(editarRascunho(rascunho, { modo: "custom", texto: e.target.value }))}
          />
          {entrada?.modo === "custom" && <span className="swatch" style={{ background: entrada.cor }} role="img" aria-label={`Amostra da cor ${entrada.cor}`} />}
        </div>
      )}
      {invalido && (
        <p id={`${id}-erro`} className="avatar-color-erro small" role="alert">
          Use uma cor com 6 dígitos hexadecimais, como #3D7A4F.
        </p>
      )}
      {rascunho.modo === "rgb" && (
        <p className="muted small">Com “Reduzir movimento” ligado no macOS, o corpo fica parado na cor original.</p>
      )}
      {erro && <Notice tone="critical">{erro}</Notice>}
      <div className="avatar-color-actions">
        <Button variant="primary" size="sm" pending={pendente} disabled={entrada === null || igual} onClick={() => entrada && onAplicar(valorDeEntrada(entrada))}>
          Aplicar
        </Button>
        {gravado !== undefined && (
          <Button size="sm" variant="ghost" disabled={pendente} onClick={onHerdar}>
            Usar a cor global
          </Button>
        )}
      </div>
    </div>
  );
}
