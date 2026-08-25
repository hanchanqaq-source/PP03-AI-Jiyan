import {
  CUSTOM_TAG_CATALOG_KEY,
  MAX_CUSTOM_TAG_COUNT,
  MAX_PAGE_TAG_COUNT,
  MAX_TAG_STORAGE_BYTES,
  PAGE_TAG_STATE_VERSION,
  DEFAULT_TAG_IDS,
  createAndSelectTag,
  createOrReuseCustomTag,
  deleteCustomTag,
  inspectCustomTagCatalog,
  inspectPageTagState,
  loadCustomTagCatalog,
  loadPageTagState,
  moveTag,
  resolveTag,
  savePageTagState,
} from "../preferences";
import { vi } from "vitest";

describe("page tag preferences", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("stores market and industry selections under independent keys", () => {
    savePageTagState("market_news", { ids: ["storage"], activeId: "storage", order: ["storage"] });
    savePageTagState("industry_research", { ids: ["robotics"], activeId: "robotics", order: ["robotics"] });

    expect(loadPageTagState("market_news")).toEqual({
      version: PAGE_TAG_STATE_VERSION, ids: ["storage"], activeId: "storage", order: ["storage"],
    });
    expect(loadPageTagState("industry_research")).toEqual({
      version: PAGE_TAG_STATE_VERSION, ids: ["robotics"], activeId: "robotics", order: ["robotics"],
    });
  });

  it("migrates the actual legacy ids/activeId shape without changing order or the active tag", () => {
    localStorage.setItem("vr-page-tags:industry_research", JSON.stringify({
      ids: ["robotics", "storage", "semiconductor"],
      activeId: "storage",
    }));

    expect(loadPageTagState("industry_research")).toEqual({
      version: PAGE_TAG_STATE_VERSION,
      ids: ["robotics", "storage", "semiconductor"],
      activeId: "storage",
      order: ["robotics", "storage", "semiconductor"],
    });
    expect(JSON.parse(localStorage.getItem("vr-page-tags:industry_research")!)).toEqual({
      version: PAGE_TAG_STATE_VERSION,
      ids: ["robotics", "storage", "semiconductor"],
      activeId: "storage",
      order: ["robotics", "storage", "semiconductor"],
    });
  });

  it("falls back without overwriting corrupt, future or oversized page records", () => {
    const corrupt = "{broken";
    const future = JSON.stringify({ version: 99, ids: ["storage"], activeId: "storage", order: ["storage"] });
    const oversized = `{"padding":"${"界".repeat(MAX_TAG_STORAGE_BYTES)}"}`;
    localStorage.setItem("vr-page-tags:market_news", corrupt);

    expect(loadPageTagState("market_news")).toEqual({
      version: PAGE_TAG_STATE_VERSION,
      ids: DEFAULT_TAG_IDS,
      activeId: "storage",
      order: DEFAULT_TAG_IDS,
    });
    expect(inspectPageTagState("market_news").status).toBe("corrupt");
    expect(localStorage.getItem("vr-page-tags:market_news")).toBe(corrupt);

    localStorage.setItem("vr-page-tags:market_news", future);
    expect(inspectPageTagState("market_news").status).toBe("unsupported");
    expect(loadPageTagState("market_news")).toMatchObject({ ids: DEFAULT_TAG_IDS });
    expect(localStorage.getItem("vr-page-tags:market_news")).toBe(future);

    localStorage.setItem("vr-page-tags:market_news", oversized);
    expect(inspectPageTagState("market_news").status).toBe("oversized");
    expect(loadPageTagState("market_news")).toMatchObject({ ids: DEFAULT_TAG_IDS });
    expect(localStorage.getItem("vr-page-tags:market_news")).toBe(oversized);
  });

  it("classifies only the exact known legacy shape as migratable", () => {
    const extraKey = JSON.stringify({ ids: ["storage"], activeId: "storage", extra: true });
    localStorage.setItem("vr-page-tags:market_news", extraKey);

    expect(inspectPageTagState("market_news").status).toBe("corrupt");
    expect(loadPageTagState("market_news")).toMatchObject({ ids: DEFAULT_TAG_IDS });
    expect(localStorage.getItem("vr-page-tags:market_news")).toBe(extraKey);
  });

  it("moves a dragged tag before its drop target without losing tags", () => {
    expect(moveTag(["semiconductor", "storage", "robotics"], "robotics", "semiconductor"))
      .toEqual(["robotics", "semiconductor", "storage"]);
  });

  it("normalizes an active tag that is no longer selected", () => {
    savePageTagState("market_news", { ids: ["storage"], activeId: "robotics", order: ["storage"] });

    expect(loadPageTagState("market_news")).toEqual({
      version: PAGE_TAG_STATE_VERSION, ids: ["storage"], activeId: "storage", order: ["storage"],
    });
  });

  it("persists a versioned shared catalog with content-free stable IDs", () => {
    const result = createOrReuseCustomTag("  量子传感  ", () => "custom-019d1234567890abcdef1234567890ab");

    expect(result).toMatchObject({ created: true, tag: { name: "量子传感", kind: "custom" } });
    expect(result.tag.id).toBe("custom-019d1234567890abcdef1234567890ab");
    expect(result.tag.id).not.toContain("量子传感");
    expect(loadCustomTagCatalog()).toEqual({
      version: 1,
      items: [{ id: result.tag.id, name: "量子传感", kind: "custom" }],
    });
    expect(JSON.parse(localStorage.getItem(CUSTOM_TAG_CATALOG_KEY)!)).toEqual(loadCustomTagCatalog());
    expect(resolveTag(result.tag.id)?.name).toBe("量子传感");
  });

  it("recovers valid catalog items in memory but blocks mutation of a mixed corrupt record", () => {
    const mixed = JSON.stringify({
      version: 1,
      items: [
        { id: "custom-valid", name: "量子传感", kind: "custom" },
        { id: "custom-bad", name: "测\n试", kind: "custom" },
      ],
    });
    localStorage.setItem(CUSTOM_TAG_CATALOG_KEY, mixed);

    expect(inspectCustomTagCatalog().status).toBe("corrupt");
    expect(loadCustomTagCatalog().items).toEqual([
      { id: "custom-valid", name: "量子传感", kind: "custom" },
    ]);
    expect(resolveTag("custom-valid")?.name).toBe("量子传感");
    expect(() => createOrReuseCustomTag("空天计算", () => "custom-new"))
      .toThrow("自定义标签存储已损坏，无法安全修改");
    expect(localStorage.getItem(CUSTOM_TAG_CATALOG_KEY)).toBe(mixed);
  });

  it("keeps corrupt, future and oversized catalog bytes untouched", () => {
    const records = [
      { raw: "{broken", status: "corrupt" },
      { raw: JSON.stringify({ version: 2, items: [] }), status: "unsupported" },
      { raw: `{"padding":"${"界".repeat(MAX_TAG_STORAGE_BYTES)}"}`, status: "oversized" },
      { raw: JSON.stringify({ version: 1, items: [], extra: true }), status: "corrupt" },
    ] as const;

    for (const record of records) {
      localStorage.setItem(CUSTOM_TAG_CATALOG_KEY, record.raw);
      expect(inspectCustomTagCatalog().status).toBe(record.status);
      expect(loadCustomTagCatalog().items).toEqual([]);
      expect(localStorage.getItem(CUSTOM_TAG_CATALOG_KEY)).toBe(record.raw);
    }
  });

  it("treats a persisted custom name that duplicates a built-in as corrupt", () => {
    const raw = JSON.stringify({
      version: 1,
      items: [{ id: "custom-duplicate", name: "  存储  ", kind: "custom" }],
    });
    localStorage.setItem(CUSTOM_TAG_CATALOG_KEY, raw);

    expect(inspectCustomTagCatalog()).toMatchObject({
      status: "corrupt",
      value: { items: [] },
    });
    expect(localStorage.getItem(CUSTOM_TAG_CATALOG_KEY)).toBe(raw);
  });

  it("classifies an over-bound page string as oversized before member normalization", () => {
    const oversizedId = `custom-${"a".repeat(257)}`;
    const raw = JSON.stringify({
      version: 2,
      ids: [oversizedId],
      activeId: oversizedId,
      order: [oversizedId],
    });
    localStorage.setItem("vr-page-tags:industry_research", raw);

    expect(inspectPageTagState("industry_research").status).toBe("oversized");
    expect(localStorage.getItem("vr-page-tags:industry_research")).toBe(raw);
  });

  it("bounds catalog and page collections before normalizing their members", () => {
    const catalogRaw = JSON.stringify({
      version: 1,
      items: Array.from({ length: MAX_CUSTOM_TAG_COUNT + 1 }, (_, index) => ({
        id: `custom-${index}`, name: `标签${index}`, kind: "custom",
      })),
    });
    localStorage.setItem(CUSTOM_TAG_CATALOG_KEY, catalogRaw);
    expect(inspectCustomTagCatalog().status).toBe("oversized");
    expect(localStorage.getItem(CUSTOM_TAG_CATALOG_KEY)).toBe(catalogRaw);

    localStorage.removeItem(CUSTOM_TAG_CATALOG_KEY);
    const pageRaw = JSON.stringify({
      version: 2,
      ids: Array.from({ length: MAX_PAGE_TAG_COUNT + 1 }, () => "storage"),
      activeId: "storage",
      order: ["storage"],
    });
    localStorage.setItem("vr-page-tags:market_news", pageRaw);
    expect(inspectPageTagState("market_news").status).toBe("oversized");
    expect(localStorage.getItem("vr-page-tags:market_news")).toBe(pageRaw);
  });

  it("parses the custom catalog once for a large bounded page state", () => {
    const items = Array.from({ length: 120 }, (_, index) => ({
      id: `custom-${index}`, name: `标签${index}`, kind: "custom" as const,
    }));
    localStorage.setItem(CUSTOM_TAG_CATALOG_KEY, JSON.stringify({ version: 1, items }));
    const ids = items.map((item) => item.id);
    localStorage.setItem("vr-page-tags:industry_research", JSON.stringify({
      version: 2, ids, activeId: ids[60], order: [...ids].reverse(),
    }));
    const reads = vi.spyOn(Storage.prototype, "getItem");

    expect(loadPageTagState("industry_research").ids).toEqual([...ids].reverse());
    expect(reads.mock.calls.filter(([key]) => key === CUSTOM_TAG_CATALOG_KEY)).toHaveLength(1);
  });

  it("refuses to overwrite a corrupt current page record", () => {
    const raw = "{broken";
    localStorage.setItem("vr-page-tags:market_news", raw);

    expect(() => savePageTagState("market_news", {
      ids: ["storage"], activeId: "storage", order: ["storage"],
    })).toThrow("页面标签存储已损坏，无法安全修改");
    expect(localStorage.getItem("vr-page-tags:market_news")).toBe(raw);
  });

  it("reuses normalized built-in and custom names instead of creating duplicates", () => {
    const builtIn = createOrReuseCustomTag("  存储  ", () => "custom-unused");
    const first = createOrReuseCustomTag("量子   传感", () => "custom-a");
    const repeated = createOrReuseCustomTag("  量子 传感  ", () => "custom-b");

    expect(builtIn).toMatchObject({ created: false, tag: { id: "storage", name: "存储" } });
    expect(first).toMatchObject({ created: true, tag: { id: "custom-a", name: "量子 传感" } });
    expect(repeated).toMatchObject({ created: false, tag: { id: "custom-a", name: "量子 传感" } });
    expect(loadCustomTagCatalog().items).toHaveLength(1);
  });

  it("validates user-perceived length and rejects blank, control and invisible input", () => {
    expect(() => createOrReuseCustomTag("   ")).toThrow("请输入标签名称");
    expect(() => createOrReuseCustomTag("测\n试")).toThrow("不能包含控制字符或不可见字符");
    expect(() => createOrReuseCustomTag("测\u200B试")).toThrow("不能包含控制字符或不可见字符");
    expect(() => createOrReuseCustomTag("研".repeat(33))).toThrow("不能超过 32 个字符");

    const composed = createOrReuseCustomTag("e\u0301".repeat(32), () => "custom-graphemes");
    expect(composed.created).toBe(true);
  });

  it("retries colliding generated IDs without overwriting an existing custom tag", () => {
    createOrReuseCustomTag("量子传感", () => "custom-collision");
    const ids = ["custom-collision", "custom-next"];

    const result = createOrReuseCustomTag("空天计算", () => ids.shift()!);

    expect(result.tag.id).toBe("custom-next");
    expect(loadCustomTagCatalog().items.map((tag) => tag.id)).toEqual(["custom-collision", "custom-next"]);
  });

  it("deletes a shared custom item from every page state without deleting built-ins", () => {
    const custom = createOrReuseCustomTag("量子传感", () => "custom-shared").tag;
    savePageTagState("market_news", {
      ids: [custom.id, "storage"], activeId: custom.id, order: [custom.id, "storage"],
    });
    savePageTagState("industry_research", {
      ids: ["robotics", custom.id], activeId: custom.id, order: ["robotics", custom.id],
    });

    expect(deleteCustomTag(custom.id)).toBe(true);
    expect(loadCustomTagCatalog().items).toEqual([]);
    expect(loadPageTagState("market_news")).toMatchObject({
      ids: ["storage"], activeId: "storage", order: ["storage"],
    });
    expect(loadPageTagState("industry_research")).toMatchObject({
      ids: ["robotics"], activeId: "robotics", order: ["robotics"],
    });

    expect(deleteCustomTag("storage")).toBe(false);
    expect(resolveTag("storage")?.name).toBe("存储");
  });

  it("rolls back a partially failed create without leaving an orphan page reference", () => {
    const originalSet = Storage.prototype.setItem;
    let pageWriteFailed = false;
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "vr-page-tags:industry_research" && !pageWriteFailed) {
        pageWriteFailed = true;
        throw new DOMException("quota", "QuotaExceededError");
      }
      return originalSet.call(this, key, value);
    });

    expect(() => createAndSelectTag(
      "industry_research", "量子传感", ["storage"], () => "custom-transaction",
    )).toThrow("标签创建未能完整保存");
    write.mockRestore();

    expect(loadCustomTagCatalog().items).toEqual([]);
    expect(loadPageTagState("industry_research").ids).not.toContain("custom-transaction");
  });

  it("preserves the created catalog item when page rollback also fails after a partial write", () => {
    savePageTagState("industry_research", {
      ids: ["storage"], activeId: "storage", order: ["storage"],
    });
    const originalSet = Storage.prototype.setItem;
    let pageWrites = 0;
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "vr-page-tags:industry_research") {
        pageWrites += 1;
        if (pageWrites === 1) {
          originalSet.call(this, key, value);
          throw new DOMException("post-write failure", "QuotaExceededError");
        }
        throw new DOMException("rollback failure", "QuotaExceededError");
      }
      return originalSet.call(this, key, value);
    });

    expect(() => createAndSelectTag(
      "industry_research", "量子传感", ["storage"], () => "custom-safe-partial",
    )).toThrow("标签创建未能完整保存");
    write.mockRestore();

    expect(loadCustomTagCatalog().items.map((item) => item.id)).toContain("custom-safe-partial");
    expect(loadPageTagState("industry_research").ids).toContain("custom-safe-partial");
  });

  it("restores page snapshots when catalog deletion fails", () => {
    const custom = createOrReuseCustomTag("量子传感", () => "custom-delete-rollback").tag;
    savePageTagState("market_news", {
      ids: [custom.id, "storage"], activeId: custom.id, order: [custom.id, "storage"],
    });
    savePageTagState("industry_research", {
      ids: ["robotics", custom.id], activeId: custom.id, order: ["robotics", custom.id],
    });
    const beforeCatalog = localStorage.getItem(CUSTOM_TAG_CATALOG_KEY);
    const beforeMarket = localStorage.getItem("vr-page-tags:market_news");
    const beforeIndustry = localStorage.getItem("vr-page-tags:industry_research");
    const originalSet = Storage.prototype.setItem;
    let failed = false;
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === CUSTOM_TAG_CATALOG_KEY && !failed) {
        failed = true;
        throw new DOMException("quota", "QuotaExceededError");
      }
      return originalSet.call(this, key, value);
    });

    expect(() => deleteCustomTag(custom.id)).toThrow("标签删除未能完整保存");
    write.mockRestore();

    expect(localStorage.getItem(CUSTOM_TAG_CATALOG_KEY)).toBe(beforeCatalog);
    expect(localStorage.getItem("vr-page-tags:market_news")).toBe(beforeMarket);
    expect(localStorage.getItem("vr-page-tags:industry_research")).toBe(beforeIndustry);
  });

  it("keeps every page free of dangling references when deletion rollback cannot restore the catalog", () => {
    const custom = createOrReuseCustomTag("量子传感", () => "custom-delete-safe").tag;
    savePageTagState("market_news", {
      ids: [custom.id, "storage"], activeId: custom.id, order: [custom.id, "storage"],
    });
    savePageTagState("industry_research", {
      ids: ["robotics", custom.id], activeId: custom.id, order: ["robotics", custom.id],
    });
    const originalSet = Storage.prototype.setItem;
    let catalogWrites = 0;
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === CUSTOM_TAG_CATALOG_KEY) {
        catalogWrites += 1;
        if (catalogWrites === 1) {
          originalSet.call(this, key, value);
          throw new DOMException("post-write failure", "QuotaExceededError");
        }
        throw new DOMException("rollback failure", "QuotaExceededError");
      }
      return originalSet.call(this, key, value);
    });

    expect(() => deleteCustomTag(custom.id)).toThrow("标签删除未能完整保存");
    write.mockRestore();

    expect(loadCustomTagCatalog().items).toEqual([]);
    expect(loadPageTagState("market_news").ids).not.toContain(custom.id);
    expect(loadPageTagState("industry_research").ids).not.toContain(custom.id);
  });

  it("fails closed on corrupt catalog/page records without overwriting either raw value", () => {
    localStorage.setItem(CUSTOM_TAG_CATALOG_KEY, "{broken");
    localStorage.setItem("vr-page-tags:industry_research", JSON.stringify({
      version: PAGE_TAG_STATE_VERSION,
      ids: ["custom-missing", "storage"],
      activeId: "custom-missing",
      order: ["custom-missing", "storage"],
    }));

    expect(loadCustomTagCatalog()).toEqual({ version: 1, items: [] });
    expect(loadPageTagState("industry_research")).toEqual({
      version: PAGE_TAG_STATE_VERSION, ids: ["storage"], activeId: "storage", order: ["storage"],
    });
    expect(localStorage.getItem(CUSTOM_TAG_CATALOG_KEY)).toBe("{broken");
    expect(JSON.parse(localStorage.getItem("vr-page-tags:industry_research")!)).toMatchObject({
      ids: ["custom-missing", "storage"],
    });
  });
});
