// O OpenCode nas superfícies do app: cartão do Painel, página Agentes (plugin que você liga) e o mascote, que não
// comemora um "concluído" quando o trabalho some sem um fim confirmado.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockIntegrations, mockProviders, mockSnapshot } from "../../services/mock";
import { fakeApi, renderApp } from "../../test/render";
import type { ProviderInfo } from "../../types/ombro";
import { current, EMPTY_MEMORY, step, type Retrato } from "../avatars/state";
import { ProviderCard } from "../dashboard/ProviderCard";
import { providerBadge } from "../../lib/format";
import type { Session } from "../../types/ombro";

const opencode = (extra: Partial<ProviderInfo> = {}): ProviderInfo => ({ ...mockProviders().find((p) => p.id === "opencode")!, ...extra });

describe("o mascote e o fim sem confirmação", () => {
  const trabalhando: Retrato = { state: "trabalhando", working: 1, waiting: 0, errors: 0, unconfirmed: 0 };
  const depois = (p: Retrato) => current(step(step(EMPTY_MEMORY, trabalhando, 0), p, 1000), 1001);

  it("um fim confirmado comemora", () => {
    expect(depois({ state: "ocioso", working: 0, waiting: 0, errors: 0, unconfirmed: 0 })).toBe("concluido");
  });

  it.each([
    ["o contador mudou", { state: "ocioso", unconfirmed: 1 }],
    ["estado desconhecido", { state: "desconhecido", unconfirmed: 0 }],
    ["plugin parado", { state: "sem_plugin", unconfirmed: 0 }],
    ["origem em conflito", { state: "conflito", unconfirmed: 0 }],
    ["coleta com erro", { state: "ocioso", unconfirmed: 0, collection: "error" }],
    ["desconectado", { state: "desconectado", unconfirmed: 0 }],
  ] as const)("não comemora quando %s", (_nome, p) => {
    expect(depois({ working: 0, waiting: 0, errors: 0, ...p } as Retrato)).toBe("ocioso");
  });

  it("os outros agentes (sem o contador) seguem como antes", () => {
    const antes = step(EMPTY_MEMORY, { state: "trabalhando", working: 1, waiting: 0, errors: 0 }, 0);
    expect(current(step(antes, { state: "ocioso", working: 0, waiting: 0, errors: 0 }, 1000), 1001)).toBe("concluido");
  });
});

describe("OpenCode no Painel", () => {
  it("diz que a versão não tem suporte e como atualizar", () => {
    render(
      <ProviderCard p={opencode({ state: "incompativel", condition: "incompatible", condition_detail: "OpenCode 2.0.17: atualize à mão para a 2.4.2 ou mais nova" })} />,
    );
    const cartao = screen.getByRole("region", { name: "OpenCode" });
    expect(within(cartao).getByText("Versão sem suporte")).toBeInTheDocument();
    expect(within(cartao).getByText(/atualize à mão para a 2\.4\.2/)).toBeInTheDocument();
  });

  it("não mostra tokens, custo nem limites", () => {
    render(<ProviderCard p={opencode()} />);
    expect(screen.queryByText(/sessão 5h|tokens hoje|US\$/)).not.toBeInTheDocument();
  });
});

describe("OpenCode desconectado: último estado conhecido", () => {
  const sessao = (extra: Partial<Session>): Session => ({
    provider: "opencode", session_id: "oc:ses_a", project: "api", status: "idle", activity: "idle", attention: "none",
    started_at: null, status_since: null, updated_at: null, ...extra,
  });

  it("o cartão diz desconectado e quantas sessões são só histórico", () => {
    const p = opencode({ state: "desconectado", condition: "offline", sessions: 0, working: 0, waiting: 0, errors: 0, disconnected_sessions: 2 });
    expect(providerBadge(p)).toEqual({ label: "Desconectado", tone: "muted" });
    render(<ProviderCard p={p} />);
    expect(screen.getByText("Desconectado")).toBeInTheDocument();
    expect(screen.getByText(/2 sessões desconectadas · último estado conhecido/)).toBeInTheDocument();
    expect(screen.queryByText("Ocioso")).not.toBeInTheDocument();
  });

  it("desligado, sem sessões e ocioso confirmado continuam distintos", () => {
    expect(providerBadge(opencode({ enabled: false, state: "desconectado" })).label).toBe("Desligado");
    expect(providerBadge(opencode({ state: "sem_sessoes" })).label).toBe("Sem sessões");
    expect(providerBadge(opencode({ state: "ocioso" })).label).toBe("Ocioso");
    expect(providerBadge(opencode({ state: "desconhecido" })).label).toBe("Sem dados");
  });

  it("a lista rotula cada sessão pela origem dela, sem mudar o histórico", async () => {
    const sessions = [
      sessao({ session_id: "a:1", project: "antiga", status: "error", disconnected: true }),
      sessao({ session_id: "a:2", project: "parada", disconnected: true }),
      sessao({ session_id: "b:1", project: "viva" }),
    ];
    const snap = mockSnapshot();
    snap.sessions!.sessions = sessions;
    renderApp(fakeApi({ getSnapshot: async () => snap }), "dashboard");
    await screen.findByText("antiga");
    const linha = (projeto: string) => screen.getByText(projeto).closest("li")!.textContent ?? "";
    expect(linha("antiga")).toContain("último: erro");
    expect(linha("parada")).toContain("último: ocioso");
    expect(linha("viva")).toContain("ocioso");
    expect(linha("viva")).not.toContain("último");
    expect(sessions.map((x) => x.status)).toEqual(["error", "idle", "idle"]);
  });

  it("o histórico não aparece em Precisa de você", async () => {
    const snap = mockSnapshot();
    snap.sessions!.sessions = [sessao({ status: "error", attention: "error", disconnected: true })];
    renderApp(fakeApi({ getSnapshot: async () => snap }), "dashboard");
    expect(await screen.findByText("último: erro")).toBeInTheDocument();
    expect(screen.queryByText("Precisa de você")).not.toBeInTheDocument();
  });
});

describe("OpenCode em Agentes", () => {
  it("mostra o plugin e o trecho, sem desfazer nem reparar um arquivo que o Ombro não edita", async () => {
    const trecho = '{"plugins": [{"package": "/x/integrations/opencode", "options": {"origin": "oc-abc"}}]}';
    const integrations = mockIntegrations().map((c) => (c.provider === "opencode" ? { ...c, state: "partial" as const, existe: true, presentes: [], trecho } : c));
    renderApp(fakeApi({ integrationsList: async () => ({ exit_code: 0, result: { integrations } }) }), "agents");
    const cartao = await screen.findByRole("region", { name: "OpenCode" });
    expect(await within(cartao).findByText("Esperando o plugin")).toBeInTheDocument();
    expect(within(cartao).getByText("Ainda não apareceu")).toBeInTheDocument();
    expect(within(cartao).getByLabelText("Trecho para o OpenCode")).toHaveTextContent("oc-abc");
    expect(within(cartao).getByText("2.4.2")).toBeInTheDocument();
    expect(within(cartao).queryByRole("button", { name: /Desfazer|Reparar/ })).not.toBeInTheDocument();
    await userEvent.click(within(cartao).getByRole("button", { name: "Remover" }));
    expect(await screen.findByText(/Tire também o trecho/)).toBeInTheDocument();
  });
});
