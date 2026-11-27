// @vitest-environment node
// O plugin de servidor do OpenCode (integrations/opencode/) com um ctx falso no formato do 2.4.2: carga e descarga,
// várias instâncias, foto antes da fila, perda do fluxo, versão sem suporte e privacidade. Pasta temporária.
// Fica fora de src/ de propósito: roda em Node e não entra na checagem de tipos do app.
import { closeSync, openSync, readFileSync } from "node:fs";
import { mkdtemp, readdir, readFile, rename, rm, utimes, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import plugin from "../../integrations/opencode/server.js";

const ORIGEM = "oc-0123456789abcdef";
const SEGREDO = "TEXTO-QUE-NAO-PODE-SAIR";

/** Um fluxo de eventos controlado pelo teste. */
function fluxo() {
  const fila: unknown[] = [];
  let esperando: ((r: IteratorResult<unknown>) => void) | null = null;
  let acabou = false;
  const entregar = (r: IteratorResult<unknown>) => {
    const e = esperando;
    esperando = null;
    e?.(r);
  };
  return {
    emitir(e: unknown) {
      if (esperando) entregar({ value: e, done: false });
      else fila.push(e);
    },
    encerrar() {
      acabou = true;
      entregar({ value: undefined, done: true });
    },
    [Symbol.asyncIterator]() {
      return {
        next: () =>
          new Promise<IteratorResult<unknown>>((ok) => {
            if (fila.length) ok({ value: fila.shift(), done: false });
            else if (acabou) ok({ value: undefined, done: true });
            else esperando = ok;
          }),
        return: async () => {
          acabou = true;
          entregar({ value: undefined, done: true });
          return { value: undefined, done: true as const };
        },
      };
    },
  };
}

interface Sessao {
  id: string;
  location: { directory: string };
  time: { created: number; updated: number; busy?: number; idle?: number; archived?: number };
  title: string;
}

const sessao = (id: string, dir: string, extra: Partial<Sessao["time"]> = {}): Sessao => ({
  id,
  location: { directory: dir },
  time: { created: Date.now() - 1000, updated: Date.now() - 500, ...extra },
  title: SEGREDO,
});

let pasta = "";
const parar: (() => unknown)[] = [];
const banco = () => new Map<string, unknown>();

function ctx(opts: {
  dir?: string;
  versao?: string;
  sessoes?: Sessao[];
  storage?: Map<string, unknown>;
  pendentes?: Record<string, string[]>;
  origem?: string | null;
  semLista?: boolean;
  porPagina?: number;
  falharPagina?: number;
  aoListar?: () => void;
  ausentes?: string[];
  getQuebrado?: string[];
  heartbeatMs?: number;
} = {}) {
  const fluxos: ReturnType<typeof fluxo>[] = [];
  const storage = opts.storage ?? banco();
  const dir = opts.dir ?? "/projetos/api";
  const usados: string[] = [];
  const todas = opts.sessoes ?? [];
  let paginas = 0;
  const alvo = {
    app: { version: opts.versao ?? "2.4.2", channel: "latest" },
    location: { directory: dir },
    options: { dir: pasta, ...(opts.heartbeatMs === undefined ? {} : { heartbeatMs: opts.heartbeatMs }), ...(opts.origem === null ? {} : { origin: opts.origem ?? ORIGEM }) },
    event: {
      subscribe: () => {
        const f = fluxo();
        fluxos.push(f);
        return f;
      },
    },
    session: {
      get: async ({ sessionID }: { sessionID: string }) => {
        if (opts.ausentes?.includes(sessionID)) throw Object.assign(new Error(SEGREDO), { _tag: "Session.NotFoundError" });
        if (opts.getQuebrado?.includes(sessionID)) throw new Error("Session.NotFoundError no texto não é prova");
        const s = todas.find((x) => x.id === sessionID) ?? sessao(sessionID, dir);
        return s;
      },
      ...(opts.semLista
        ? {}
        : {
            list: async ({ cursor }: { cursor?: string }) => {
              opts.aoListar?.();
              if (++paginas === opts.falharPagina) throw new Error(SEGREDO);
              const de = Number(cursor ?? 0);
              const ate = de + (opts.porPagina ?? 100);
              return { data: todas.slice(de, ate), cursor: { next: ate < todas.length ? String(ate) : undefined } };
            },
          }),
      context: async () => usados.push("context"),
      prompt: async () => usados.push("prompt"),
    },
    permission: {
      list: async ({ sessionID }: { sessionID: string }) => {
        const s = todas.find((x) => x.id === sessionID);
        if (s?.location.directory !== dir) return [];
        return (opts.pendentes?.[sessionID] ?? []).map((id) => ({ id, action: SEGREDO, resources: [SEGREDO] }));
      },
      reply: async () => usados.push("reply"),
    },
    storage: { get: async (k: string) => storage.get(k), set: async (k: string, v: unknown) => void storage.set(k, v) },
  };
  return { alvo, fluxos, usados, storage };
}

async function ligar(c: ReturnType<typeof ctx>) {
  const fim = (await plugin.setup(c.alvo)) as () => Promise<void>;
  parar.push(fim);
  return fim;
}

const ler = async (p: string) => JSON.parse(await readFile(p, "utf8"));
async function eventos() {
  const nomes = (await readdir(join(pasta, "eventos")).catch(() => [] as string[])).filter((n) => !n.startsWith(".")).sort();
  return Promise.all(nomes.map((n) => ler(join(pasta, "eventos", n))));
}
async function pulsos() {
  const nomes = (await readdir(join(pasta, "opencode", "live")).catch(() => [] as string[])).filter((n) => !n.startsWith("."));
  return Promise.all(nomes.map((n) => ler(join(pasta, "opencode", "live", n))));
}
async function ate<T>(fn: () => Promise<T | false | undefined>, ms = 3000): Promise<T> {
  const fim = Date.now() + ms;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > fim) throw new Error(`não aconteceu: ${JSON.stringify([await pulsos(), await eventos()])}`);
    await new Promise((r) => setTimeout(r, 10));
  }
}
const pronto = () => ate(async () => (await pulsos())[0]?.state === "ready" && (await pulsos())[0]);
const doTipo = (tipo: string, n = 1) => ate(async () => {
  const achados = (await eventos()).filter((e) => e.event === tipo);
  return achados.length >= n && achados;
});
const ev = (type: string, id: string, data: object, seq?: number) => ({ type, id, created: Date.now(), data, ...(seq === undefined ? {} : { durable: { seq } }) });

