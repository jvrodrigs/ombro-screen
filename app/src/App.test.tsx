import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { createMockApi, mockAnimation, mockConfig, mockSnapshot } from "./services/mock";
import { fakeApi, renderApp } from "./test/render";
import type { ConfigSchema, ConfigUpdateResult, Snapshot } from "./types/ombro";

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

describe("ligar e desligar um agente", () => {
  it("o switch mostra o que foi gravado, e o aviso diz que vale ao reiniciar", async () => {
    const api = fakeApi();
    renderApp(api, "agents");
    const codex = await screen.findByRole("region", { name: "Codex" });
    const sw = within(codex).getByRole("switch", { name: "Acompanhar este agente" });
    expect(sw).toBeChecked();
    await userEvent.click(sw);
    expect(api.configUpdate).toHaveBeenCalledWith({ "providers.codex": false }, true);
    await waitFor(() => expect(sw).not.toBeChecked()); // antes voltava a aparecer ligado
    expect(await screen.findByRole("button", { name: "Reiniciar agora" })).toBeInTheDocument();
    await userEvent.click(sw); // voltou atrás: nada mais a reiniciar
    await waitFor(() => expect(sw).toBeChecked());
    await waitFor(() => expect(screen.queryByRole("button", { name: "Reiniciar agora" })).toBeNull());
  });
});

