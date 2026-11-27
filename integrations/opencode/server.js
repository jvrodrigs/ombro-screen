// Plugin de servidor do OpenCode (V2, 2.4.2 ou mais novo) que avisa o Ombro sobre as sessões. Você o liga à mão, na
// configuração do OpenCode: {"plugins": [{"package": "<esta pasta>", "options": {"origin": "oc-…"}}]}.
//
// Só metadados saem daqui: ids, horários, o tipo do evento, o nome curto da pasta do projeto e ids de pedidos
// abertos. Nunca título, prompt, resposta, comando, conteúdo, caminho completo ou credencial. Não usa rede, não
// responde permissões nem perguntas e não muda nada nas sessões. Do ctx, só usa: app, location, options,
// event.subscribe, session.list, session.get, permission.list e storage (uma chave, para marcar o banco).
//
// A ÂNCORA diz qual instância local é esta, sem abrir o banco: o caminho que o OpenCode usa (OPENCODE_DB, ou
// opencode.db / opencode-<canal>.db na pasta de dados) só vale se um descritor que ESTE processo já tem aberto for
// o mesmo arquivo (stat e fstat, nada além). Abrir ou fechar o SQLite por fora poderia soltar travas POSIX dele,
// então isso nunca é feito. Sem essa prova, nada é coletado.
//
// Grava na pasta do Ombro (options.dir, OMBRO_SCREEN_DIR ou ~/.ombro-screen):
//   eventos/<ordem>.json                um evento, no formato da caixa dos hooks
//   opencode/live/<origem>.<pid>.json   o pulso deste processo (vivo, compatível, coletando, parcial)
import { createHash, randomBytes } from "node:crypto";
import { fstatSync, statSync } from "node:fs";
import { mkdir, readdir, readFile, rename, stat, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, join, resolve } from "node:path";

const MINIMA = [2, 4, 2];
const ORIGEM = /^[a-z0-9][a-z0-9-]{7,63}$/;
const ID = /^[A-Za-z0-9._-]{1,80}$/;
const CHAVE_BANCO = "ombro.banco";
const CANAIS_SEM_SUFIXO = ["latest", "dev", "beta", "next", "prod"];
const MAX_DESCRITORES = 4096;
const PULSO_MS = 20_000;
const PULSO_MIN_MS = 5_000;
const PULSO_MAX_MS = 30_000; // o Ombro considera morto um pulso de mais de 75 s
const ESPERA_MIN_MS = 1_000; // entre uma queda do fluxo e a nova assinatura: nunca menos que isto
const ESPERA_MAX_MS = 60_000;
const FLUXO_ESTAVEL_MS = 30_000;
const FOLGA_FUTURO_MS = 120_000;
const RECENTE_MS = 4 * 3_600_000; // o mesmo prazo em que o Ombro esquece uma sessão parada
const POR_PAGINA = 100;
const MAX_PAGINAS = 10;
const MAX_CONFERIDAS = 200; // sessões conhecidas e ausentes conferidas por session.get a cada leitura
const MAX_PENDENTES = 5000; // arquivos esperando o Ombro (o mesmo teto do hook)
const MAX_VISTOS = 4096;
const EVENTOS = new Set([
  "session.created", "session.deleted", "session.execution.started", "session.execution.succeeded",
  "session.execution.failed", "session.execution.interrupted", "session.retry.scheduled",
  "permission.asked", "permission.replied", "form.created", "form.replied", "form.cancelled",
]);

// o que é um só por processo, mesmo com uma instância do plugin por projeto (o mesmo evento chega a todas)
const PROCESSO = Symbol.for("ombro.opencode.v1");
const TESTE = Symbol.for("ombro.opencode.teste"); // só os testes do repositório o definem

const id = (v) => (typeof v === "string" && ID.test(v) ? v : undefined);
/** Um horário da fonte, em ms; indefinido se ausente ou impossível. Nunca é trocado pelo relógio daqui. */
const ms = (v) => {
  const t = typeof v === "number" ? v : typeof v === "string" ? Date.parse(v) : v instanceof Date ? v.getTime() : NaN;
  return Number.isFinite(t) && t > 0 && t <= Date.now() + FOLGA_FUTURO_MS ? Math.round(t) : undefined;
};
const nomeDaPasta = (d) => (typeof d === "string" && d ? basename(d).slice(0, 80) || null : null);
const dados = (r) => (r && typeof r === "object" && !Array.isArray(r) && "data" in r ? r.data : r);

