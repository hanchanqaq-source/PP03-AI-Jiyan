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
export const MAX_TAG_STORAGE_BYTES = 64 * 1024;
export const MAX_CUSTOM_TAG_COUNT = 256;
export const MAX_PAGE_TAG_COUNT = 256;
const MAX_STORED_STRING_BYTES = 256;
const DEFAULT_ACTIVE = "storage";
const PAGE_KEYS: PageKey[] = ["market_news", "industry_research"];
const CUSTOM_ID_PATTERN = /^custom-[a-z0-9][a-z0-9-]{0,56}$/;
const CONTROL_OR_INVISIBLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u;
const keyFor = (pageKey: PageKey) => `vr-page-tags:${pageKey}`;

export type TagStorageParseStatus = "valid" | "legacy" | "corrupt" | "unsupported" | "oversized";

export interface TagStorageInspection<T> {
  status: TagStorageParseStatus;
  value: T;
  raw: string | null;
}

interface CatalogIndex {
  byId: Map<string, TagDefinition>;
  byName: Map<string, TagDefinition>;
}

const fallback = (): PageTagState => ({
  version: PAGE_TAG_STATE_VERSION,
  ids: [...DEFAULT_TAG_IDS],
  activeId: DEFAULT_ACTIVE,
  order: [...DEFAULT_TAG_IDS],
});

function emptyCustomCatalog(): CustomTagCatalogState {
  return { version: CUSTOM_TAG_CATALOG_VERSION, items: [] };
}

function utf8Bytes(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function exactRecord(value: unknown, keys: readonly string[]): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return false;
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function readRaw(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    throw new Error("无法读取本地标签存储，请检查浏览器存储权限");
  }
}

function writeRawVerified(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
    if (localStorage.getItem(key) !== value) throw new Error("readback mismatch");
  } catch {
    throw new Error("本地标签存储写入失败，请检查浏览器存储空间");
  }
}

function restoreRawVerified(key: string, raw: string | null): void {
  try {
    if (raw === null) {
      localStorage.removeItem(key);
      if (localStorage.getItem(key) !== null) throw new Error("remove readback mismatch");
      return;
    }
    writeRawVerified(key, raw);
  } catch {
    throw new Error("本地标签存储回滚失败");
  }
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
  if (typeof value !== "string" || utf8Bytes(value) > MAX_STORED_STRING_BYTES) {
    throw new Error("标签名称不能超过 32 个字符");
  }
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

function parseCatalogRaw(raw: string | null): TagStorageInspection<CustomTagCatalogState> {
  const empty = emptyCustomCatalog();
  if (raw === null) return { status: "valid", value: empty, raw };
  if (utf8Bytes(raw) > MAX_TAG_STORAGE_BYTES) return { status: "oversized", value: empty, raw };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { status: "corrupt", value: empty, raw };
  }
  if (!exactRecord(parsed, ["version", "items"])) {
    if (exactRecord(parsed, ["version", "items", "extra"]) && parsed.version !== CUSTOM_TAG_CATALOG_VERSION) {
      return { status: "unsupported", value: empty, raw };
    }
    if (parsed && typeof parsed === "object" && Object.prototype.hasOwnProperty.call(parsed, "version")
      && (parsed as { version?: unknown }).version !== CUSTOM_TAG_CATALOG_VERSION) {
      return { status: "unsupported", value: empty, raw };
    }
    return { status: "corrupt", value: empty, raw };
  }
  if (parsed.version !== CUSTOM_TAG_CATALOG_VERSION) return { status: "unsupported", value: empty, raw };
  if (!Array.isArray(parsed.items)) return { status: "corrupt", value: empty, raw };
  if (parsed.items.length > MAX_CUSTOM_TAG_COUNT) return { status: "oversized", value: empty, raw };

  const ids = new Set<string>();
  const names = new Set<string>();
  const builtInNames = new Set(TAG_CATALOG.flatMap((tag) => (
    [tag.name, ...tag.aliases].map(duplicateKey)
  )));
  const items: CustomTagCatalogItem[] = [];
  let status: TagStorageParseStatus = "valid";
  for (const item of parsed.items) {
    if (!exactRecord(item, ["id", "name", "kind"])) {
      status = "corrupt";
      continue;
    }
    if (typeof item.id !== "string" || typeof item.name !== "string") {
      status = "corrupt";
      continue;
    }
    if (utf8Bytes(item.id) > MAX_STORED_STRING_BYTES || utf8Bytes(item.name) > MAX_STORED_STRING_BYTES) {
      status = "oversized";
      continue;
    }
    if (item.kind !== "custom" || !CUSTOM_ID_PATTERN.test(item.id)) {
      status = "corrupt";
      continue;
    }
    let name: string;
    try {
      name = validateCustomName(item.name);
    } catch {
      status = "corrupt";
      continue;
    }
    const key = duplicateKey(name);
    if (ids.has(item.id) || names.has(key) || builtInNames.has(key)) {
      status = "corrupt";
      continue;
    }
    ids.add(item.id);
    names.add(key);
    items.push({ id: item.id, name, kind: "custom" });
  }
  return { status, value: { version: CUSTOM_TAG_CATALOG_VERSION, items }, raw };
}

