// Os avatares do Ombro no app: os mesmos arquivos que a telinha usa (avatars/ na raiz do repositório), no formato
// do open-bot-avatar (contrato v1), com a mesma regra de escolha do backend (avatares.py). O que o ombrod decidiu
// (o "aleatório ao iniciar" já sorteado) chega pela IPC; esta cópia da regra serve para o que é por sessão e para
// mostrar a galeria sem depender do ombrod.
import { validateAvatar, type AvatarDefinition, type AvatarMotion } from "open-bot-avatar";
import catalogJson from "../../../../avatars/catalog.json";
import type { ProviderId } from "../../types/ombro";

const DEFS = import.meta.glob<{ default: unknown }>("../../../../avatars/defs/*.json", { eager: true });

export const SPECIAL = ["padrao", "aleatorio"] as const;
export type AvatarChoice = "padrao" | "aleatorio" | string;
export type RandomMode = "inicio" | "sessao";
export type ColorMode = "agente" | "sorteada";
export type AvatarSlot = ProviderId | "padrao";

export interface AvatarEntry {
  id: string;
  name: string;
  description: string;
  tags: string[];
  color: string;
  random: boolean;
  definition: AvatarDefinition;
}

export interface Catalog {
  avatars: AvatarEntry[];
  byId: Map<string, AvatarEntry>;
  defaultId: string;
  providerDefault: Record<string, string>;
  providerColor: Record<string, string>;
  /** Os do sorteio, em ordem de id: a mesma ordem do backend (avatares.Catalogo.sorteaveis). */
  pool: string[];
  /** Definições que não passaram no validador do open-bot-avatar (não entram no catálogo). */
  invalid: { id: string; errors: string[] }[];
}

interface CatalogJson {
  version: number;
  default: string;
  providers: Record<string, { avatar: string; color: string }>;
  avatars: { id: string; description: string; tags: string[]; color: string; random: boolean }[];
}

export function buildCatalog(json: CatalogJson = catalogJson as CatalogJson, defs: Record<string, unknown> = defsById()): Catalog {
  const avatars: AvatarEntry[] = [];
  const invalid: Catalog["invalid"] = [];
  for (const meta of json.avatars) {
    const r = validateAvatar(defs[meta.id]);
    if (!r.ok) {
      invalid.push({ id: meta.id, errors: r.errors });
      continue;
    }
    avatars.push({ id: meta.id, name: r.value.name, description: meta.description, tags: meta.tags, color: meta.color.toUpperCase(), random: meta.random, definition: r.value });
  }
  const byId = new Map(avatars.map((a) => [a.id, a]));
  const providerDefault: Record<string, string> = {};
  const providerColor: Record<string, string> = {};
  for (const [p, v] of Object.entries(json.providers)) {
    providerDefault[p] = v.avatar;
    providerColor[p] = v.color.toUpperCase();
  }
  const pool = avatars.filter((a) => a.random).map((a) => a.id).sort();
  return { avatars, byId, defaultId: json.default, providerDefault, providerColor, pool, invalid };
}

function defsById(): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [path, mod] of Object.entries(DEFS)) {
    const id = path.split("/").pop()!.replace(/\.json$/, "");
    out[id] = mod.default;
  }
  return out;
}

let cached: Catalog | null = null;
export function catalog(): Catalog {
  return (cached ??= buildCatalog());
}

/** FNV-1a de 32 bits sobre o UTF-8: a mesma conta do avatares.fnv1a (Python). */
export function fnv1a(text: string): number {
  let h = 0x811c9dc5;
  for (const b of new TextEncoder().encode(text)) {
    h ^= b;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

export function defaultFor(cat: Catalog, provider: ProviderId | null): AvatarEntry {
  const id = (provider && cat.providerDefault[provider]) || cat.defaultId;
  return cat.byId.get(id) ?? cat.avatars[0];
}

/** O avatar de um agente (ou do Ombro, com provider null). Igual ao avatares.escolher: nunca falha. */
export function chooseAvatar(
  cat: Catalog,
  choice: AvatarChoice | undefined,
  provider: ProviderId | null,
  mode: RandomMode = "inicio",
  seed = 0,
  session?: string | null,
): AvatarEntry {
  if (choice === "aleatorio" && cat.pool.length) {
    const who = provider ?? "ombro";
    const key = mode === "sessao" && session ? `${who}:${session}` : `${who}:${seed}`;
    return cat.byId.get(cat.pool[fnv1a(key) % cat.pool.length])!;
  }
  if (choice && !SPECIAL.includes(choice as "padrao") && cat.byId.has(choice)) return cat.byId.get(choice)!;
  return defaultFor(cat, provider);
}

/** A cor de um avatar: a do agente (o padrão), ou a do próprio avatar quando não há agente. */
export function colorFor(cat: Catalog, provider: ProviderId | null, avatar: AvatarEntry): string {
  return (provider && cat.providerColor[provider]) || avatar.color;
}

/** O estado do mascote do Ombro -> o humor do open-bot-avatar (idle, thinking, happy). */
export function moodFor(state: string | undefined): AvatarMotion {
  if (state === "trabalhando" || state === "alerta") return "thinking";
  if (state === "concluido" || state === "reset") return "happy";
  return "idle";
}
