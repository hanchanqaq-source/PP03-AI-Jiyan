import {
  DEFAULT_TAG_IDS,
  loadPageTagState,
  moveTag,
  savePageTagState,
} from "../preferences";

describe("page tag preferences", () => {
  beforeEach(() => localStorage.clear());

  it("stores market and industry selections under independent keys", () => {
    savePageTagState("market_news", { ids: ["storage"], activeId: "storage" });
    savePageTagState("industry_research", { ids: ["robotics"], activeId: "robotics" });

    expect(loadPageTagState("market_news")).toEqual({ ids: ["storage"], activeId: "storage" });
    expect(loadPageTagState("industry_research")).toEqual({ ids: ["robotics"], activeId: "robotics" });
  });

  it("falls back to the three demonstrator tags when stored JSON is malformed", () => {
    localStorage.setItem("vr-page-tags:market_news", "{broken");

    expect(loadPageTagState("market_news")).toEqual({
      ids: DEFAULT_TAG_IDS,
      activeId: "storage",
    });
  });

  it("moves a dragged tag before its drop target without losing tags", () => {
    expect(moveTag(["semiconductor", "storage", "robotics"], "robotics", "semiconductor"))
      .toEqual(["robotics", "semiconductor", "storage"]);
  });

  it("normalizes an active tag that is no longer selected", () => {
    savePageTagState("market_news", { ids: ["storage"], activeId: "robotics" });

    expect(loadPageTagState("market_news")).toEqual({ ids: ["storage"], activeId: "storage" });
  });
});
