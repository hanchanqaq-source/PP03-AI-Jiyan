import { storageGet, storageSet } from "@/lib/storage";
import { getTag, TAG_CATALOG } from "./catalog";
import type {
  CustomTagCatalogItem,
  CustomTagCatalogState,
  CustomTagCreateResult,
  PageKey,
  PageTagState,
  PageTagStateInput,
  TagDefinition,
} from "./types";

export const DEFAULT_TAG_IDS = ["semiconductor", "storage", "robotics"];
export const PAGE_TAG_STATE_VERSION = 2 as const;
export const CUSTOM_TAG_CATALOG_VERSION = 1 as const;
export const CUSTOM_TAG_CATALOG_KEY = "vr-custom-tags";
const DEFAULT_ACTIVE = "storage";
const PAGE_KEYS: PageKey[] = ["market_news", "industry_research"];
const CUSTOM_ID_PATTERN = /^custom-[a-z0-9][a-z0-9-]{0,56}$/;
const CONTROL_OR_INVISIBLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u;
const keyFor = (pageKey: PageKey) => `vr-page-tags:${pageKey}`;

const fallback = (): PageTagState => ({
  version: PAGE_TAG_STATE_VERSION,
  ids: [...DEFAULT_TAG_IDS],
  activeId: DEFAULT_ACTIVE,
  order: [...DEFAULT_TAG_IDS],
});

function emptyCustomCatalog(): CustomTagCatalogState {
  return { version: CUSTOM_TAG_CATALOG_VERSION, items: [] };
}

function customTagDefinition(item: CustomTagCatalogItem): TagDefinition {
  return {
    id: item.id,
    name: item.name,
    slug: item.id,
    type: "自定义标签",
    parent_id: null,
    aliases: [],
    keywords: [item.name],
    description: "用户创建的行业研究标签；没有已批准模板时只显示建设状态。",
    sort_order: 0,
    enabled: true,
    kind: "custom",
  };
}

function normalizedName(value: string): string {
  return value.normalize("NFKC").trim().replace(/\s+/gu, " ");
}

function duplicateKey(value: string): string {
  return normalizedName(value).toLocaleLowerCase("zh-CN");
}

function graphemeLength(value: string): number {
  type SegmenterLike = new (
    locale: string,
    options: { granularity: "grapheme" },
  ) => { segment(input: string): Iterable<unknown> };
  const Segmenter = (Intl as unknown as { Segmenter?: SegmenterLike }).Segmenter;
  return Segmenter
    ? Array.from(new Segmenter("zh-CN", { granularity: "grapheme" }).segment(value)).length
    : Array.from(value).length;
}

function validateCustomName(value: string): string {
  const canonical = value.normalize("NFKC");
  if (CONTROL_OR_INVISIBLE.test(canonical)) throw new Error("标签名称不能包含控制字符或不可见字符");
  const name = normalizedName(canonical);
  if (!name) throw new Error("请输入标签名称");
  if (graphemeLength(name) > 32) throw new Error("标签名称不能超过 32 个字符");
  return name;
}

