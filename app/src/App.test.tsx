import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { mockSnapshot } from "./services/mock";
import { fakeApi, renderApp } from "./test/render";
import type { Snapshot } from "./types/ombro";

describe("navegação", () => {
  it("abre no painel e troca de seção por clique e por ⌘2", async () => {
    renderApp(fakeApi());
    expect(await screen.findByRole("heading", { level: 1, name: "Painel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Painel/ })).toHaveAttribute("aria-current", "page");

    await userEvent.click(screen.getByRole("button", { name: /Tela/ }));
    expect(await screen.findByRole("heading", { level: 1, name: "Tela" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "2", metaKey: true });
    expect(await screen.findByRole("heading", { level: 1, name: "Agentes" })).toBeInTheDocument();
  });

  it("todas as seções abrem sem erro", async () => {
    renderApp(fakeApi());
    await screen.findByRole("heading", { level: 1, name: "Painel" });
    for (const [n, titulo] of [["3", "Tela"], ["4", "Aparência"], ["5", "Diagnóstico"], ["6", "Ajustes"]]) {
      fireEvent.keyDown(window, { key: n, metaKey: true });
      expect(await screen.findByRole("heading", { level: 1, name: titulo })).toBeInTheDocument();
    }
  });
});

describe("painel", () => {
  it("mostra quem espera você e os agentes", async () => {
    renderApp(fakeApi());
    const urgentes = await screen.findByText("Precisa de você");
    expect(urgentes).toBeInTheDocument();
    expect(screen.getAllByText("site").length).toBeGreaterThan(0);
    expect(screen.getByRole("region", { name: "Claude Code" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Codex" })).queryByRole("progressbar")).toBeNull();
  });

  it("o serviço fora vira aviso com ação, sem derrubar a página", async () => {
    const parado: Snapshot = { ...mockSnapshot(), daemon: { state: "failed", reason: "o ombrod não está rodando" }, status: null, providers: null, sessions: null, health: null };
    const api = fakeApi({ getSnapshot: () => Promise.resolve(parado), onSnapshot: async () => () => {} });
    renderApp(api);
    expect(await screen.findByText("O Ombro não está monitorando")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Painel" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Iniciar monitoramento" }));
    expect(api.daemonStart).toHaveBeenCalledTimes(1);
  });

  it("um provider com erro não esconde os outros", async () => {
    const s = mockSnapshot();
    s.providers!.providers[1] = { ...s.providers!.providers[1], collection: "error", sessions: 0, state: "indisponivel" };
    renderApp(fakeApi({ getSnapshot: () => Promise.resolve(s), onSnapshot: async () => () => {} }));
    expect(await screen.findByText("Coleta com erro")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Claude Code" })).toBeInTheDocument();
  });

  it("retratos novos atualizam a tela", async () => {
    let emitir: (s: Snapshot) => void = () => {};
    renderApp(fakeApi({ onSnapshot: async (cb) => ((emitir = cb), () => {}) }));
    await screen.findByText("Precisa de você");
    const s = mockSnapshot();
    s.seq = 99;
    s.status!.waiting = 0;
    s.sessions!.sessions = [];
    act(() => emitir(s));
    await waitFor(() => expect(screen.queryByText("Precisa de você")).toBeNull());
    expect(screen.getByText("Nenhuma sessão aberta")).toBeInTheDocument();
  });
});

describe("integrações (consentimento)", () => {
  it("só grava depois da confirmação, e mostra o resultado real", async () => {
    const api = fakeApi();
    renderApp(api, "agents");
    const cursor = await screen.findByRole("region", { name: "Cursor" });
    await userEvent.click(await within(cursor).findByRole("button", { name: "Instalar integração" }));
    const dialogo = await screen.findByRole("dialog");
    expect(within(dialogo).getByText(/nunca guarda prompts/i)).toBeInTheDocument();
    expect(within(dialogo).getByLabelText("O que muda no arquivo")).toBeInTheDocument();
    expect(api.integrationRun).toHaveBeenCalledTimes(1);
    expect(api.integrationRun).toHaveBeenLastCalledWith("cursor", "install", true);

    await userEvent.click(within(dialogo).getByRole("button", { name: "Instalar integração" }));
    expect(api.integrationRun).toHaveBeenLastCalledWith("cursor", "install", false);
    expect(await within(dialogo).findByText("Integração instalada")).toBeInTheDocument();
  });

  it("cancelar não grava nada", async () => {
    const api = fakeApi();
    renderApp(api, "agents");
    const cursor = await screen.findByRole("region", { name: "Cursor" });
    await userEvent.click(await within(cursor).findByRole("button", { name: "Instalar integração" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancelar" }));
    expect(api.integrationRun).toHaveBeenCalledTimes(1);
    expect(api.integrationRun).not.toHaveBeenCalledWith("cursor", "install", false);
  });

  it("erro do gerenciador aparece como está, sem sucesso falso", async () => {
    const api = fakeApi({
      integrationRun: (p, a, dry) =>
        Promise.resolve({ exit_code: 1, result: { ok: false as const, provider: p, action: a, error: dry ? "~/.cursor/hooks.json não é um JSON válido" : "x" } }),
    });
    renderApp(api, "agents");
    const cursor = await screen.findByRole("region", { name: "Cursor" });
    await userEvent.click(await within(cursor).findByRole("button", { name: "Instalar integração" }));
    const dialogo = await screen.findByRole("dialog");
    expect(await within(dialogo).findByText(/não é um JSON válido/)).toBeInTheDocument();
    expect(within(dialogo).getByText("Nada foi alterado")).toBeInTheDocument();
  });
});

describe("tela e preferências", () => {
  it("layout troca na hora pelo ombrod", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    await userEvent.click(await screen.findByRole("radio", { name: /Multiagente/ }));
    expect(api.setLayout).toHaveBeenCalledWith("multiagente", true);
  });

  it("hardware mostra 'não salvo' até gravar", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    expect(await screen.findByText("Sem alterações")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "Girar 180°" }));
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Salvar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ girar: true }, true);
    expect(await screen.findByText(/vale depois de reiniciar o monitoramento/)).toBeInTheDocument();
  });
});

describe("avatares", () => {
  it("o painel mostra o avatar de cada agente", async () => {
    renderApp(fakeApi());
    // animados pelo estado real de cada um; o Cursor, sem a integração, fica parado
    expect(await screen.findByRole("img", { name: "Claude Code: Ombro Cloud, esperando você" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Codex: Órbita, esperando você" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Cursor: Cubo" })).toBeInTheDocument();
  });

  it("escolher um avatar grava pela IPC e aparece na hora", async () => {
    const api = fakeApi();
    renderApp(api, "appearance");
    await screen.findByRole("img", { name: "Codex: Órbita" });
    await userEvent.click(screen.getAllByRole("button", { name: "Escolher…" })[1]);
    const dialogo = await screen.findByRole("dialog");
    expect(within(within(dialogo).getByRole("radiogroup", { name: "Avatares" })).getAllByRole("radio")).toHaveLength(11); // padrão, aleatório e os nove
    await userEvent.click(within(dialogo).getByRole("radio", { name: /^Nova/ }));
    await userEvent.click(within(dialogo).getByRole("button", { name: "Usar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ "avatares.codex": "nova" }, true);
    expect(await screen.findByRole("img", { name: "Codex: Nova" })).toBeInTheDocument();
  });

  it("aleatório por sessão e cor do agente", async () => {
    const api = fakeApi();
    renderApp(api, "appearance");
    await userEvent.click(await screen.findByRole("radio", { name: "Um por sessão" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ avatar_sorteio: "sessao" }, true);
    await userEvent.click(screen.getByRole("radio", { name: "Sorteada" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ cor_mascote: null, cor_modo: "sorteada" }, true);
  });
});

describe("primeiro uso", () => {
  it("aparece até ser concluído, e grava a conclusão", async () => {
    const api = fakeApi({ prefsGet: () => Promise.resolve({ onboarding_completed: false, notifications: false, owned_daemon_pid: null, autostart: false }) });
    renderApp(api);
    await userEvent.click(await screen.findByRole("button", { name: "Começar" }));
    for (let i = 0; i < 4; i++) await userEvent.click(screen.getByRole("button", { name: "Continuar" }));
    expect(screen.getByRole("heading", { name: "Tudo pronto" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Abrir o painel" }));
    expect(api.prefsSet).toHaveBeenCalledWith({ onboarding_completed: true });
    expect(api.integrationRun).not.toHaveBeenCalledWith(expect.anything(), "install", false);
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "Painel" })).toBeInTheDocument());
  });
});

vi.setConfig({ testTimeout: 15000 });
