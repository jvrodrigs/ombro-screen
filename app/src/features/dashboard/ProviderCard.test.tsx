import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { mockProviders } from "../../services/mock";
import { ProviderCard } from "./ProviderCard";

const [claude, codex] = mockProviders();

describe("ProviderCard", () => {
  it("Claude: as duas janelas de uso, o reset e os tokens", () => {
    render(<ProviderCard p={claude} />);
    expect(screen.getByRole("progressbar", { name: "Sessão 5 h" })).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByRole("progressbar", { name: "Semana" })).toHaveAttribute("aria-valuenow", "67");
    expect(screen.getAllByText(/reinicia em/)).toHaveLength(2);
    expect(screen.getByText("18,6 M")).toBeInTheDocument();
  });

  it("Codex: sem barras vazias nem quotas inventadas", () => {
    render(<ProviderCard p={codex} />);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByText("Este agente não informa limites de uso.")).toBeInTheDocument();
    expect(screen.queryByText(/Tokens hoje/)).toBeNull();
  });

  it("dados velhos aparecem marcados", () => {
    render(<ProviderCard p={{ ...claude, quotas: { ...claude.quotas!, stale: true }, stale_parts: ["usage"] }} />);
    expect(screen.getByText(/Números de/)).toBeInTheDocument();
    expect(screen.getByText(/Desatualizado: tokens/)).toBeInTheDocument();
  });

  it("janela com percentual nulo mostra traço", () => {
    const p = { ...claude, quotas: { ...claude.quotas!, windows: [{ id: "5h", label: "sessão 5h", used_percent: null, resets_at: null }] } };
    render(<ProviderCard p={p} />);
    const bar = screen.getByRole("progressbar", { name: "Sessão 5 h" });
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
