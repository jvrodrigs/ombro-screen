import { AlertTriangle } from "lucide-react";
import { Badge, Button, Card, UsageBar } from "../../components/ui";
import { ago, clock, pluralize, providerBadge, providerName, tokens, until } from "../../lib/format";
import type { ProviderInfo, Quotas } from "../../types/ombro";
import { ProviderAvatar } from "../avatars/ProviderAvatar";

const STALE_PARTS: Record<string, string> = { sessions: "sessões", usage: "tokens", quotas: "limites" };

/**
 * Os limites da assinatura, sempre com a idade do dado à vista. Os percentuais são da assinatura inteira, mas só
 * há número novo quando o Claude Code no terminal conversa ou roda /usage: o uso pelo app Claude não atualiza.
 * Por isso um número velho nunca aparece como se fosse de agora, e uma janela que reiniciou fica sem número.
 */
function QuotaBlock({ q, now }: { q: Quotas; now: Date }) {
  const observado = q.observed_at ?? q.fetched_at;
  const estado = q.state ?? (q.stale ? "stale" : "fresh");
  const idade = ago(observado, now);
  return (
    <div className="provider-quotas">
      {q.windows.map((w, i) => {
        const nome = w.id === "5h" ? "Sessão de 5 horas" : w.id === "7d" ? "Semana" : w.label;
        if (w.expired) {
          return (
            <div className="usage" key={w.id}>
              <div className="usage-row">
                <span className="usage-label">{nome}</span>
                <span className="usage-value muted">Aguardando atualização</span>
              </div>
              <div className="usage-track" role="progressbar" aria-label={nome} aria-valuemin={0} aria-valuemax={100} aria-valuetext="aguardando atualização" />
              <div className="usage-hint muted">
                A janela reiniciou{w.resets_at ? ` às ${clock(w.resets_at, now)}` : ""}; o Ombro ainda não viu o número novo.
              </div>
            </div>
          );
        }
        return (
          <UsageBar
            key={w.id}
            label={nome}
            value={w.used_percent}
            accent={i === 0 ? "orange" : "blue"}
            hint={w.resets_at ? `reinicia ${until(w.resets_at, now)} (${clock(w.resets_at, now)})` : "sem horário de reset"}
          />
        );
      })}
      {estado === "fresh" ? (
        <p className="quota-age muted">Atualizado {idade}</p>
      ) : (
        <>
          <p className="quota-age stale">
            <AlertTriangle size={13} aria-hidden />{" "}
            {estado === "awaiting_update" ? `Aguardando atualização · última observação ${idade}` : `Último valor conhecido · ${idade}`}
          </p>
          {!q.auto_update && (
            <p className="muted small">
              Os limites chegam quando o Claude Code no terminal conversa ou roda /usage. O uso pelo app Claude conta no
              limite, mas não atualiza estes números (fonte: {q.source}).
            </p>
          )}
        </>
      )}
    </div>
  );
}

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
              <QuotaBlock q={p.quotas} now={now} />
            ) : (
              <div className="provider-quotas">
                <p className="quota-age">Dados indisponíveis</p>
                <p className="muted small">
                  O Ombro ainda não viu os limites desta assinatura. Eles chegam quando o Claude Code no terminal conversa (com o
                  statusline ligado) ou roda /usage.
                </p>
              </div>
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
