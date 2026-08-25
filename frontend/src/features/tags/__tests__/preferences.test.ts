import {
  CUSTOM_TAG_CATALOG_KEY,
  PAGE_TAG_STATE_VERSION,
  DEFAULT_TAG_IDS,
  createOrReuseCustomTag,
  deleteCustomTag,
  loadCustomTagCatalog,
  loadPageTagState,
  moveTag,
  resolveTag,
  savePageTagState,
} from "../preferences";

describe("page tag preferences", () => {
  beforeEach(() => localStorage.clear());

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

  it("falls back to the three demonstrator tags when stored JSON is malformed", () => {
    localStorage.setItem("vr-page-tags:market_news", "{broken");

    expect(loadPageTagState("market_news")).toEqual({
      version: PAGE_TAG_STATE_VERSION,
      ids: DEFAULT_TAG_IDS,
      activeId: "storage",
      order: DEFAULT_TAG_IDS,
    });
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

  it("fails closed on corrupt catalog/page records and removes dangling custom references", () => {
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
  });
});