function catalogIndex(catalog: CustomTagCatalogState): CatalogIndex {
  const byId = new Map<string, TagDefinition>();
  const byName = new Map<string, TagDefinition>();
  for (const tag of TAG_CATALOG) {
    byId.set(tag.id, tag);
    for (const candidate of [tag.name, ...tag.aliases]) byName.set(duplicateKey(candidate), tag);
  }
  for (const item of catalog.items) {
    const tag = customTagDefinition(item);
    byId.set(item.id, tag);
    byName.set(duplicateKey(item.name), tag);
  }
  return { byId, byName };
}

function boundedStrings(value: unknown, maxCount: number): value is string[] {
  if (!Array.isArray(value) || value.length > maxCount) return false;
  return value.every((item) => typeof item === "string" && utf8Bytes(item) <= MAX_STORED_STRING_BYTES);
}

function unique(values: string[]): boolean {
  return new Set(values).size === values.length;
}

function parseStoredPageRaw(raw: string | null, index: CatalogIndex): TagStorageInspection<PageTagState> {
  const safe = fallback();
  if (raw === null) return { status: "valid", value: safe, raw };
  if (utf8Bytes(raw) > MAX_TAG_STORAGE_BYTES) return { status: "oversized", value: safe, raw };
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { status: "corrupt", value: safe, raw };
  }

  const legacy = exactRecord(parsed, ["ids", "activeId"]);
  if (!legacy) {
    if (parsed && typeof parsed === "object" && Object.prototype.hasOwnProperty.call(parsed, "version")
      && (parsed as { version?: unknown }).version !== PAGE_TAG_STATE_VERSION) {
      return { status: "unsupported", value: safe, raw };
    }
    if (!exactRecord(parsed, ["version", "ids", "activeId", "order"])) {
      return { status: "corrupt", value: safe, raw };
    }
  }

  const record = parsed as Record<string, unknown>;
  const idsValue = record.ids;
  const orderValue = legacy ? record.ids : record.order;
  if (!Array.isArray(idsValue) || !Array.isArray(orderValue)) return { status: "corrupt", value: safe, raw };
  if (idsValue.length > MAX_PAGE_TAG_COUNT || orderValue.length > MAX_PAGE_TAG_COUNT) {
    return { status: "oversized", value: safe, raw };
  }
  if (idsValue.some((item) => typeof item === "string" && utf8Bytes(item) > MAX_STORED_STRING_BYTES)
    || orderValue.some((item) => typeof item === "string" && utf8Bytes(item) > MAX_STORED_STRING_BYTES)
    || (typeof record.activeId === "string" && utf8Bytes(record.activeId) > MAX_STORED_STRING_BYTES)) {
    return { status: "oversized", value: safe, raw };
  }
  if (!boundedStrings(idsValue, MAX_PAGE_TAG_COUNT) || !boundedStrings(orderValue, MAX_PAGE_TAG_COUNT)
    || typeof record.activeId !== "string" || utf8Bytes(record.activeId) > MAX_STORED_STRING_BYTES) {
    return { status: "corrupt", value: safe, raw };
  }
  const selectedIds = new Set(idsValue);
  if (!unique(idsValue) || !unique(orderValue)
    || idsValue.length !== orderValue.length
    || idsValue.some((id) => !index.byId.has(id))
    || orderValue.some((id) => !index.byId.has(id) || !selectedIds.has(id))) {
    const seen = new Set<string>();
    const available = orderValue.filter((id) => index.byId.has(id) && !seen.has(id) && !!seen.add(id));
    return { status: "corrupt", value: {
      version: PAGE_TAG_STATE_VERSION,
      ids: available,
      activeId: available.includes(record.activeId as string) ? record.activeId as string : available[0] || "",
      order: available,
    }, raw };
  }
  if (idsValue.length === 0) {
    if (record.activeId !== "") return { status: "corrupt", value: { version: 2, ids: [], activeId: "", order: [] }, raw };
    return { status: legacy ? "legacy" : "valid", value: { version: 2, ids: [], activeId: "", order: [] }, raw };
  }
  if (!idsValue.includes(record.activeId as string)) return { status: "corrupt", value: safe, raw };
  const value: PageTagState = {
    version: PAGE_TAG_STATE_VERSION,
    ids: [...orderValue],
    activeId: record.activeId as string,
    order: [...orderValue],
  };
  return { status: legacy ? "legacy" : "valid", value, raw };
}

