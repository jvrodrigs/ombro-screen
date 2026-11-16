import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeApi, renderApp } from "../../test/render";
import { createMockApi } from "../../services/mock";
import type { ConfigUpdateResult } from "../../types/ombro";

/** Uma promessa que o teste resolve quando quiser: para pôr as respostas na ordem que o teste precisa. */
function adiada<T>() {
  let resolver!: (v: T) => void;
  let rejeitar!: (e: unknown) => void;
  const promessa = new Promise<T>((res, rej) => {
    resolver = res;
    rejeitar = rej;
  });
  return { promessa, resolver, rejeitar };
}

const radio = (nome: RegExp) => screen.findByRole("radio", { name: nome });
const girar = () => screen.getByRole("switch", { name: "Girar 180°" });
const deitado = () => screen.getByRole("switch", { name: "Layout clássico deitado" });
/** O tamanho do quadro na linha "Resolução" do estado da tela. */
const resolucao = () => screen.getByText("Resolução").parentElement!.textContent;

describe("layouts", () => {
  it("todos os layouts do esquema aparecem e cada um vira o marcado quando o ombrod confirma", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    for (const [nome, id] of [[/Minimal v2/, "minimal-v2"], [/Clássico/, "classico"], [/Multiagente/, "multiagente"], [/^Ombro/, "ombro"]] as const) {
      const r = await radio(nome);
      await userEvent.click(r);
      await waitFor(() => expect(r).toHaveAttribute("aria-checked", "true"));
      expect(api.configUpdate).toHaveBeenLastCalledWith({ layout: id }, true);
    }
    expect(within(screen.getByRole("radiogroup", { name: "Layout da tela" })).getAllByRole("radio", { checked: true })).toHaveLength(1);
  });

  it("clássico -> Minimal v2 -> clássico, sem pedir reinício", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    const classico = await radio(/Clássico/);
    const minimal = await radio(/Minimal v2/);
    for (const r of [classico, minimal, classico]) {
      await userEvent.click(r);
      await waitFor(() => expect(r).toHaveAttribute("aria-checked", "true"));
    }
    expect(screen.queryByText(/vale depois de reiniciar/)).toBeNull();
    expect(api.daemonRestart).not.toHaveBeenCalled();
    await waitFor(() => expect(resolucao()).toContain("320×480")); // o clássico em pé, no estado da tela
  });

  it("cliques seguidos saem um de cada vez, na ordem, e o último é o que fica", async () => {
    const pedidos: { layout: unknown; r: ReturnType<typeof adiada<ConfigUpdateResult>> }[] = [];
    const base = createMockApi();
    const api = fakeApi({
      configUpdate: (changes, persist) => {
        const r = adiada<ConfigUpdateResult>();
        pedidos.push({ layout: changes.layout, r });
        return r.promessa.then(() => base.configUpdate(changes, persist));
      },
    });
    renderApp(api, "display");
    const [minimal, classico, multi] = [await radio(/Minimal v2/), await radio(/Clássico/), await radio(/Multiagente/)];
    await userEvent.click(minimal);
    await userEvent.click(classico);
    await userEvent.click(multi);
    expect(pedidos.map((p) => p.layout)).toEqual(["minimal-v2"]); // os outros esperam a vez
    expect(multi).toHaveAttribute("aria-busy", "true");
    for (let i = 0; i < 3; i++) {
      await waitFor(() => expect(pedidos.length).toBeGreaterThan(i));
      await act(async () => pedidos[i].r.resolver(undefined as never));
    }
    expect(pedidos.map((p) => p.layout)).toEqual(["minimal-v2", "classico", "multiagente"]);
    await waitFor(() => expect(multi).toHaveAttribute("aria-checked", "true"));
    expect(minimal).toHaveAttribute("aria-checked", "false");
  });

});