export function compativel(versao, ctx) {
  const m = /^v?(\d+)\.(\d+)\.(\d+)/.exec(String(versao ?? ""));
  if (!m) return false;
  const v = m.slice(1).map(Number);
  const i = v.findIndex((n, k) => n !== MINIMA[k]);
  if (i >= 0 && v[i] < MINIMA[i]) return false;
  return [ctx?.session?.list, ctx?.session?.get, ctx?.permission?.list, ctx?.event?.subscribe,
    ctx?.storage?.get, ctx?.storage?.set].every((f) => typeof f === "function");
}

/** A âncora desta instância, ou null se não dá para provar que o banco resolvido é o que este processo usa. */
export function ancora(canal) {
  try {
    const semSufixo = CANAIS_SEM_SUFIXO.includes(canal) || ["1", "true"].includes(process.env.OPENCODE_DISABLE_CHANNEL_DB ?? "");
    if (!process.env.OPENCODE_DB && !semSufixo && typeof canal !== "string") return null;
    const nome = process.env.OPENCODE_DB ?? (semSufixo ? "opencode.db" : `opencode-${canal.replace(/[^a-zA-Z0-9._-]/g, "-")}.db`);
    if (nome === ":memory:") return null;
    const pasta = join(process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "opencode");
    const arquivo = statSync(resolve(pasta, nome), { bigint: true }); // metadados do caminho: não abre
    if (!arquivo.isFile() || arquivo.ino <= 0n || arquivo.birthtimeNs <= 0n) return null;
    for (let fd = 0; fd < MAX_DESCRITORES; fd++) {
      try {
        const aberto = fstatSync(fd, { bigint: true }); // só consulta um descritor que já existe
        if (aberto.dev === arquivo.dev && aberto.ino === arquivo.ino) {
          // sem o dispositivo: no macOS ele pode mudar entre inicializações; uma cópia sempre ganha outro inode
          return createHash("sha256").update(`${arquivo.ino}:${arquivo.birthtimeNs}`).digest("hex").slice(0, 32);
        }
      } catch {
        // descritor fechado
      }
    }
    return null;
  } catch {
    return null;
  }
}

function estadoDoProcesso(chave) {
  const todos = (globalThis[PROCESSO] ??= new Map());
  let p = todos.get(chave);
  if (!p) {
    p = { boot: randomBytes(8).toString("hex"), instancias: 0, saudaveis: 0, fotografando: 0, parcial: false,
      vistos: new Set(), ordens: new Map(), contador: 0, banco: null, listado: -1, arquivos: 0, escritos: 0,
      cheio: false, fim: () => todos.delete(chave) };
    todos.set(chave, p);
  }
  return p;
}

/** A ordem do produtor: dada quando o evento chega (a mesma para todas as instâncias), antes de qualquer espera. */
function ordemDe(p, eid) {
  if (!eid) return ++p.contador;
  let n = p.ordens.get(eid);
  if (n === undefined) {
    p.ordens.set(eid, (n = ++p.contador));
    if (p.ordens.size > MAX_VISTOS) p.ordens.delete(p.ordens.keys().next().value);
  }
  return n;
}

async function gravar(pasta, nome, objeto) {
  await mkdir(pasta, { recursive: true, mode: 0o700 });
  const tmp = join(pasta, `.${nome}.${randomBytes(4).toString("hex")}.tmp`); // escondido até ficar inteiro
  await writeFile(tmp, JSON.stringify(objeto), { mode: 0o600, flag: "wx" });
  await rename(tmp, join(pasta, nome));
}

// o pulso é um arquivo por processo: as gravações saem em fila, para um aviso atrasado não passar por cima do "parado"
let filaDoPulso = Promise.resolve();

/** A marca do banco do OpenCode: uma só gravação por processo (as instâncias esperam a mesma promessa). O storage não
 * tem escrita condicional: depois de gravar, espera um instante e relê, para dois processos estreando o mesmo banco
 * tenderem à mesma marca. Se ainda assim divergirem, o Ombro recusa a segunda (nunca mistura). */