function normalizePageInput(value: PageTagStateInput, index: CatalogIndex): PageTagState {
  const versioned = exactRecord(value, ["version", "ids", "activeId", "order"])
    && value.version === PAGE_TAG_STATE_VERSION;
  if (!versioned && !exactRecord(value, ["ids", "activeId", "order"])
    && !exactRecord(value, ["ids", "activeId"])) {
    throw new Error("页面标签数据格式无效");
  }
  const requestedOrder = value.order ?? value.ids;
  if (!Array.isArray(value.ids) || !Array.isArray(requestedOrder)
    || value.ids.length > MAX_PAGE_TAG_COUNT || requestedOrder.length > MAX_PAGE_TAG_COUNT
    || !boundedStrings(value.ids, MAX_PAGE_TAG_COUNT) || !boundedStrings(requestedOrder, MAX_PAGE_TAG_COUNT)
    || typeof value.activeId !== "string" || utf8Bytes(value.activeId) > MAX_STORED_STRING_BYTES) {
    throw new Error("页面标签数据过大或格式无效");
  }
  const selected = new Set(value.ids.filter((id) => index.byId.has(id)));
  const order = Array.from(new Set([
    ...requestedOrder.filter((id) => selected.has(id)),
    ...selected,
  ]));
  const activeId = order.includes(value.activeId) ? value.activeId : order[0] || "";
  return { version: PAGE_TAG_STATE_VERSION, ids: [...order], activeId, order: [...order] };
}

function mutableError(subject: "自定义标签" | "页面标签", status: TagStorageParseStatus): Error {
  const state = status === "corrupt" ? "已损坏"
    : status === "unsupported" ? "版本不受支持"
      : status === "oversized" ? "数据过大"
        : "状态无效";
  return new Error(`${subject}存储${state}，无法安全修改`);
}

function assertCatalogMutable(inspection: TagStorageInspection<CustomTagCatalogState>): void {
  if (inspection.status !== "valid") throw mutableError("自定义标签", inspection.status);
}

function assertPageMutable(inspection: TagStorageInspection<PageTagState>): void {
  if (inspection.status !== "valid" && inspection.status !== "legacy") {
    throw mutableError("页面标签", inspection.status);
  }
}