describe("girar 180°", () => {
  it("liga e desliga na hora, e o switch mostra o que o ombrod confirmou", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    await radio(/Minimal v2/);
    expect(girar()).not.toBeChecked();
    await userEvent.click(girar());
    expect(api.configUpdate).toHaveBeenLastCalledWith({ girar: true }, true);
    await waitFor(() => expect(girar()).toBeChecked());
    await userEvent.click(girar());
    expect(api.configUpdate).toHaveBeenLastCalledWith({ girar: false }, true);
    await waitFor(() => expect(girar()).not.toBeChecked()); // desligou: não fica verde
    expect(screen.queryByText(/vale depois de reiniciar/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Salvar" })).toBeDisabled(); // nada "por salvar": já está valendo
  });

  it("o switch só muda com a resposta: enquanto espera, mostra o valor em uso", async () => {
    const r = adiada<void>();
    const base = createMockApi();
    const api = fakeApi({ configUpdate: (c, p) => r.promessa.then(() => base.configUpdate(c, p)) });
    renderApp(api, "display");
    await radio(/Minimal v2/);
    await userEvent.click(girar());
    expect(girar()).not.toBeChecked(); // ainda não confirmado
    expect(screen.getByLabelText("Salvando")).toBeInTheDocument();
    await act(async () => r.resolver());
    await waitFor(() => expect(girar()).toBeChecked());
  });

  it("falha da IPC: aparece o erro, o switch fica como estava, sem sucesso falso", async () => {
    const api = fakeApi({ configUpdate: () => Promise.reject({ kind: "ipc", message: "o ombrod não respondeu" }) });
    renderApp(api, "display");
    await radio(/Minimal v2/);
    await userEvent.click(girar());
    expect(await screen.findByText(/o ombrod não respondeu/)).toBeInTheDocument();
    expect(girar()).not.toBeChecked();
    expect(screen.queryByText(/Gravado/)).toBeNull();
  });

  it("depois de sair e voltar à página, o switch é o do ombrod", async () => {
    const api = fakeApi();
    const { unmount } = renderApp(api, "display");
    await radio(/Minimal v2/);
    await userEvent.click(girar());
    await waitFor(() => expect(girar()).toBeChecked());
    unmount(); // fechou a janela
    renderApp(api, "display");
    await radio(/Minimal v2/);
    await waitFor(() => expect(girar()).toBeChecked());
    await userEvent.click(girar());
    await waitFor(() => expect(girar()).not.toBeChecked());
    unmount();
  });
});

describe("clássico deitado", () => {
  it("só fica disponível no layout clássico, e diz por quê", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    await radio(/Minimal v2/);
    expect(deitado()).toBeDisabled();
    expect(screen.getByText(/Só o layout clássico tem os dois modos/)).toBeInTheDocument();
    await userEvent.click(await radio(/Clássico/));
    await waitFor(() => expect(deitado()).toBeEnabled());
    await waitFor(() => expect(resolucao()).toContain("320×480"));
    await userEvent.click(deitado());
    expect(api.configUpdate).toHaveBeenLastCalledWith({ paisagem: true }, true);
    await waitFor(() => expect(deitado()).toBeChecked());
    await waitFor(() => expect(resolucao()).toContain("480×320"));
  });
});

describe("o que só vale ao reiniciar", () => {
  it("uma releitura das preferências não apaga o que está sendo editado", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    await radio(/Minimal v2/);
    await userEvent.click(screen.getByRole("switch", { name: "Usar a tela USB" }));
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
    await userEvent.click(girar()); // outra mudança (ao vivo) relê as preferências
    await waitFor(() => expect(girar()).toBeChecked());
    expect(screen.getByRole("switch", { name: "Usar a tela USB" })).toBeChecked();
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
  });

  it("gravado fica pendente até reiniciar; depois de reiniciar, está em uso e o aviso some", async () => {
    const api = fakeApi();
    renderApp(api, "display");
    await radio(/Minimal v2/);
    await userEvent.click(screen.getByRole("switch", { name: "Usar a tela USB" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvar" }));
    const reiniciar = await screen.findByRole("button", { name: "Reiniciar agora" });
    expect(screen.getByText(/usar a tela USB\. A tela pisca/)).toBeInTheDocument();
    await userEvent.click(reiniciar);
    await waitFor(() => expect(screen.queryByRole("button", { name: "Reiniciar agora" })).toBeNull());
    expect(screen.getByRole("switch", { name: "Usar a tela USB" })).toBeChecked();
    expect(screen.getByText("Sem alterações")).toBeInTheDocument();
  });

  it("falha ao gravar mantém o que foi editado e não diz que gravou", async () => {
    const api = fakeApi({ configUpdate: () => Promise.reject({ kind: "rejected", message: "não consegui gravar o config.json" }) });
    renderApp(api, "display");
    await radio(/Minimal v2/);
    await userEvent.click(screen.getByRole("switch", { name: "Usar a tela USB" }));
    await userEvent.click(screen.getByRole("button", { name: "Salvar" }));
    expect(await screen.findByText(/não consegui gravar/)).toBeInTheDocument();
    expect(screen.getByText("Alterações não salvas")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reiniciar agora" })).toBeNull();
  });
});