// o "banco" deste processo de teste: o plugin só confia no caminho se um descritor aberto aqui for o mesmo arquivo
let descritor: number | null = null;
const TESTE = Symbol.for("ombro.opencode.teste");
const bancoNoDisco = () => join(pasta, "dados", "opencode", "opencode.db");
const fechar = () => {
  if (descritor !== null) closeSync(descritor);
  descritor = null;
};

beforeEach(async () => {
  pasta = await mkdtemp(join(tmpdir(), "ombro-oc-plugin-"));
  await mkdir(join(pasta, "dados", "opencode"), { recursive: true });
  descritor = openSync(bancoNoDisco(), "w+");
  process.env.XDG_DATA_HOME = join(pasta, "dados");
  delete process.env.OPENCODE_DB;
  (globalThis as Record<symbol, unknown>)[TESTE] = { pulsoMs: 20 };
});
afterEach(async () => {
  for (const f of parar.splice(0)) await f();
  fechar();
  delete (globalThis as Record<symbol, unknown>)[TESTE];
  delete process.env.XDG_DATA_HOME;
  delete process.env.OPENCODE_DB;
  await rm(pasta, { recursive: true, force: true });
});

describe("plugin do OpenCode", () => {
  it("carrega, pulsa pronto com a marca do banco e descarrega parado", async () => {
    const c = ctx();
    const fim = await ligar(c);
    const p = await pronto();
    expect(p).toMatchObject({ v: 1, origin: ORIGEM, pid: process.pid, version: "2.4.2", partial: false });
    expect(p.ns).toBe(c.storage.get("ombro.banco"));
    await fim();
    expect((await pulsos())[0].state).toBe("stopped");
    c.fluxos[0].emitir(ev("session.execution.started", "evt_depois", { sessionID: "ses_a" }));
    await new Promise((r) => setTimeout(r, 60));
    expect((await eventos()).filter((e) => e.event.startsWith("session."))).toHaveLength(0);
  });

  it("descarregar no meio de um pulso deixa 'parado' como o último", async () => {
    const storage = banco();
    for (let i = 0; i < 60; i++) {
      const fim = await ligar(ctx({ storage }));
      await new Promise((r) => setTimeout(r, i % 6)); // a primeira leitura ainda está avisando
      await fim();
      await new Promise((r) => setTimeout(r, 4)); // um aviso atrasado teria chegado
      expect((await pulsos())[0].state, `volta ${i}`).toBe("stopped");
    }
  });

  it("carregar de novo muda o boot e mantém a marca do banco", async () => {
    const storage = banco();
    const fim = await ligar(ctx({ storage }));
    const antes = await pronto();
    await fim();
    await ligar(ctx({ storage }));
    const depois = await ate(async () => {
      const p = (await pulsos())[0];
      return p.state === "ready" && p.boot !== antes.boot && p;
    });
    expect(depois.ns).toBe(antes.ns);
  });

  it("duas instâncias (dois projetos) gravam o mesmo evento uma vez e dividem o boot", async () => {
    const storage = banco();
    const a = ctx({ storage, dir: "/projetos/api" });
    const b = ctx({ storage, dir: "/projetos/site" });
    const fimA = await ligar(a);
    await ligar(b);
    await pronto();
    const e = ev("session.execution.started", "evt_1", { sessionID: "ses_a" }, 2);
    a.fluxos[0].emitir(e);
    b.fluxos[0].emitir(e);
    b.fluxos[0].emitir(ev("session.execution.succeeded", "evt_2", { sessionID: "ses_b" }, 3));
    await doTipo("session.execution.succeeded");
    const gravados = await eventos();
    expect(gravados.filter((x) => x.x.id === "evt_1")).toHaveLength(1);
    expect(gravados.find((x) => x.x.id === "evt_1")).toMatchObject({ session: `${ORIGEM}:ses_a`, provider: "opencode", x: { origin: ORIGEM, seq: 2 } });
    expect(new Set(gravados.map((x) => x.x.boot)).size).toBe(1);
    await fimA();
    expect((await pulsos())[0].state).not.toBe("stopped"); // a outra instância segue viva
  });

  it("fotografa as sessões recentes em todas as páginas; permissão só do próprio projeto", async () => {
    const velha = Date.now() - 6 * 3_600_000;
    const c = ctx({
      porPagina: 2,
      sessoes: [
        sessao("ses_minha", "/projetos/api", { busy: 1_000, idle: 2_000 }),
        sessao("ses_outra", "/projetos/site"),
        sessao("ses_velha", "/projetos/api", { updated: velha, created: velha }),
        sessao("ses_arquivada", "/projetos/api", { archived: Date.now() }),
        sessao("ses_quinta", "/projetos/api"),
      ],
      pendentes: { ses_minha: ["per_1"], ses_outra: ["per_2"] },
    });
    await ligar(c);
    await doTipo("ombro.snapshot", 3);
    await pronto();
    const fotos = (await eventos()).filter((e) => e.event === "ombro.snapshot");
    expect(fotos.map((f) => f.session.split(":")[1]).sort()).toEqual(["ses_minha", "ses_outra", "ses_quinta"]);
    const de = (id: string) => fotos.find((f) => f.session.endsWith(id));
    expect(de("ses_minha")).toMatchObject({ project: "api", x: { snap: { busy: 1000, idle: 2000, pending: ["per_1"] } } });
    expect(de("ses_outra").x.snap.pending).toEqual([]); // de outro projeto: não se pergunta
    expect(c.usados).toEqual([]);
  });

  it("página que falha deixa a foto parcial, sem apagar nada", async () => {
    await ligar(ctx({ porPagina: 1, falharPagina: 2, sessoes: [sessao("ses_a", "/projetos/api"), sessao("ses_b", "/projetos/api")] }));
    await ate(async () => (await pulsos())[0]?.partial === true);
    const e = await eventos();
    expect(e.map((x) => x.event)).toEqual(["ombro.snapshot"]);
  });

  it("assina antes de fotografar: o que chega durante a foto vem depois dela", async () => {
    let disparou = false;
    const c: ReturnType<typeof ctx> = ctx({
      sessoes: [sessao("ses_a", "/projetos/api")],
      aoListar: () => {
        if (disparou) return;
        disparou = true;
        expect(c.fluxos).toHaveLength(1); // a assinatura já existe quando a foto começa
        c.fluxos[0].emitir(ev("session.execution.started", "evt_meio", { sessionID: "ses_a" }));
      },
    });
    await ligar(c);
    await doTipo("session.execution.started");
    expect((await eventos()).map((e) => e.event)).toEqual(["ombro.snapshot", "session.execution.started"]);
  });

  it("fluxo que cai: avisa a lacuna, assina de novo e fotografa", async () => {
    const c = ctx({ sessoes: [sessao("ses_a", "/projetos/api")] });
    await ligar(c);
    await pronto();
    c.fluxos[0].encerrar();
    await doTipo("ombro.gap");
    await doTipo("ombro.snapshot", 2);
    expect(c.fluxos.length).toBeGreaterThan(1);
    const nomes = (await eventos()).map((e) => e.event);
    expect(nomes.indexOf("ombro.gap")).toBeLessThan(nomes.lastIndexOf("ombro.snapshot"));
  });

  it("o pedido de foto do Ombro (reinício) é atendido sem consultar a todo instante", async () => {
    let listas = 0;
    await ligar(ctx({ sessoes: [sessao("ses_a", "/projetos/api")], aoListar: () => listas++ }));
    await doTipo("ombro.snapshot");
    await new Promise((r) => setTimeout(r, 120));
    expect(listas).toBe(1);
    await mkdir(join(pasta, "opencode"), { recursive: true });
    await writeFile(join(pasta, "opencode", "resync"), "");
    const t = new Date(Date.now() + 5000);
    await utimes(join(pasta, "opencode", "resync"), t, t);
    await doTipo("ombro.snapshot", 2);
    await new Promise((r) => setTimeout(r, 120));
    expect(listas).toBe(2);
  });

  it("permissão e pergunta levam só o id do pedido; nada de texto", async () => {
    const c = ctx();
    await ligar(c);
    await pronto();
    const f = c.fluxos[0];
    f.emitir(ev("permission.asked", "evt_1", { sessionID: "ses_a", id: "per_1", action: SEGREDO, resources: [SEGREDO] }));
    f.emitir(ev("permission.replied", "evt_2", { sessionID: "ses_a", requestID: "per_1", reply: SEGREDO }));
    f.emitir(ev("form.created", "evt_3", { form: { id: "frm_1", sessionID: "ses_a", title: SEGREDO, fields: [SEGREDO] } }));
    f.emitir(ev("form.replied", "evt_4", { id: "frm_1", sessionID: "ses_a", answer: { k: SEGREDO } }));
    f.emitir(ev("session.text.delta", "evt_5", { sessionID: "ses_a", delta: SEGREDO }));
    f.emitir(ev("session.created", "evt_6", { sessionID: "ses_b", title: SEGREDO, location: { directory: `/u/${SEGREDO}/mobile` } }));
    await doTipo("session.created");
    const gravados = await eventos();
    expect(gravados.filter((e) => e.x.req).map((e) => [e.event, e.x.req])).toEqual([
      ["permission.asked", "per_1"], ["permission.replied", "per_1"], ["form.created", "frm_1"], ["form.replied", "frm_1"],
    ]);
    expect(gravados.map((e) => e.event)).not.toContain("session.text.delta");
    expect(gravados.at(-1).project).toBe("mobile");
    expect(JSON.stringify([gravados, await pulsos()])).not.toContain(SEGREDO);
    expect(c.usados).toEqual([]);
  });

  it.each([
    ["2.0.17 (sem session.list)", { versao: "2.0.17", semLista: true }],
    ["2.1.0", { versao: "2.1.0" }],
    ["2.4.1", { versao: "2.4.1" }],
    ["2.4.2 sem a capacidade", { versao: "2.4.2", semLista: true }],
    ["versão ilegível", { versao: "local" }],
  ])("recusa %s dizendo o porquê, sem assinar nem gravar eventos", async (_nome, opts) => {
    const c = ctx({ ...opts, sessoes: [sessao("ses_a", "/projetos/api")] });
    await ligar(c);
    await ate(async () => (await pulsos())[0]?.state === "incompatible");
    expect(c.fluxos).toHaveLength(0);
    expect(await eventos()).toEqual([]);
    expect(c.storage.size).toBe(0);
  });

  it.each(["2.4.2", "2.4.10", "2.5.0", "3.0.0"])("aceita %s", async (versao) => {
    await ligar(ctx({ versao }));
    await pronto();
  });

  it("sem origem (ou com uma inválida) não coleta", async () => {
    for (const origem of [null, "curta", "COM-MAIUSCULA-123"]) {
      const c = ctx({ origem });
      const fim = await ligar(c);
      await ate(async () => (await pulsos()).some((p) => p.state === "no_origin"));
      expect(c.fluxos).toHaveLength(0);
      await fim();
    }
    expect(await eventos()).toEqual([]);
  });

  describe("a âncora da instância", () => {
    const recusado = async () => {
      const c = ctx({ sessoes: [sessao("ses_a", "/projetos/api")] });
      await ligar(c);
      await ate(async () => (await pulsos())[0]?.state === "unverified");
      expect(c.fluxos).toHaveLength(0);
      expect(await eventos()).toEqual([]);
      expect(c.storage.size).toBe(0);
    };

    it("vai no pulso e em cada evento, não muda ao recarregar e muda se o arquivo for outro", async () => {
      const storage = banco();
      const c = ctx({ storage });
      const fim = await ligar(c);
      const p = await pronto();
      expect(p.anchor).toMatch(/^[0-9a-f]{32}$/);
      c.fluxos[0].emitir(ev("session.execution.started", "evt_1", { sessionID: "ses_a" }, 2));
      const [e] = await doTipo("session.execution.started");
      expect(e.x).toMatchObject({ anc: p.anchor, ns: p.ns });
      await fim();
      await ligar(ctx({ storage }));
      expect((await ate(async () => (await pulsos())[0].state === "ready" && (await pulsos())[0])).anchor).toBe(p.anchor);
      for (const f of parar.splice(0)) await f();
      // uma cópia: o mesmo conteúdo (e a mesma marca) em outro arquivo
      await rename(bancoNoDisco(), `${bancoNoDisco()}.antigo`);
      fechar();
      descritor = openSync(bancoNoDisco(), "w+");
      await ligar(ctx({ storage }));
      const copia = await ate(async () => (await pulsos())[0].state === "ready" && (await pulsos())[0]);
      expect(copia.ns).toBe(p.ns);
      expect(copia.anchor).not.toBe(p.anchor);
    });

    it("sem o arquivo aberto por este processo, não coleta", async () => {
      fechar();
      await recusado();
    });

    it("um caminho que não é o aberto (outro arquivo em OPENCODE_DB) não vale", async () => {
      await writeFile(join(pasta, "outro.db"), "x");
      process.env.OPENCODE_DB = join(pasta, "outro.db");
      await recusado();
    });

    it.each([":memory:", "/nao/existe.db"])("banco %s não se comprova", async (caminho) => {
      process.env.OPENCODE_DB = caminho;
      await recusado();
    });

    it("arquivo trocado com o plugin rodando: para de gravar eventos", async () => {
      const c = ctx();
      await ligar(c);
      await pronto();
      await rename(bancoNoDisco(), `${bancoNoDisco()}.antigo`);
      await writeFile(bancoNoDisco(), "outro");
      await ate(async () => (await pulsos())[0].state === "unverified");
      c.fluxos[0].emitir(ev("session.execution.started", "evt_depois", { sessionID: "ses_a" }, 2));
      await new Promise((r) => setTimeout(r, 120));
      expect((await eventos()).filter((e) => e.event === "session.execution.started")).toEqual([]);
    });

    it("só lê metadados: nunca abre, lê ou fecha o arquivo do banco", () => {
      const fonte = readFileSync(new URL("../../integrations/opencode/server.js", import.meta.url), "utf8");
      expect(fonte.match(/import \{([^}]*)\} from "node:fs";/)?.[1].split(",").map((s) => s.trim()).sort()).toEqual(["fstatSync", "statSync"]);
      expect(fonte).not.toMatch(/\bopenSync\b|\breadFileSync\b|\bcloseSync\b|createReadStream|\bopen\(|bun:sqlite|node:sqlite/);
      expect(fonte.match(/from "node:fs\/promises";/g)).toHaveLength(1);
      expect(fonte).not.toMatch(/readFile\(.*banco|copyFile/);
    });
  });

  it("o horário é o da fonte ou nenhum: nunca o relógio local no lugar dele", async () => {
    const c = ctx();
    await ligar(c);
    await pronto();
    const antes = Date.now();
    const bom = antes - 600_000;
    const casos: [string, unknown][] = [["evt_ok", bom], ["evt_sem", undefined], ["evt_txt", "ontem"], ["evt_neg", -1], ["evt_fut", antes + 3_600_000], ["evt_zero", 0]];
    for (const [i, [eid, created]] of casos.entries())
      c.fluxos[0].emitir({ type: "session.execution.started", id: eid, created, data: { sessionID: `ses_${i}` }, durable: { seq: 2 } });
    const gravados = await doTipo("session.execution.started", casos.length);
    const por = Object.fromEntries(gravados.map((e) => [e.x.id, e]));
    expect(por.evt_ok.x.at).toBe(bom);
    for (const [eid] of casos.slice(1)) expect(por[eid].x.at).toBeUndefined();
    for (const e of gravados) expect(e.ts).toBeGreaterThanOrEqual(antes); // quando foi observado, à parte
  });

  it("ordem do produtor: cresce a cada evento, é a mesma entre instâncias, e a leitura fica antes do que chega nela", async () => {
    const storage = banco();
    let disparou = false;
    const a: ReturnType<typeof ctx> = ctx({
      storage, sessoes: [sessao("ses_a", "/projetos/api")],
      aoListar: () => {
        if (disparou) return;
        disparou = true;
        a.fluxos[0].emitir(ev("session.execution.started", "evt_meio", { sessionID: "ses_a" }, 2));
      },
    });
    await ligar(a);
    await doTipo("session.execution.started");
    const b = ctx({ storage, dir: "/projetos/site" });
    await ligar(b);
    await ate(async () => b.fluxos.length === 1);
    for (const i of [1, 2, 3]) {
      const e = ev("session.execution.succeeded", `evt_${i}`, { sessionID: "ses_a" }, 2 + i);
      a.fluxos[0].emitir(e);
      b.fluxos[0].emitir(e);
    }
    const feitos = await doTipo("session.execution.succeeded", 3);
    const todos = await eventos();
    const foto = todos.find((e) => e.event === "ombro.snapshot" && e.session.endsWith("ses_a"));
    const meio = todos.find((e) => e.x.id === "evt_meio");
    expect(foto.x.n).toBeLessThan(meio.x.n);
    const ns = [meio, ...feitos].map((e) => e.x.n);
    expect(ns).toEqual([...ns].sort((x, y) => x - y));
    expect(new Set(ns).size).toBe(ns.length);
  });

  it("remoção perdida: só com a prova do session.get, mesmo com a leitura incompleta", async () => {
    await mkdir(join(pasta, "opencode", "known"), { recursive: true });
    await writeFile(join(pasta, "opencode", "known", `${ORIGEM}.json`), JSON.stringify({ sessions: ["ses_sumida", "ses_viva", "ses_duvida", "../x"] }));
    await ligar(ctx({
      sessoes: [sessao("ses_viva", "/projetos/api"), sessao("ses_outra", "/projetos/api")],
      porPagina: 1, falharPagina: 2, ausentes: ["ses_sumida"], getQuebrado: ["ses_duvida"],
    }));
    const [r] = await doTipo("ombro.removed");
    await new Promise((x) => setTimeout(x, 80));
    expect(r.session).toBe(`${ORIGEM}:ses_sumida`);
    expect((await eventos()).filter((e) => e.event === "ombro.removed")).toHaveLength(1);
  });

  it("o pulso pedido nas opções é limitado e não acelera a reconexão", async () => {
    delete (globalThis as Record<symbol, unknown>)[TESTE];
    const c = ctx({ heartbeatMs: 1 });
    await ligar(c);
    await pronto();
    await new Promise((r) => setTimeout(r, 150)); // a primeira leitura termina e avisa
    const antes = (await pulsos())[0].ts;
    await new Promise((r) => setTimeout(r, 400));
    expect((await pulsos())[0].ts).toBe(antes); // o piso é de segundos
    c.fluxos[0].encerrar();
    await new Promise((r) => setTimeout(r, 500));
    expect(c.fluxos).toHaveLength(1);
    await ate(async () => c.fluxos.length === 2, 2500);
  });

  it("fluxo que sempre cai na hora não vira um laço", async () => {
    const c = ctx({ heartbeatMs: 1 });
    c.alvo.event.subscribe = () => {
      const f = fluxo();
      c.fluxos.push(f);
      f.encerrar();
      return f;
    };
    await ligar(c);
    await new Promise((r) => setTimeout(r, 2200));
    expect(c.fluxos.length).toBeLessThanOrEqual(3);
  });

  it("um erro interno não derruba o OpenCode", async () => {
    const c = ctx();
    c.alvo.storage.get = async () => {
      throw new Error("storage fora");
    };
    await expect(plugin.setup(c.alvo)).resolves.toBeTypeOf("function");
    expect(await eventos()).toEqual([]);
  });
});