function selectCustomCandidate(
  value: string,
  inspection: TagStorageInspection<CustomTagCatalogState>,
  createId: () => string,
): { result: CustomTagCreateResult; nextCatalog: CustomTagCatalogState } {
  assertCatalogMutable(inspection);
  const name = validateCustomName(value);
  const index = catalogIndex(inspection.value);
  const existing = index.byName.get(duplicateKey(name));
  if (existing) return {
    result: { tag: existing, created: false },
    nextCatalog: inspection.value,
  };

  const occupied = new Set(index.byId.keys());
  let id = "";
  for (let attempt = 0; attempt < 16; attempt += 1) {
    const candidate = createId();
    if (typeof candidate !== "string" || utf8Bytes(candidate) > MAX_STORED_STRING_BYTES
      || !CUSTOM_ID_PATTERN.test(candidate)) {
      throw new Error("无法生成安全的标签 ID，请稍后重试");
    }
    if (!occupied.has(candidate)) {
      id = candidate;
      break;
    }
  }
  if (!id) throw new Error("无法生成唯一的标签 ID，请稍后重试");
  if (inspection.value.items.length >= MAX_CUSTOM_TAG_COUNT) throw new Error("自定义标签数量已达到上限");
  const item: CustomTagCatalogItem = { id, name, kind: "custom" };
  return {
    result: { tag: customTagDefinition(item), created: true },
    nextCatalog: { version: CUSTOM_TAG_CATALOG_VERSION, items: [...inspection.value.items, item] },
  };
}

export function inspectCustomTagCatalog(): TagStorageInspection<CustomTagCatalogState> {
  return parseCatalogRaw(readRaw(CUSTOM_TAG_CATALOG_KEY));
}

export function loadCustomTagCatalog(): CustomTagCatalogState {
  return inspectCustomTagCatalog().value;
}

export function inspectPageTagState(pageKey: PageKey): TagStorageInspection<PageTagState> {
  const catalog = inspectCustomTagCatalog();
  const index = catalogIndex(catalog.value);
  return parseStoredPageRaw(readRaw(keyFor(pageKey)), index);
}

export function resolveTag(id: string): TagDefinition | undefined {
  const builtIn = getTag(id);
  if (builtIn) return builtIn;
  const custom = loadCustomTagCatalog().items.find((tag) => tag.id === id);
  return custom ? customTagDefinition(custom) : undefined;
}

export function loadPageTagState(pageKey: PageKey): PageTagState {
  const inspection = inspectPageTagState(pageKey);
  if (inspection.status === "legacy") {
    try {
      writeRawVerified(keyFor(pageKey), JSON.stringify(inspection.value));
    } catch {
      throw new Error("旧版页面标签迁移保存失败，请检查浏览器存储空间");
    }
  }
  return inspection.value;
}

export function savePageTagState(pageKey: PageKey, state: PageTagStateInput): PageTagState {
  const catalog = inspectCustomTagCatalog();
  assertCatalogMutable(catalog);
  const current = parseStoredPageRaw(readRaw(keyFor(pageKey)), catalogIndex(catalog.value));
  assertPageMutable(current);
  const normalized = normalizePageInput(state, catalogIndex(catalog.value));
  writeRawVerified(keyFor(pageKey), JSON.stringify(normalized));
  return normalized;
}

export function createOrReuseCustomTag(
  value: string,
  createId: () => string = secureCustomId,
): CustomTagCreateResult {
  const inspection = inspectCustomTagCatalog();
  const candidate = selectCustomCandidate(value, inspection, createId);
  if (candidate.result.created) {
    writeRawVerified(CUSTOM_TAG_CATALOG_KEY, JSON.stringify(candidate.nextCatalog));
  }
  return candidate.result;
}