describe("tela e preferências", () => {
  it("layout troca na hora pelo ombrod", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    const multi = await screen.findByRole("radio", { name: /Multiagente/ });
    await userEvent.click(multi);
    expect(api.configUpdate).toHaveBeenCalledWith({ layout: "multiagente" }, true);
    await waitFor(() => expect(multi).toHaveAttribute("aria-checked", "true")); // o que o ombrod confirmou
    expect(screen.queryByText(/vale depois de reiniciar/)).toBeNull();
  });

  it("hardware mostra 'não salvo' até gravar, e depois o que ficou pendente", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    expect(await screen.findByText("Sem alterações")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "Usar a tela USB" }));
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Salvar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ usb: true }, true);
    expect(await screen.findByText("Gravado; vale depois de reiniciar o monitoramento", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Usar a tela USB" })).toBeChecked(); // o que foi gravado
    expect(screen.getByRole("button", { name: "Reiniciar agora" })).toBeInTheDocument();
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
    expect(within(within(dialogo).getByRole("radiogroup", { name: "Avatares" })).getAllByRole("radio")).toHaveLength(14); // padrão, aleatório e os doze
    await userEvent.click(within(dialogo).getByRole("radio", { name: /^Fantasma/ })); // um dos três novos
    // só as prévias grandes ficam no palco (com o espaço dos extras): a da galeria e a das animações (Aparência);
    // as miniaturas e os cartões seguem no corpo
    expect(dialogo.querySelectorAll(".avatar-palco")).toHaveLength(1);
    expect(dialogo.querySelector(".avatar-picker-stage .avatar-palco")).not.toBeNull();
    expect(document.querySelectorAll(".avatar-palco")).toHaveLength(2);
    expect(document.querySelector(".animation-preview .avatar-palco")).not.toBeNull();
    await userEvent.click(within(dialogo).getByRole("button", { name: "Usar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ "avatares.codex": "fantasma" }, true);
    expect(await screen.findByRole("img", { name: "Codex: Fantasma" })).toBeInTheDocument();
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

describe("cor do corpo do avatar (Aparência > Avatares)", () => {
  const abrir = async (api: ReturnType<typeof fakeApi>, avatar = /^Fantasma/) => {
    renderApp(api, "appearance");
    await screen.findByRole("img", { name: "Codex: Órbita" });
    await userEvent.click(screen.getAllByRole("button", { name: "Escolher…" })[1]);
    const dialogo = await screen.findByRole("dialog");
    await userEvent.click(within(dialogo).getByRole("radio", { name: avatar }));
    return dialogo;
  };

  it("personalizada: rascunho válido em tempo real, mensagem no inválido e Aplicar grava por id, persistente", async () => {
    const api = fakeApi();
    const dialogo = await abrir(api);
    const grupo = within(dialogo).getByRole("group", { name: /Cor do corpo: Fantasma/ });
    expect(within(grupo).getByRole("radio", { name: "Padrão" })).toBeChecked();
    expect(within(grupo).queryByLabelText("Cor")).toBeNull(); // o campo só aparece em Personalizada
    await userEvent.click(within(grupo).getByRole("radio", { name: "Personalizada" }));
    const campo = within(grupo).getByLabelText("Cor");
    await userEvent.clear(campo);
    await userEvent.type(campo, "#3D7A4");
    expect(within(grupo).getByRole("alert")).toHaveTextContent("6 dígitos hexadecimais");
    expect(campo).toHaveAttribute("aria-invalid", "true");
    expect(within(grupo).queryByRole("img", { name: /Amostra/ })).toBeNull();
    expect(within(grupo).getByRole("button", { name: "Aplicar" })).toBeDisabled();
    await userEvent.type(campo, "F");
    expect(within(grupo).queryByRole("alert")).toBeNull();
    expect(within(grupo).getByRole("img", { name: "Amostra da cor #3D7A4F" })).toBeInTheDocument(); // a amostra só em custom
    // o rascunho válido já está na prévia: o avatar troca (0,6 s) e a cor do destino entra junto com a forma
    await waitFor(() => expect(dialogo.querySelector(".avatar-picker-stage svg path")!.getAttribute("fill")!.toUpperCase()).toBe("#3D7A4F"), { timeout: 3000 });
    await userEvent.click(within(grupo).getByRole("button", { name: "Aplicar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ "cores_avatares.fantasma": "#3D7A4F" }, true);
  });

  it("Padrão grava default e RGB grava rgb, por avatar", async () => {
    const api = fakeApi();
    const dialogo = await abrir(api, /^Cubo/);
    const grupo = within(dialogo).getByRole("group", { name: /Cor do corpo: Cubo/ });
    await userEvent.click(within(grupo).getByRole("radio", { name: "RGB" }));
    expect(within(grupo).queryByLabelText("Cor")).toBeNull();
    expect(within(grupo).getByText(/Reduzir movimento/)).toBeInTheDocument();
    await userEvent.click(within(grupo).getByRole("button", { name: "Aplicar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ "cores_avatares.cubo": "rgb" }, true);
    // o mapa em uso volta no retrato: "Usar a cor global" aparece e remove a entrada (null herda)
    await userEvent.click(await within(dialogo).findByRole("button", { name: "Usar a cor global" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ "cores_avatares.cubo": null }, true);
    await userEvent.click(within(dialogo).getByRole("radio", { name: "Personalizada" }));
    await userEvent.click(within(dialogo).getByRole("radio", { name: "Padrão" }));
    await userEvent.click(within(dialogo).getByRole("button", { name: "Aplicar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ "cores_avatares.cubo": "default" }, true);
  });

  const palcoFill = (dialogo: HTMLElement) => dialogo.querySelector(".avatar-picker-stage svg path")?.getAttribute("fill")?.toUpperCase();

  it("R4-06: cola um texto longo sem truncar, mantém a última prévia válida e não deixa aplicar", async () => {
    const api = fakeApi();
    const dialogo = await abrir(api);
    const grupo = within(dialogo).getByRole("group", { name: /Cor do corpo: Fantasma/ });
    await userEvent.click(within(grupo).getByRole("radio", { name: "Personalizada" }));
    const campo = within(grupo).getByLabelText("Cor");
    await userEvent.clear(campo);
    await userEvent.type(campo, "#112233");
    await waitFor(() => expect(palcoFill(dialogo)).toBe("#112233"), { timeout: 3000 });
    await userEvent.clear(campo);
    await userEvent.click(campo);
    await userEvent.paste("#1234567"); // sete dígitos: a colagem não é cortada em seis, e é inválida
    expect(campo).toHaveValue("#1234567");
    expect(within(grupo).getByRole("alert")).toBeInTheDocument();
    expect(within(grupo).getByRole("button", { name: "Aplicar" })).toBeDisabled();
    expect(palcoFill(dialogo)).toBe("#112233"); // a prévia fica na última válida do rascunho
    await userEvent.clear(campo);
    expect(palcoFill(dialogo)).toBe("#112233");
    expect(within(grupo).getByRole("button", { name: "Aplicar" })).toBeDisabled();
    await userEvent.paste("#445566");
    expect(within(grupo).queryByRole("alert")).toBeNull();
    expect(within(grupo).getByRole("button", { name: "Aplicar" })).toBeEnabled();
    await waitFor(() => expect(palcoFill(dialogo)).toBe("#445566"));
  });

  it("R4-07: mudança externa confirmada atualiza o gravado, o rascunho sobrevive (inclusive à reabertura) e um retrato velho não vence o update", async () => {
    let empurrar!: (s: Snapshot) => void;
    const api = fakeApi({
      onSnapshot: async (cb) => {
        empurrar = cb;
        return () => {};
      },
    });
    const dialogo = await abrir(api);
    let grupo = within(dialogo).getByRole("group", { name: /Cor do corpo: Fantasma/ });
    expect(within(grupo).queryByRole("button", { name: "Usar a cor global" })).toBeNull(); // nada gravado ainda
    await userEvent.click(within(grupo).getByRole("radio", { name: "Personalizada" }));
    const campo = within(grupo).getByLabelText("Cor");
    await userEvent.clear(campo);
    await userEvent.type(campo, "#ABCDEF"); // o rascunho do usuário
    // outro cliente grava o fantasma em rgb; o ombrod confirma e o próximo retrato publica
    await api.configUpdate({ "cores_avatares.fantasma": "rgb" }, true);
    await act(async () => empurrar(await api.getSnapshot()));
    expect(await within(grupo).findByRole("button", { name: "Usar a cor global" })).toBeInTheDocument(); // o gravado mudou
    expect(within(grupo).getByRole("radio", { name: "Personalizada" })).toBeChecked(); // e o rascunho ficou
    expect(within(grupo).getByLabelText("Cor")).toHaveValue("#ABCDEF");
    // fechar e reabrir: o rascunho de cada id continua
    await userEvent.click(within(dialogo).getByRole("button", { name: "Cancelar" }));
    await userEvent.click(screen.getAllByRole("button", { name: "Escolher…" })[1]);
    const dialogo2 = await screen.findByRole("dialog");
    await userEvent.click(within(dialogo2).getByRole("radio", { name: /^Fantasma/ }));
    grupo = within(dialogo2).getByRole("group", { name: /Cor do corpo: Fantasma/ });
    expect(within(grupo).getByLabelText("Cor")).toHaveValue("#ABCDEF");
    // aplicar grava; um retrato ainda velho (rgb) chegando depois NÃO desfaz a resposta mais nova do update
    await userEvent.click(within(grupo).getByRole("button", { name: "Aplicar" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ "cores_avatares.fantasma": "#ABCDEF" }, true);
    await act(async () => empurrar({ ...(await api.getSnapshot()), providers: { ...(await api.getSnapshot()).providers!, avatar_colors: { fantasma: "rgb" } } }));
    await waitFor(() => expect(within(grupo).getByRole("radio", { name: "Personalizada" })).toBeChecked());
    expect(within(grupo).getByLabelText("Cor")).toHaveValue("#ABCDEF");
    expect(within(grupo).getByRole("radio", { name: "RGB" })).not.toBeChecked();
  });

  it("R8-04: update local rev11 com o refresh pendente e mudança externa rev12 no meio: reconcilia ao terminar, sem reload contínuo", async () => {
    const adiado = <T,>() => {
      let resolver!: (v: T) => void;
      const promessa = new Promise<T>((r) => (resolver = r));
      return { promessa, resolver };
    };
    const configRev = (revision: number, cores: Record<string, string>) => ({ ...mockConfig(), revision, instance: "t1", values: { ...mockConfig().values, cores_avatares: cores } });
    const retrato = (cores: Record<string, string>): Snapshot => {
      const s = mockSnapshot();
      return { ...s, providers: { ...s.providers!, avatar_colors: cores } };
    };
    let atual = configRev(10, {});
    let leituras = 0;
    let armado = false;
    let empurrar!: (s: Snapshot) => void;
    const atualizacao = adiado<ConfigUpdateResult>();
    const refresco = adiado<void>();
    const api = fakeApi({
      configGet: () => {
        leituras++;
        return Promise.resolve(structuredClone(atual));
      },
      configUpdate: () => atualizacao.promessa,
      refresh: () => (armado ? refresco.promessa : Promise.resolve()),
      onSnapshot: async (cb) => {
        empurrar = cb;
        return () => {};
      },
    });
    const dialogo = await abrir(api);
    // um rascunho do Cubo (outro id), que nada disto pode apagar
    await userEvent.click(within(dialogo).getByRole("radio", { name: /^Cubo/ }));
    await userEvent.click(within(within(dialogo).getByRole("group", { name: /Cor do corpo: Cubo/ })).getByRole("radio", { name: "Personalizada" }));
    const campoCubo = within(dialogo).getByLabelText("Cor");
    await userEvent.clear(campoCubo);
    await userEvent.type(campoCubo, "#ABCDEF");
    // a gravação local do Fantasma (rgb): o update fica pendente
    await userEvent.click(within(dialogo).getByRole("radio", { name: /^Fantasma/ }));
    let grupo = within(dialogo).getByRole("group", { name: /Cor do corpo: Fantasma/ });
    await userEvent.click(within(grupo).getByRole("radio", { name: "RGB" }));
    armado = true;
    await userEvent.click(within(grupo).getByRole("button", { name: "Aplicar" }));
    // o update responde rev11; o store lê de novo e o refresh fica pendente
    atual = configRev(11, { fantasma: "rgb" });
    await act(async () =>
      atualizacao.resolver({ applied: ["cores_avatares.fantasma"], restart_required: [], deferred: [], persisted: true, file: null, values: { "cores_avatares.fantasma": "rgb" }, pending: {}, revision: 11, instance: "t1" } as ConfigUpdateResult),
    );
    await waitFor(() => expect(leituras).toBeGreaterThanOrEqual(2)); // a leitura do início e a do fim do update
    const antes = leituras;
    // enquanto o refresh está pendente, outro cliente grava (rev12) e os retratos chegam (repetidos)
    atual = configRev(12, { fantasma: "#123456" });
    await act(async () => empurrar(retrato({ fantasma: "#123456" })));
    await act(async () => empurrar(retrato({ fantasma: "#123456" })));
    expect(leituras).toBe(antes); // ainda pendente: nenhuma leitura no meio da operação local
    // a operação local termina: reconcilia o que chegou nesse meio tempo (config.get versionado manda)
    await act(async () => refresco.resolver());
    await waitFor(() => expect(leituras).toBe(antes + 1));
    grupo = within(dialogo).getByRole("group", { name: /Cor do corpo: Fantasma/ });
    await waitFor(() => expect(within(grupo).getByRole("radio", { name: "Personalizada" })).toBeChecked());
    expect(within(grupo).getByLabelText("Cor")).toHaveValue("#123456"); // o gravado é o do rev12, não o do rev11
    // retratos idênticos depois: nada de leitura contínua
    await act(async () => empurrar(retrato({ fantasma: "#123456" })));
    await act(async () => empurrar(retrato({ fantasma: "#123456" })));
    expect(leituras).toBe(antes + 1);
    // o rascunho do outro id continua
    await userEvent.click(within(dialogo).getByRole("radio", { name: /^Cubo/ }));
    expect(within(within(dialogo).getByRole("group", { name: /Cor do corpo: Cubo/ })).getByLabelText("Cor")).toHaveValue("#ABCDEF");
  });

  it("a cor vale só para o avatar escolhido e aparece nos cartões; os controles legados seguem", async () => {
    const api = fakeApi();
    await api.configUpdate({ "cores_avatares.ombro-cloud": "#112233" }, true);
    renderApp(api, "appearance");
    const cartaoClaude = await screen.findByRole("img", { name: /^Claude Code: Ombro Cloud/ });
    await waitFor(() => expect(cartaoClaude.querySelector("path")!.getAttribute("fill")!.toUpperCase()).toBe("#112233"));
    const orbita = screen.getByRole("img", { name: /^Codex: Órbita/ });
    expect(orbita.querySelector("path")!.getAttribute("fill")!.toUpperCase()).not.toBe("#112233");
    expect(screen.getByRole("radio", { name: "Do agente" })).toBeInTheDocument(); // cor global, como antes
    expect(screen.getByRole("radio", { name: "Fixa" })).toBeInTheDocument();
  });

  it("falha ao gravar mostra o erro e mantém o rascunho", async () => {
    const api = fakeApi();
    const dialogo = await abrir(api);
    (api.configUpdate as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("o ombrod recusou"));
    const grupo = within(dialogo).getByRole("group", { name: /Cor do corpo: Fantasma/ });
    await userEvent.click(within(grupo).getByRole("radio", { name: "RGB" }));
    await userEvent.click(within(grupo).getByRole("button", { name: "Aplicar" }));
    expect(await within(grupo).findByText(/o ombrod recusou/)).toBeInTheDocument();
    expect(within(grupo).getByRole("radio", { name: "RGB" })).toBeChecked();
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

describe("Aparência: o pacote de animações", () => {
  const cartao = async () => (await screen.findByRole("heading", { name: "Animações" })).closest(".card") as HTMLElement;

  it("mostra o em uso, escolher grava pela IPC e, com atividade, explica a espera", async () => {
    const api = fakeApi();
    renderApp(api, "appearance");
    const c = await cartao();
    await waitFor(() => expect(within(c).getByRole("status")).toHaveTextContent("Vivo em uso no app e na telinha."));
    expect(within(c).getByRole("radio", { name: "Vivo" })).toBeChecked();
    expect(within(c).queryByRole("radio", { name: /3D|Terminal/ })).toBeNull(); // em avaliação: não dá para escolher
    expect(c).toHaveTextContent("3D e Terminal ainda estão em avaliação.");
    expect(c.querySelector(".animation-preview .avatar-palco")).not.toBeNull();
    await userEvent.click(within(c).getByRole("radio", { name: "Clássico" }));
    expect(api.configUpdate).toHaveBeenCalledWith({ pacote_animacao: "classico" }, true);
    await waitFor(() => expect(within(c).getByRole("radio", { name: "Clássico" })).toBeChecked());
    // na demonstração o Claude e o Codex estão esperando você: a troca fica pendente
    expect(within(c).getByRole("status")).toHaveTextContent(
      "O Clássico entra quando a atividade terminar (Claude Code e Codex: trabalhando ou esperando você). Até lá, segue o Vivo.",
    );
  });

  it("sem atividade, a escolha entra na hora", async () => {
    const base = createMockApi();
    const api = fakeApi({
      configUpdate: async (c, p) => ({ ...(await base.configUpdate(c, p)), animation: mockAnimation("classico") }),
    });
    renderApp(api, "appearance");
    const c = await cartao();
    await waitFor(() => expect(within(c).getByRole("radio", { name: "Clássico" })).toBeEnabled());
    await userEvent.click(within(c).getByRole("radio", { name: "Clássico" }));
    await waitFor(() => expect(within(c).getByRole("status")).toHaveTextContent("Clássico em uso no app e na telinha."));
  });

  it("um pacote que não carregou: diz e segue o em uso", async () => {
    const base = createMockApi();
    const api = fakeApi({
      configUpdate: async (c, p) => ({ ...(await base.configUpdate(c, p)), animation: { ...mockAnimation("classico", "vivo"), pending: false, failed: "classico" } }),
    });
    renderApp(api, "appearance");
    const c = await cartao();
    await waitFor(() => expect(within(c).getByRole("radio", { name: "Clássico" })).toBeEnabled());
    await userEvent.click(within(c).getByRole("radio", { name: "Clássico" }));
    await waitFor(() => expect(within(c).getByRole("status")).toHaveTextContent("Não consegui carregar o Clássico; segue o Vivo."));
  });

  it("com um ombrod de antes desta opção: fica no Vivo, desligado, e explica", async () => {
    const s = mockSnapshot();
    delete s.providers!.animation;
    const cfg = mockConfig();
    delete cfg.animation;
    delete cfg.values.pacote_animacao;
    const schema = await createMockApi().configSchema();
    const velho: ConfigSchema = { ...schema, options: schema.options.filter((o) => o.key !== "pacote_animacao") };
    const api = fakeApi({ getSnapshot: () => Promise.resolve(s), onSnapshot: async () => () => {}, configGet: () => Promise.resolve(cfg), configSchema: () => Promise.resolve(velho) });
    renderApp(api, "appearance");
    const c = await cartao();
    await waitFor(() => expect(within(c).getByRole("status")).toHaveTextContent("O serviço do Ombro em uso ainda não tem esta opção"));
    const vivo = within(c).getByRole("radio", { name: "Vivo" });
    expect(vivo).toBeChecked();
    expect(vivo).toBeDisabled();
    expect(api.configUpdate).not.toHaveBeenCalled();
  });
});
