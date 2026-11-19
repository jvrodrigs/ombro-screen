import { useMemo, useState } from "react";
import { Shuffle } from "lucide-react";
import { Button, Card, Dialog, Notice, Segmented } from "../../components/ui";
import { useOmbro } from "../../hooks/useOmbro";
import { errorMessage } from "../../services/api";
import type { ConfigInfo, ProviderId } from "../../types/ombro";
import { AvatarView } from "./AvatarView";
import { PROPORCAO_PALCO } from "./animacoes";
import { catalog, chooseAvatar, colorFor, type AvatarChoice, type AvatarSlot, type RandomMode } from "./catalog";
import type { MascotState } from "./state";

const SLOTS: { slot: AvatarSlot; label: string; hint: string }[] = [
  { slot: "claude", label: "Claude Code", hint: "Nos layouts do Ombro e Minimal v2, e no multiagente" },
  { slot: "codex", label: "Codex", hint: "No multiagente, quando é a vez do Codex" },
  { slot: "cursor", label: "Cursor", hint: "No multiagente, quando é a vez do Cursor" },
  { slot: "padrao", label: "Ombro", hint: "Quando nenhum agente está em foco no multiagente" },
];

// os quatro estados principais, com as animações do pacote em uso (as mesmas da telinha)
export const ESTADOS: { value: MascotState; label: string }[] = [
  { value: "ocioso", label: "Ocioso" },
  { value: "trabalhando", label: "Trabalhando" },
  { value: "esperando", label: "Esperando" },
  { value: "concluido", label: "Concluído" },
];

function providerOf(slot: AvatarSlot): ProviderId | null {
  return slot === "padrao" ? null : slot;
}

/** Avatares por agente: padrão, um específico ou aleatório; e o modo do aleatório. Grava pelo ombrod. */
export function AvatarSettings({ config, onChanged }: { config: ConfigInfo | null; onChanged: () => void }) {
  const { api, snapshot } = useOmbro();
  const cat = catalog();
  const escolhas = (config?.values.avatares ?? {}) as Record<string, AvatarChoice>;
  const sorteio = (config?.values.avatar_sorteio ?? "inicio") as RandomMode;
  const [aberto, setAberto] = useState<AvatarSlot | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [salvando, setSalvando] = useState(false);

  // o que o ombrod está usando agora (o aleatório ao iniciar já sorteado); sem ele, a mesma regra calculada aqui
  const resolvidos = useMemo(() => {
    const m = new Map<AvatarSlot, { id: string; color: string; choice: string }>();
    for (const p of snapshot?.providers?.providers ?? []) if (p.avatar) m.set(p.id, { id: p.avatar.avatar, color: p.avatar.color, choice: p.avatar.choice });
    const o = snapshot?.providers?.ombro_avatar;
    if (o) m.set("padrao", { id: o.avatar, color: o.color, choice: o.choice });
    return m;
  }, [snapshot]);

  const atual = (slot: AvatarSlot) => {
    // o do ombrod só se for da preferência gravada agora (logo depois de mudar, o retrato ainda é o anterior)
    const r0 = resolvidos.get(slot);
    const r = r0 && r0.choice === (escolhas[slot] ?? "padrao") ? r0 : undefined;
    const av = (r && cat.byId.get(r.id)) || chooseAvatar(cat, escolhas[slot], providerOf(slot), sorteio);
    return { av, color: r?.color ?? colorFor(cat, providerOf(slot), av) };
  };

  const salvar = async (mudancas: Record<string, string>) => {
    setSalvando(true);
    setErro(null);
    try {
      await api.configUpdate(mudancas, true);
      onChanged();
      await api.refresh();
    } catch (e) {
      setErro(errorMessage(e));
    } finally {
      setSalvando(false);
    }
  };

  const rotulo = (escolha: AvatarChoice | undefined, slot: AvatarSlot) => {
    if (!escolha || escolha === "padrao") return slot === "padrao" ? "Padrão do Ombro" : "Padrão do agente";
    if (escolha === "aleatorio") return sorteio === "sessao" ? "Aleatório, um por sessão" : "Aleatório, sorteado ao iniciar";
    return cat.byId.has(escolha) ? "Escolhido por você" : `${escolha} não existe: vale o padrão`;
  };

  return (
    <Card>
      <div className="card-title">
        <h2>Avatares</h2>
      </div>
      {erro && <Notice tone="critical">{erro}</Notice>}
      {cat.invalid.length > 0 && (
        <Notice tone="warning" title="Avatares ignorados">
          {cat.invalid.map((i) => i.id).join(", ")} não passaram na validação do mascotinhos.
        </Notice>
      )}
      <ul className="avatar-slots">
        {SLOTS.map(({ slot, label, hint }) => {
          const { av, color } = atual(slot);
          return (
            <li key={slot}>
              <AvatarView definition={av.definition} color={color} size={44} extras={false} label={`${label}: ${av.name}`} />
              <div className="avatar-slot-text">
                <strong>{label}</strong>
                <span className="secondary small">
                  {av.name} · {rotulo(escolhas[slot], slot)}
                </span>
                <span className="muted small">{hint}</span>
              </div>
              <Button size="sm" disabled={!config} onClick={() => setAberto(slot)}>
                Escolher…
              </Button>
            </li>
          );
        })}
      </ul>
      <div className="field">
        <span className="field-label">Aleatório</span>
        <Segmented<RandomMode>
          label="Como o aleatório escolhe"
          value={sorteio}
          disabled={!config || salvando}
          onChange={(v) => salvar({ avatar_sorteio: v })}
          options={[
            { value: "inicio", label: "Um ao iniciar", title: "Sorteado quando o Ombro inicia; o mesmo até reiniciar" },
            { value: "sessao", label: "Um por sessão", title: "Cada sessão tem o seu avatar, sempre o mesmo para ela" },
          ]}
        />
        <p className="muted small">Vale para quem estiver em "Aleatório". O sorteio usa os {cat.pool.length} avatares da coleção.</p>
      </div>
      {aberto && (
        <AvatarPicker
          slot={aberto}
          label={SLOTS.find((s) => s.slot === aberto)!.label}
          current={escolhas[aberto] ?? "padrao"}
          pending={salvando}
          onClose={() => setAberto(null)}
          onPick={async (escolha) => {
            await salvar({ [`avatares.${aberto}`]: escolha });
            setAberto(null);
          }}
        />
      )}
    </Card>
  );
}