export function createAndSelectTag(
  pageKey: PageKey,
  value: string,
  currentOrder: string[],
  createId: () => string = secureCustomId,
): CustomTagCreateResult & { state: PageTagState } {
  const catalog = inspectCustomTagCatalog();
  assertCatalogMutable(catalog);
  const pageRaw = readRaw(keyFor(pageKey));
  const page = parseStoredPageRaw(pageRaw, catalogIndex(catalog.value));
  assertPageMutable(page);
  const candidate = selectCustomCandidate(value, catalog, createId);
  const nextIndex = catalogIndex(candidate.nextCatalog);
  const order = [candidate.result.tag.id, ...currentOrder.filter((id) => id !== candidate.result.tag.id)];
  const nextPage = normalizePageInput({ ids: order, activeId: candidate.result.tag.id, order }, nextIndex);
  let catalogWriteAttempted = false;
  let pageWriteAttempted = false;
  try {
    if (candidate.result.created) {
      catalogWriteAttempted = true;
      writeRawVerified(CUSTOM_TAG_CATALOG_KEY, JSON.stringify(candidate.nextCatalog));
    }
    pageWriteAttempted = true;
    writeRawVerified(keyFor(pageKey), JSON.stringify(nextPage));
    return { ...candidate.result, state: nextPage };
  } catch {
    let pageRestored = !pageWriteAttempted;
    if (pageWriteAttempted) {
      try {
        restoreRawVerified(keyFor(pageKey), pageRaw);
        pageRestored = true;
      } catch {
        pageRestored = false;
      }
    }
    if (candidate.result.created && catalogWriteAttempted && pageRestored) {
      try {
        restoreRawVerified(CUSTOM_TAG_CATALOG_KEY, catalog.raw);
      } catch {
        // An orphan catalog item is safe; never remove it while a partial page reference may remain.
      }
    }
    throw new Error("标签创建未能完整保存，请检查浏览器存储空间后重试");
  }
}

export function deleteCustomTag(id: string): boolean {
  const catalog = inspectCustomTagCatalog();
  assertCatalogMutable(catalog);
  if (!catalog.value.items.some((tag) => tag.id === id)) return false;
  const index = catalogIndex(catalog.value);
  const pages = PAGE_KEYS.map((pageKey) => {
    const raw = readRaw(keyFor(pageKey));
    const inspection = parseStoredPageRaw(raw, index);
    assertPageMutable(inspection);
    const order = inspection.value.order.filter((tagId) => tagId !== id);
    return {
      pageKey,
      raw,
      changed: order.length !== inspection.value.order.length,
      next: normalizePageInput({
        ids: order,
        order,
        activeId: inspection.value.activeId === id ? order[0] || "" : inspection.value.activeId,
      }, index),
    };
  });
  const nextCatalog: CustomTagCatalogState = {
    version: CUSTOM_TAG_CATALOG_VERSION,
    items: catalog.value.items.filter((tag) => tag.id !== id),
  };
  const attemptedPages: typeof pages = [];
  let catalogWriteAttempted = false;
  try {
    for (const page of pages) {
      if (!page.changed) continue;
      attemptedPages.push(page);
      writeRawVerified(keyFor(page.pageKey), JSON.stringify(page.next));
    }
    catalogWriteAttempted = true;
    writeRawVerified(CUSTOM_TAG_CATALOG_KEY, JSON.stringify(nextCatalog));
    return true;
  } catch {
    let catalogPresent = !catalogWriteAttempted;
    if (catalogWriteAttempted) {
      try {
        restoreRawVerified(CUSTOM_TAG_CATALOG_KEY, catalog.raw);
        catalogPresent = true;
      } catch {
        catalogPresent = false;
      }
    }
    if (catalogPresent) {
      for (const page of [...attemptedPages].reverse()) {
        try {
          restoreRawVerified(keyFor(page.pageKey), page.raw);
        } catch {
          // The original catalog still exists, so either page version cannot dangle.
        }
      }
    }
    throw new Error("标签删除未能完整保存，请检查浏览器存储空间后重试");
  }
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

export function moveTagByOffset(ids: string[], id: string, offset: -1 | 1): string[] {
  const index = ids.indexOf(id);
  const destination = index + offset;
  if (index < 0 || destination < 0 || destination >= ids.length) return [...ids];
  const next = [...ids];
  [next[index], next[destination]] = [next[destination], next[index]];
  return next;
}
