import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mockProviders } from "../../services/mock";
import { ProviderCard } from "./ProviderCard";

const [claude, codex] = mockProviders();
/** Um horário como o ombrod manda: ISO local, sem fuso. */
const local = (d: Date) => new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 19);
/** A linha da idade dos limites. */
const idade = () => document.querySelector(".quota-age")?.textContent ?? "";

describe("ProviderCard", () => {
  it("Claude: as duas janelas de uso, o reset e os tokens", () => {
    render(<ProviderCard p={claude} />);
    expect(screen.getByRole("progressbar", { name: "Sessão de 5 horas" })).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByRole("progressbar", { name: "Semana" })).toHaveAttribute("aria-valuenow", "67");
    expect(screen.getAllByText(/reinicia em/)).toHaveLength(2);
    expect(screen.getByText("18,6 M")).toBeInTheDocument();
    expect(screen.getByText("Atualizado há 2 min")).toBeInTheDocument(); // a idade do dado, sempre à vista
    expect(screen.queryByText(/Último valor conhecido/)).toBeNull();
  });

  it("Codex: sem barras vazias nem quotas inventadas", () => {
    render(<ProviderCard p={codex} />);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByText("Este agente não informa limites de uso.")).toBeInTheDocument();
    expect(screen.queryByText(/Tokens hoje/)).toBeNull();
  });

  it("dados velhos aparecem como o último valor conhecido, com a idade e o porquê", () => {
    const agora = new Date();
    const ha38 = local(new Date(agora.getTime() - 38 * 60_000));
    const q = { ...claude.quotas!, stale: true, state: "stale" as const, fetched_at: ha38, observed_at: ha38 };
    render(<ProviderCard p={{ ...claude, quotas: q, stale_parts: ["usage"] }} now={agora} />);
    expect(idade()).toContain("Último valor conhecido · há 38 min");
    expect(idade()).not.toContain("Atualizado"); // nunca como se fosse de agora
    expect(screen.getByText(/O uso pelo app Claude conta no\s+limite, mas não atualiza estes números/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Sessão de 5 horas" })).toHaveAttribute("aria-valuenow", "42"); // o número fica, marcado
    expect(screen.getByText(/Desatualizado: tokens/)).toBeInTheDocument();
  });

  it("um ombrod de antes (sem state) continua marcando os dados velhos", () => {
    const { state: _s, observed_at: _o, ...antigo } = { ...claude.quotas!, stale: true };
    render(<ProviderCard p={{ ...claude, quotas: antigo }} />);
    expect(screen.getByText(/Último valor conhecido/)).toBeInTheDocument();
  });

  it("sem nenhuma fonte: dados indisponíveis, sem barra nem número", () => {
    render(<ProviderCard p={{ ...claude, quotas: null }} />);
    expect(screen.getByText("Dados indisponíveis")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("janela que reiniciou: aguardando atualização, nunca 0%", () => {
    const q = {
      ...claude.quotas!, state: "awaiting_update" as const,
      windows: [
        { id: "5h", label: "sessão 5h", used_percent: null, resets_at: claude.quotas!.fetched_at, expired: true },
        { id: "7d", label: "semana", used_percent: 67, resets_at: claude.quotas!.windows[1].resets_at, expired: false },
      ],
    };
    render(<ProviderCard p={{ ...claude, quotas: q }} />);
    const cinco = screen.getByRole("progressbar", { name: "Sessão de 5 horas" });
    expect(cinco).not.toHaveAttribute("aria-valuenow");
    expect(cinco).toHaveAttribute("aria-valuetext", "aguardando atualização");
    expect(screen.getByText(/A janela reiniciou às/)).toBeInTheDocument();
    expect(screen.queryByText("0%")).toBeNull();
    expect(screen.getByRole("progressbar", { name: "Semana" })).toHaveAttribute("aria-valuenow", "67");
    expect(screen.getByText(/Aguardando atualização · última observação/)).toBeInTheDocument();
  });

  it("um número novo do ombrod troca a barra e a idade sem recarregar nada", () => {
    const agora = new Date();
    const em = (min: number) => local(new Date(agora.getTime() - min * 60_000));
    const velho = { ...claude.quotas!, stale: true, state: "stale" as const, fetched_at: em(56), observed_at: em(56) };
    const { rerender } = render(<ProviderCard p={{ ...claude, quotas: { ...velho, windows: [{ ...velho.windows[0], used_percent: 32 }, { ...velho.windows[1], used_percent: 54 }] } }} now={agora} />);
    expect(screen.getByRole("progressbar", { name: "Sessão de 5 horas" })).toHaveAttribute("aria-valuenow", "32");
    const novo = { ...claude.quotas!, fetched_at: em(0), observed_at: em(0), windows: [{ ...velho.windows[0], used_percent: 43 }, { ...velho.windows[1], used_percent: 56 }] };
    rerender(<ProviderCard p={{ ...claude, quotas: novo }} now={agora} />); // o retrato seguinte do ombrod
    expect(screen.getByRole("progressbar", { name: "Sessão de 5 horas" })).toHaveAttribute("aria-valuenow", "43");
    expect(screen.getByRole("progressbar", { name: "Semana" })).toHaveAttribute("aria-valuenow", "56");
    expect(idade()).toMatch(/^Atualizado/);
    expect(screen.queryByText(/Último valor conhecido/)).toBeNull();
  });

  it("janela com percentual nulo mostra traço", () => {
    const p = { ...claude, quotas: { ...claude.quotas!, windows: [{ id: "5h", label: "sessão 5h", used_percent: null, resets_at: null }] } };
    render(<ProviderCard p={p} />);
    const bar = screen.getByRole("progressbar", { name: "Sessão de 5 horas" });
    expect(bar).not.toHaveAttribute("aria-valuenow");
    expect(bar).toHaveAttribute("aria-valuetext", "sem dado");
  });

  it("não instalado e sem integração", () => {
    const { rerender } = render(<ProviderCard p={{ ...codex, installed: false, state: "nao_instalado" }} />);
    expect(screen.getByText("Não encontrado neste Mac.")).toBeInTheDocument();
    const configurar = vi.fn();
    rerender(<ProviderCard p={{ ...codex, integration: "not_configured", state: "nao_configurado", sessions: 0 }} onConfigure={configurar} />);
    screen.getByRole("button", { name: "Configurar integração" }).click();
    expect(configurar).toHaveBeenCalled();
  });
});
