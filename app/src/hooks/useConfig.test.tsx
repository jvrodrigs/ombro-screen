import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OmbroProvider } from "./useOmbro";
import { useConfig, type ConfigStore } from "./useConfig";
import { setApiForTests } from "../services/api";
import { createMockApi } from "../services/mock";
import { fakeApi } from "../test/render";
import type { ConfigInfo, ConfigUpdateResult } from "../types/ombro";

function adiada<T>() {
  let resolver!: (v: T) => void;
  const promessa = new Promise<T>((res) => {
    resolver = res;
  });
  return { promessa, resolver };
}

/** Monta o hook e deixa o teste chamá-lo. */
function montar(api: ReturnType<typeof fakeApi>) {
  const ref: { cfg: ConfigStore | null } = { cfg: null };
  function Sonda() {
    ref.cfg = useConfig();
    return <output>{JSON.stringify({ layout: ref.cfg.values.layout, girar: ref.cfg.values.girar, pending: ref.cfg.pending })}</output>;
  }
  setApiForTests(api);
  render(
    <OmbroProvider>
      <Sonda />
    </OmbroProvider>,
  );
  const visto = () => JSON.parse(screen.getByRole("status").textContent ?? "{}");
  return { cfg: () => ref.cfg!, visto };
}

describe("useConfig: o que aparece é o que o ombrod confirmou", () => {
  it("uma leitura pedida antes de uma mudança e respondida depois é descartada", async () => {
    const base = createMockApi();
    const velha = adiada<ConfigInfo>();
    let leituras = 0;
    const api = fakeApi({ configGet: () => (++leituras === 2 ? velha.promessa : base.configGet()), configUpdate: base.configUpdate });
    const { cfg, visto } = montar(api);
    await waitFor(() => expect(visto().layout).toBe("auto"));
    const retratoVelho = await base.configGet(); // revisão 0: layout "auto", girar desligado

    act(() => void cfg().reload()); // a leitura sai... e demora
    await act(async () => void (await cfg().update({ layout: "minimal-v2", girar: true }))); // a mudança responde antes
    expect(visto()).toMatchObject({ layout: "minimal-v2", girar: true });

    await act(async () => velha.resolver(retratoVelho)); // agora chega a resposta velha
    expect(visto()).toMatchObject({ layout: "minimal-v2", girar: true }); // e não desfaz nada
  });

  it("respostas de mudanças fora de ordem: fica a de revisão maior", async () => {
    const base = createMockApi();
    const respostas: ConfigUpdateResult[] = [];
    const api = fakeApi({
      // o ombrod atendeu as duas na ordem; o teste entrega as respostas ao contrário
      configUpdate: async (c, p) => {
        respostas.push(await base.configUpdate(c, p));
        return respostas[respostas.length - 1];
      },
    });
    const { cfg, visto } = montar(api);
    await waitFor(() => expect(visto().layout).toBe("auto"));
    await act(async () => void (await cfg().update({ layout: "classico" })));
    await act(async () => void (await cfg().update({ layout: "multiagente" })));
    expect(respostas.map((r) => r.revision)).toEqual([1, 2]);
    expect(visto().layout).toBe("multiagente");
  });

  it("o que só vale ao reiniciar fica em pending, e values continua o que está em uso", async () => {
    const { cfg, visto } = montar(fakeApi());
    await waitFor(() => expect(visto().layout).toBe("auto"));
    let r!: ConfigUpdateResult;
    await act(async () => void (r = await cfg().update({ usb: true, girar: true })));
    expect(r.applied).toEqual(["girar"]);
    expect(r.restart_required).toEqual(["usb"]);
    expect(visto()).toMatchObject({ girar: true, pending: { usb: true } });
    expect(cfg().values.usb).toBe(false);
  });

  it("depois que o ombrod reinicia, as revisões recomeçam e a leitura nova vale", async () => {
    const api = fakeApi();
    const { cfg, visto } = montar(api);
    await waitFor(() => expect(visto().layout).toBe("auto"));
    await act(async () => void (await cfg().update({ usb: true })));
    await act(async () => void (await cfg().update({ layout: "minimal-v2" }))); // revisão 2
    await act(async () => void (await api.daemonRestart())); // outro ombrod: revisão 0 de novo
    await act(async () => void (await cfg().reload()));
    expect(visto().pending).toEqual({}); // o que estava gravado entrou em uso
    expect(cfg().values.usb).toBe(true);
  });

  it("uma mudança recusada rejeita e não muda nada", async () => {
    const api = fakeApi({ configUpdate: () => Promise.reject({ kind: "rejected", message: "layout deve ser um destes" }) });
    const { cfg, visto } = montar(api);
    await waitFor(() => expect(visto().layout).toBe("auto"));
    let erro: unknown;
    await act(async () => void (await cfg().update({ layout: "nao-existe" }).catch((e) => (erro = e))));
    expect(erro).toMatchObject({ message: "layout deve ser um destes" });
    expect(visto().layout).toBe("auto");
    expect(cfg().busy("layout")).toBe(false);
  });
});