/** A galeria: escolher padrão, aleatório ou um avatar; a prévia grande anima nos estados principais. */
function AvatarPicker({
  slot,
  label,
  current,
  pending,
  onClose,
  onPick,
}: {
  slot: AvatarSlot;
  label: string;
  current: AvatarChoice;
  pending: boolean;
  onClose: () => void;
  onPick: (choice: AvatarChoice) => void;
}) {
  const cat = catalog();
  const provider = providerOf(slot);
  const [sel, setSel] = useState<AvatarChoice>(current);
  const [estado, setEstado] = useState<MascotState>("trabalhando");
  const padrao = chooseAvatar(cat, "padrao", provider);
  const mostrado = sel === "aleatorio" ? null : sel === "padrao" ? padrao : (cat.byId.get(sel) ?? padrao);
  const cor = (id: string) => colorFor(cat, provider, cat.byId.get(id)!);

  return (
    <Dialog
      open
      wide
      title={`Avatar: ${label}`}
      onClose={onClose}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancelar
          </Button>
          <Button variant="primary" pending={pending} disabled={sel === current} onClick={() => onPick(sel)}>
            Usar
          </Button>
        </>
      }
    >
      <div className="avatar-picker">
        <div className="avatar-picker-preview">
          {/* a moldura tem a proporção do palco: trocar de avatar, de estado ou ir para o Aleatório não mexe no layout */}
          <div className="avatar-picker-stage" style={{ aspectRatio: PROPORCAO_PALCO }}>
            {mostrado ? (
              <AvatarView definition={mostrado.definition} color={cor(mostrado.id)} state={estado} live enquadre="palco" />
            ) : (
              <div className="avatar-random" aria-label="Aleatório">
                <Shuffle size={40} />
              </div>
            )}
          </div>
          <strong>{mostrado ? mostrado.name : "Aleatório"}</strong>
          <p className="muted small">
            {mostrado ? mostrado.description : "Um dos avatares da coleção, sorteado ao iniciar o Ombro ou um por sessão (Aparência > Avatares)."}
          </p>
          {mostrado && <Segmented<MascotState> label="Estado da prévia" value={estado} onChange={setEstado} options={ESTADOS} />}
        </div>
        <div className="avatar-grid" role="radiogroup" aria-label="Avatares">
          <button type="button" role="radio" aria-checked={sel === "padrao"} className={`avatar-choice ${sel === "padrao" ? "active" : ""}`} onClick={() => setSel("padrao")}>
            <AvatarView definition={padrao.definition} color={cor(padrao.id)} size={56} extras={false} label={padrao.name} />
            <span>{slot === "padrao" ? "Padrão do Ombro" : "Padrão do agente"}</span>
          </button>
          <button type="button" role="radio" aria-checked={sel === "aleatorio"} className={`avatar-choice ${sel === "aleatorio" ? "active" : ""}`} onClick={() => setSel("aleatorio")}>
            <span className="avatar-random small-random" aria-hidden>
              <Shuffle size={22} />
            </span>
            <span>Aleatório</span>
          </button>
          {cat.avatars.map((a) => (
            <button
              key={a.id}
              type="button"
              role="radio"
              aria-checked={sel === a.id}
              className={`avatar-choice ${sel === a.id ? "active" : ""}`}
              title={a.description}
              onClick={() => setSel(a.id)}
            >
              <AvatarView definition={a.definition} color={cor(a.id)} size={56} extras={false} label={a.name} />
              <span>{a.name}</span>
            </button>
          ))}
        </div>
      </div>
    </Dialog>
  );
}