function secureCustomId(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.getRandomValues) throw new Error("当前浏览器无法安全创建标签，请稍后重试");
  const bytes = new Uint8Array(16);
  cryptoApi.getRandomValues(bytes);
  return `custom-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function parseCustomCatalog(value: unknown): CustomTagCatalogState {
  if (!value || typeof value !== "object") return emptyCustomCatalog();
  const raw = value as Partial<CustomTagCatalogState>;
  if (raw.version !== CUSTOM_TAG_CATALOG_VERSION || !Array.isArray(raw.items)) return emptyCustomCatalog();
  const ids = new Set<string>();
  const names = new Set<string>();
  const items: CustomTagCatalogItem[] = [];
  for (const item of raw.items) {
    if (!item || typeof item !== "object") return emptyCustomCatalog();
    const candidate = item as Partial<CustomTagCatalogItem>;
    if (candidate.kind !== "custom" || typeof candidate.id !== "string"
      || !CUSTOM_ID_PATTERN.test(candidate.id) || typeof candidate.name !== "string") return emptyCustomCatalog();
    let name: string;
    try {
      name = validateCustomName(candidate.name);
    } catch {
      return emptyCustomCatalog();
    }
    const key = duplicateKey(name);
    if (ids.has(candidate.id) || names.has(key)) return emptyCustomCatalog();
    ids.add(candidate.id);
    names.add(key);
    items.push({ id: candidate.id, name, kind: "custom" });
  }
  return { version: CUSTOM_TAG_CATALOG_VERSION, items };
}

export function loadCustomTagCatalog(): CustomTagCatalogState {
  const raw = storageGet(CUSTOM_TAG_CATALOG_KEY);
  if (!raw) return emptyCustomCatalog();
  try {
    return parseCustomCatalog(JSON.parse(raw));
  } catch {
    return emptyCustomCatalog();
  }
}

function saveCustomTagCatalog(items: CustomTagCatalogItem[]): CustomTagCatalogState {
  const catalog: CustomTagCatalogState = { version: CUSTOM_TAG_CATALOG_VERSION, items };
  storageSet(CUSTOM_TAG_CATALOG_KEY, JSON.stringify(catalog));
  return catalog;
}

export function resolveTag(id: string): TagDefinition | undefined {
  const builtIn = getTag(id);
  if (builtIn) return builtIn;
  const custom = loadCustomTagCatalog().items.find((tag) => tag.id === id);
  return custom ? customTagDefinition(custom) : undefined;
}

export function createOrReuseCustomTag(
  value: string,
  createId: () => string = secureCustomId,
): CustomTagCreateResult {
  const name = validateCustomName(value);
  const key = duplicateKey(name);
  const builtIn = TAG_CATALOG.find((tag) => (
    [tag.name, ...tag.aliases].some((candidate) => duplicateKey(candidate) === key)
  ));
  if (builtIn) return { tag: builtIn, created: false };

  const catalog = loadCustomTagCatalog();
  const existing = catalog.items.find((tag) => duplicateKey(tag.name) === key);
  if (existing) return { tag: customTagDefinition(existing), created: false };

  const occupied = new Set([
    ...TAG_CATALOG.map((tag) => tag.id),
    ...catalog.items.map((tag) => tag.id),
  ]);
  let id = "";
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const candidate = createId();
    if (!CUSTOM_ID_PATTERN.test(candidate)) throw new Error("无法生成安全的标签 ID，请稍后重试");
    if (!occupied.has(candidate)) {
      id = candidate;
      break;
    }
  }
  if (!id) throw new Error("无法生成唯一的标签 ID，请稍后重试");
  const item: CustomTagCatalogItem = { id, name, kind: "custom" };
  saveCustomTagCatalog([...catalog.items, item]);
  return { tag: customTagDefinition(item), created: true };
}

function normalize(value: unknown): PageTagState {
  if (!value || typeof value !== "object") return fallback();
  const raw = value as Partial<PageTagState>;
  if (raw.version !== undefined && raw.version !== PAGE_TAG_STATE_VERSION) return fallback();
  if (!Array.isArray(raw.ids)) return fallback();
  const selected = new Set(raw.ids.filter((id): id is string => typeof id === "string" && !!resolveTag(id)));
  const requestedOrder = raw.version === PAGE_TAG_STATE_VERSION && Array.isArray(raw.order) ? raw.order : raw.ids;
  const order = Array.from(new Set([
    ...requestedOrder.filter((id): id is string => typeof id === "string" && selected.has(id)),
    ...selected,
  ]));
  const ids = [...order];
  if (ids.length === 0) return { version: PAGE_TAG_STATE_VERSION, ids: [], activeId: "", order: [] };
  const activeId = typeof raw.activeId === "string" && ids.includes(raw.activeId) ? raw.activeId : ids[0];
  return { version: PAGE_TAG_STATE_VERSION, ids, activeId, order };
}

export function loadPageTagState(pageKey: PageKey): PageTagState {
  const raw = storageGet(keyFor(pageKey));
  if (!raw) return fallback();
  try {
    const normalized = normalize(JSON.parse(raw));
    if (JSON.stringify(normalized) !== raw) storageSet(keyFor(pageKey), JSON.stringify(normalized));
    return normalized;
  } catch {
    const safe = fallback();
    storageSet(keyFor(pageKey), JSON.stringify(safe));
    return safe;
  }
}

export function savePageTagState(pageKey: PageKey, state: PageTagStateInput): PageTagState {
  const normalized = normalize(state);
  storageSet(keyFor(pageKey), JSON.stringify(normalized));
  return normalized;
}

export function deleteCustomTag(id: string): boolean {
  const catalog = loadCustomTagCatalog();
  if (!catalog.items.some((tag) => tag.id === id)) return false;
  const pageStates = PAGE_KEYS.map((pageKey) => ({ pageKey, state: loadPageTagState(pageKey) }));
  saveCustomTagCatalog(catalog.items.filter((tag) => tag.id !== id));
  for (const { pageKey, state } of pageStates) {
    if (!state.ids.includes(id)) continue;
    const order = state.order.filter((tagId) => tagId !== id);
    savePageTagState(pageKey, {
      ids: order,
      order,
      activeId: state.activeId === id ? order[0] || "" : state.activeId,
    });
  }
  return true;
}

export function moveTag(ids: string[], sourceId: string, targetId: string): string[] {
  const sourceIndex = ids.indexOf(sourceId);
  const targetIndex = ids.indexOf(targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return [...ids];
  const next = [...ids];
  next.splice(sourceIndex, 1);
  next.splice(next.indexOf(targetId), 0, sourceId);
  return next;
}