function marcaDoBanco(p, storage) {
  p.banco ??= (async () => {
    const atual = await storage.get(CHAVE_BANCO);
    if (id(atual)) return atual;
    await storage.set(CHAVE_BANCO, randomBytes(12).toString("hex"));
    await new Promise((r) => setTimeout(r, 250));
    const gravada = id(await storage.get(CHAVE_BANCO));
    if (!gravada) throw new Error("storage");
    return gravada;
  })().catch((e) => {
    p.banco = null;
    throw e;
  });
  return p.banco;
}

async function iniciar(ctx) {
  const opcoes = ctx.options ?? {};
  const raiz = resolve(String(opcoes.dir || process.env.OMBRO_SCREEN_DIR || join(homedir(), ".ombro-screen"))
    .replace(/^~(?=\/|$)/, homedir()));
  const lives = join(raiz, "opencode", "live");
  const caixa = join(raiz, "eventos");
  const versao = id(ctx.app?.version) ?? null;
  const origem = typeof opcoes.origin === "string" && ORIGEM.test(opcoes.origin) ? opcoes.origin : null;
  const pedido = Number(opcoes.heartbeatMs);
  const pulsoMs = globalThis[TESTE]?.pulsoMs
    ?? (Number.isFinite(pedido) && pedido > 0 ? Math.min(PULSO_MAX_MS, Math.max(PULSO_MIN_MS, pedido)) : PULSO_MS);
  const apto = compativel(versao, ctx);
  const anc = origem && apto ? ancora(ctx.app?.channel) : null;
  const p = estadoDoProcesso(`${raiz}\n${origem ?? ""}\n${apto}\n${anc ?? ""}`);
  p.instancias += 1;
  let parado = false;
  let confere = true; // o banco continua sendo o arquivo da âncora?
  let relogio;

  const pulso = (estado, banco = "") => {
    const dado = { v: 1, origin: origem ?? "unconfigured", pid: process.pid, boot: p.boot, ns: banco, anchor: anc ?? "",
      state: estado, version: versao, partial: p.parcial, ts: Date.now() };
    return (filaDoPulso = filaDoPulso
      .then(() => gravar(lives, `${dado.origin}.${process.pid}.json`, dado))
      .catch(() => {}));
  };

  const sair = async (estado, banco) => {
    if (parado) return;
    parado = true;
    clearInterval(relogio);
    p.instancias -= 1;
    if (p.instancias === 0) {
      p.fim();
      await pulso(estado, banco);
    }
  };

  // sem origem, versão sem suporte ou instância não comprovada: nada é coletado. O pulso diz por quê
  if (!origem || !apto || !anc) {
    const estado = !origem ? "no_origin" : !apto ? "incompatible" : "unverified";
    await pulso(estado);
    relogio = setInterval(() => pulso(estado), pulsoMs);
    relogio.unref?.();
    return () => sair("stopped");
  }

  const banco = await marcaDoBanco(p, ctx.storage);
  const sessoes = new Map(); // id -> {projeto, dir}: o que se leu de cada sessão (nada de título)
  const aqui = String(ctx.location?.directory ?? "");
  let fila = Promise.resolve();
  let gerador = null;
  let saudavel = false;

  const estadoAtual = () => (!confere ? "unverified" : p.fotografando > 0 ? "collecting" : p.parcial ? "error" : "ready");
  const avisar = () => (parado ? Promise.resolve() : pulso(estadoAtual(), banco));
  const naFila = (fn) => (fila = fila.then(() => (parado ? undefined : fn())).catch(() => {
    p.parcial = true;
  }));

  /** `n` é a ordem do produtor; `x.at`, o horário da fonte (quando há); `ts`, quando foi observado aqui. */
  const escrever = async (sessao, nome, n, projeto, x) => {
    if (!confere) return;
    if (p.escritos++ % 100 === 0) {
      const lista = await readdir(caixa).catch(() => []);
      p.cheio = lista.length >= MAX_PENDENTES;
    }
    if (p.cheio) throw new Error("caixa cheia"); // o Ombro está desligado há muito tempo: não enche o disco
    const arquivo = `${String(BigInt(Date.now()) * 1_000_000n + BigInt(p.arquivos++ % 1_000_000)).padStart(20, "0")}-${process.pid}-${randomBytes(3).toString("hex")}.json`;
    await gravar(caixa, arquivo, {
      v: 1, provider: "opencode", session: sessao ? `${origem}:${sessao}` : origem, event: nome, ts: Date.now(),
      project: projeto ?? null, detail: null, x: { origin: origem, boot: p.boot, ns: banco, anc, n, ...x },
    });
  };

  const ler = async (sid) => {
    if (!sessoes.has(sid)) {
      try {
        const s = dados(await ctx.session.get({ sessionID: sid }));
        const dir = s?.location?.directory;
        sessoes.set(sid, { projeto: nomeDaPasta(dir), dir });
      } catch {
        return {}; // sessão que já não existe: o evento segue sem projeto
      }
    }
    return sessoes.get(sid);
  };

  const repassar = async (e, n) => {
    const eid = id(e.id);
    if (!eid || p.vistos.has(eid)) return; // sem id não se identifica; ou outra instância deste processo já gravou
    const d = e.data ?? {};
    const sid = id(d.sessionID ?? d.form?.sessionID ?? d.info?.id);
    if (!sid) return;
    const dir = d.location?.directory;
    if (dir) sessoes.set(sid, { projeto: nomeDaPasta(dir), dir });
    const { projeto } = e.type === "session.deleted" ? (sessoes.get(sid) ?? {}) : await ler(sid);
    const x = { id: eid };
    const quando = ms(e.created);
    if (quando !== undefined) x.at = quando;
    if (Number.isInteger(e.durable?.seq)) x.seq = e.durable.seq;
    if (e.type.startsWith("permission.")) x.req = id(e.type === "permission.asked" ? d.id : (d.requestID ?? d.id));
    if (e.type.startsWith("form.")) x.req = id(d.form?.id ?? d.formID ?? d.id);
    if (p.vistos.has(eid)) return;
    p.vistos.add(eid); // marca antes de gravar (outra instância pode chegar aqui enquanto grava)...
    if (p.vistos.size > MAX_VISTOS) p.vistos.delete(p.vistos.values().next().value);
    try {
      await escrever(sid, e.type, n, projeto, x);
    } catch (erro) {
      p.vistos.delete(eid); // ...e desmarca se não gravou
      throw erro;
    }
    if (e.type === "session.deleted") sessoes.delete(sid);
  };

  /** Uma remoção que se perdeu: das sessões que o Ombro conhece e não vieram na leitura, só as que session.get diz
   * que não existem. Estar fora de uma lista não prova nada. */
  const conferirAusentes = async (listadas) => {
    let conhecidas = [];
    try {
      conhecidas = JSON.parse(await readFile(join(raiz, "opencode", "known", `${origem}.json`), "utf8")).sessions;
    } catch {
      // o Ombro ainda não guardou nenhuma
    }
    const ausentes = [...new Set([...(Array.isArray(conhecidas) ? conhecidas : []), ...sessoes.keys()])]
      .filter((sid) => id(sid) && !listadas.has(sid)).slice(0, MAX_CONFERIDAS);
    for (const sid of ausentes) {
      const n = ++p.contador;
      try {
        await ctx.session.get({ sessionID: sid });
      } catch (erro) {
        if ((erro?._tag ?? erro?.name) !== "Session.NotFoundError") continue;
        await escrever(sid, "ombro.removed", n, sessoes.get(sid)?.projeto, { id: "", at: Date.now() });
        sessoes.delete(sid);
      }
    }
  };

  /** Lê as sessões recentes (todas as páginas, com limite). Uma instância por processo lê as de outros projetos; cada
   * instância cuida das do seu projeto, o único em que permission.list responde. */
  const fotografar = async (geracao) => {
    p.fotografando += 1;
    await avisar();
    let completa = true;
    const outras = p.listado < geracao;
    if (outras) p.listado = geracao;
    const n = ++p.contador; // a leitura vale como de agora: o que chegar durante ela é posterior
    const listadas = new Set();
    try {
      let cursor;
      for (let pagina = 0; ; pagina++) {
        if (pagina === MAX_PAGINAS) {
          completa = false;
          break;
        }
        const r = await ctx.session.list(cursor ? { limit: POR_PAGINA, cursor } : { limit: POR_PAGINA });
        const itens = Array.isArray(r) ? r : r?.data;
        if (!Array.isArray(itens)) throw new Error("lista");
        for (const s of itens) {
          const sid = id(s?.id);
          if (!sid) continue;
          listadas.add(sid);
          const atualizada = ms(s.time?.updated) ?? ms(s.time?.created);
          if (!atualizada || s.time?.archived || Date.now() - atualizada > RECENTE_MS) continue;
          const dir = s.location?.directory;
          sessoes.set(sid, { projeto: nomeDaPasta(dir), dir });
          const minha = dir === aqui;
          if (!minha && !outras) continue;
          let pendentes = [];
          if (minha) {
            try {
              const lista = dados(await ctx.permission.list({ sessionID: sid }));
              pendentes = (Array.isArray(lista) ? lista : []).map((q) => id(q?.id)).filter(Boolean).slice(0, 32);
            } catch {
              completa = false;
            }
          }
          const snap = { created: ms(s.time?.created), updated: atualizada, busy: ms(s.time?.busy),
            idle: ms(s.time?.idle), pending: pendentes };
          await escrever(sid, "ombro.snapshot", n, nomeDaPasta(dir), { id: "", at: atualizada, snap });
        }
        cursor = r?.cursor?.next;
        if (!cursor || !itens.length) break;
      }
    } catch {
      completa = false;
      if (outras) p.listado = geracao - 1; // outra instância pode tentar
    }
    try {
      if (outras) await conferirAusentes(listadas);
    } catch {
      completa = false;
    } finally {
      p.fotografando -= 1;
      p.parcial = !completa;
    }
    await avisar();
  };

  const pedidoDeFoto = async () => (await stat(join(raiz, "opencode", "resync")).catch(() => null))?.mtimeMs ?? 0;
  let geracao = await pedidoDeFoto();

  /** Assina primeiro e lê depois: o que chegar durante a leitura espera na fila e é aplicado em seguida. */
  const assinar = async () => {
    let espera = ESPERA_MIN_MS;
    while (!parado) {
      const desde = Date.now();
      try {
        gerador = ctx.event.subscribe()[Symbol.asyncIterator]();
        let proximo = gerador.next(); // a assinatura começa aqui, antes da leitura
        saudavel = true;
        p.saudaveis += 1;
        naFila(() => fotografar(geracao));
        for (;;) {
          const { value, done } = await proximo;
          if (done || parado) break;
          if (value?.type && EVENTOS.has(value.type)) {
            const n = ordemDe(p, id(value.id));
            naFila(() => repassar(value, n));
          }
          proximo = gerador.next();
        }
      } catch {
        // o fluxo caiu: abaixo
      }
      if (saudavel) {
        saudavel = false;
        p.saudaveis -= 1;
      }
      if (parado) break;
      // nenhuma instância ouvindo: pode ter se perdido um evento. O Ombro passa a não saber, e lê-se de novo
      if (p.saudaveis === 0) {
        const n = ++p.contador;
        await naFila(() => escrever(null, "ombro.gap", n, null, { id: "", at: Date.now() }));
        geracao += 1;
      }
      if (Date.now() - desde >= FLUXO_ESTAVEL_MS) espera = ESPERA_MIN_MS;
      await new Promise((r) => {
        const t = setTimeout(r, espera); // não depende do pulso: nunca vira um laço
        t.unref?.();
      });
      espera = Math.min(espera * 2, ESPERA_MAX_MS);
    }
  };

  await avisar();
  relogio = setInterval(async () => {
    confere = ancora(ctx.app?.channel) === anc; // o arquivo foi trocado com o OpenCode no ar?
    const pedidoNovo = await pedidoDeFoto(); // o Ombro reiniciou e quer saber o que há
    if (confere && pedidoNovo > geracao) {
      geracao = pedidoNovo;
      naFila(() => fotografar(pedidoNovo));
    }
    await avisar();
  }, pulsoMs);
  relogio.unref?.();
  assinar();

  return async () => {
    if (saudavel) {
      saudavel = false;
      p.saudaveis -= 1;
    }
    await sair("stopped", banco);
    gerador?.return?.()?.catch?.(() => {});
  };
}

export default {
  id: "ombro.monitor",
  async setup(ctx) {
    try {
      return await iniciar(ctx);
    } catch {
      return () => {}; // um monitor nunca atrapalha o OpenCode
    }
  },
};
