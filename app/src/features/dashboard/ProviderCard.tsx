import { AlertTriangle } from "lucide-react";
import { Badge, Button, Card, UsageBar } from "../../components/ui";
import { ago, clock, pluralize, providerBadge, providerName, tokens, until } from "../../lib/format";
import type { ProviderInfo } from "../../types/ombro";
import { ProviderAvatar } from "../avatars/ProviderAvatar";

const STALE_PARTS: Record<string, string> = { sessions: "sessões", usage: "tokens", quotas: "limites" };

/** O cartão de um agente: só mostra o que o provider realmente informa (sem barras vazias nem números inventados). */
export function ProviderCard({ p, onConfigure, now = new Date() }: { p: ProviderInfo; onConfigure?: () => void; now?: Date }) {
  const badge = providerBadge(p);
  const caps = new Set(p.capabilities ?? []);
  const ativo = p.enabled && p.installed !== false && p.integration !== "not_configured";
  const partes: string[] = [];
  if (p.working) partes.push(`${p.working} trabalhando`);
  if (p.waiting) partes.push(`${p.waiting} esperando`);
  if (p.errors) partes.push(pluralize(p.errors, "com erro", "com erro"));
  const ultima = p.last_event_at ?? p.last_collected_at;

  return (
    <Card className={`provider-card ${ativo ? "" : "inactive"}`} aria-label={providerName(p)}>
      <div className="card-title">
        <h2>
          <ProviderAvatar p={p} />
          {providerName(p)}
        </h2>
        <Badge tone={badge.tone}>{badge.label}</Badge>
      </div>

      {!p.enabled && <p className="muted">O Ombro não está acompanhando este agente (Ajustes da tela).</p>}
      {p.enabled && p.installed === false && <p className="muted">Não encontrado neste Mac.</p>}
      {p.enabled && p.installed !== false && p.integration === "not_configured" && (
        <div className="stack">
          <p className="secondary">Instalado, mas sem a integração do Ombro: as sessões não aparecem.</p>
          {onConfigure && (
            <Button size="sm" onClick={onConfigure}>
              Configurar integração
            </Button>
          )}
        </div>
      )}

      {ativo && (
        <>
          <p className="provider-counts">
            <span className="mono big">{p.sessions ?? 0}</span>{" "}
            <span className="secondary">{(p.sessions ?? 0) === 1 ? "sessão" : "sessões"}</span>
            {partes.length > 0 && <span className="secondary"> · {partes.join(" · ")}</span>}
          </p>

          {caps.has("quota_windows") ? (
            p.quotas && p.quotas.windows.length > 0 ? (
              <div className="provider-quotas">
                {p.quotas.windows.map((w, i) => (
                  <UsageBar
                    key={w.id}
                    label={w.id === "5h" ? "Sessão 5 h" : w.id === "7d" ? "Semana" : w.label}
                    value={w.used_percent}
                    accent={i === 0 ? "orange" : "blue"}
                    hint={w.resets_at ? `reinicia ${until(w.resets_at, now)} (${clock(w.resets_at, now)})` : "sem horário de reset"}
                  />
                ))}
                {p.quotas.stale && (
                  <p className="stale">
                    <AlertTriangle size={13} aria-hidden /> Números de {ago(p.quotas.fetched_at, now).replace("há ", "")} atrás
                    (fonte: {p.quotas.source})
                  </p>
                )}
              </div>
            ) : (
              <p className="muted small">Sem dados de limite ainda. Rode /usage no agente ou ligue o statusline.</p>
            )
          ) : (
            <p className="muted small">Este agente não informa limites de uso.</p>
          )}

          {caps.has("token_usage") && p.usage && (
            <p className="provider-tokens">
              <span className="muted">Tokens hoje</span> <span className="mono">{tokens(p.usage.total_tokens)}</span>
              {p.usage.output_tokens != null && (
                <>
                  <span className="muted"> · saída</span> <span className="mono">{tokens(p.usage.output_tokens)}</span>
                </>
              )}
            </p>
          )}

          {(p.stale_parts?.length ?? 0) > 0 && (
            <p className="stale">
              <AlertTriangle size={13} aria-hidden /> Desatualizado: {p.stale_parts!.map((s) => STALE_PARTS[s] ?? s).join(", ")}
            </p>
          )}
        </>
      )}

      {p.enabled && p.installed !== false && <p className="provider-updated muted">Atualizado {ago(ultima, now)}</p>}
    </Card>
  );
}
