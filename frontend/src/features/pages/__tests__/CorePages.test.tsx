import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { api, type RadarData } from "@/lib/api";
import { directEvent, marketNewsResponse, translatedEnglishEvent } from "@/features/market-news/__tests__/fixtures";
import type { MarketNewsEvent, MarketNewsQuery, MarketNewsResponse, MarketNewsTranslationResponse } from "@/features/market-news/types";
import { cacheMarketNewsResponse, MarketNews, readMarketNewsCache } from "@/pages/MarketNews";
import { IndustryResearch } from "@/pages/IndustryResearch";

const now = Math.floor(Date.now() / 1000);
const radar: RadarData = {
  generated_at: "2026-08-16 12:00", recent_days: 7,
  stats: { industries: 2, total_sources: 2 },
  industries: [
    { key: "semi", name: "半导体", accent: "#22d3ee", total: 1, items: [
      { title: "HBM存储需求跟踪", url: "https://example.com/hbm", time: "08-16 10:00", ts: now, source: "半导体资讯", summary: "HBM需求" },
    ] },
    { key: "robot", name: "机器人", accent: "#14b8a6", total: 1, items: [
      { title: "机器人量产进度更新", url: "https://example.com/robot", time: "08-16 09:00", ts: now, source: "机器人资讯", summary: "机器人量产" },
    ] },
  ],
};

function queryFor(tagId: string, mode: MarketNewsQuery["mode"] = "my_focus"): MarketNewsQuery {
  return { mode, tag_ids: [tagId], category: "all", days: 7, sort: "importance" };
}

function eventFor(id: string, title: string, tagId: string, tagName: string): MarketNewsEvent {
  return {
    ...directEvent,
    event_id: id,
    title,
    related_tags: [{ id: tagId, name: tagName }],
    tag_evidence: [{ id: tagId, name: tagName, provenance: "article_text" }],
  };
}

function responseFor(
  query: MarketNewsQuery,
  event: MarketNewsEvent,
  snapshotId: string,
  holdingRelatedCount = 1,
): MarketNewsResponse {
  return {
    ...marketNewsResponse,
    events: [event],
    focus_events: [event],
    snapshot_id: snapshotId,
    filters: query,
    impact_summary: {
      holding_related_count: holdingRelatedCount,
      direct_count: holdingRelatedCount,
      industry_count: 0,
      watch_count: 0,
      funds: holdingRelatedCount ? [{ fund_code: "017811", fund_name: "测试基金", event_count: holdingRelatedCount }] : [],
    },
  };
}

describe("PP03 core pages", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "radar").mockResolvedValue(radar);
    vi.spyOn(api, "fundPortfolio").mockResolvedValue({ schema_version: 2, holdings: [], total_cost: 0, updated: null, migration: null, data_status: "ok" });
  });

  it("keeps active filters while switching four market-news modes and tags", async () => {
    const user = userEvent.setup();
    const load = vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [directEvent] });
    render(<MarketNews />);

    expect(await screen.findByRole("heading", { name: directEvent.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "我的关注" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "全部" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "7天" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重要度" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "政策" }));
    await user.click(screen.getByRole("button", { name: "30天" }));
    await user.click(screen.getByRole("button", { name: "最新时间" }));
    await user.click(screen.getByRole("button", { name: "我的持仓" }));
    await user.click(screen.getByRole("button", { name: "全球科技" }));
    await user.click(screen.getByRole("button", { name: "国内政策" }));
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));

    expect(load).toHaveBeenLastCalledWith({ mode: "domestic_policy", tag_ids: ["robotics"], category: "policy", days: 30, sort: "latest" });
  });

  it("translates only visible English events and updates list plus open detail on the same snapshot", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-llm", JSON.stringify({
      provider: "openai", baseURL: "https://model.example.test/v1", apiKey: "request-only", model: "test-model",
    }));
    const englishEvent = {
      ...translatedEnglishEvent,
      translated_title_zh: undefined,
      translated_summary_zh: undefined,
      translation_status: undefined,
      translation_provider: undefined,
      translated_at: undefined,
    };
    let resolveTranslations!: (value: { translations: Array<Record<string, unknown>>; limit: number }) => void;
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue(responseFor(queryFor("storage"), englishEvent, "translation-snapshot"));
    const translate = vi.spyOn(api as any, "marketNewsTranslations").mockImplementation(
      () => new Promise((resolve) => { resolveTranslations = resolve; }),
    );

    render(<MarketNews />);
    await screen.findByRole("heading", { name: englishEvent.title });
    await user.click(screen.getByRole("button", { name: `查看事件详情 ${englishEvent.title}` }));

    expect(translate).toHaveBeenCalledWith({
      items: [{
        event_id: englishEvent.event_id,
        title: englishEvent.title,
        summary: englishEvent.summary,
        source_language: "en",
      }],
      llm: { provider: "openai", baseURL: "https://model.example.test/v1", apiKey: "request-only", model: "test-model" },
    });

    resolveTranslations({ translations: [{
      event_id: englishEvent.event_id,
      translated_title_zh: "美光（Micron）发布 HBM3E",
      translated_summary_zh: "本季度开始出货。",
      translation_status: "translated",
      translation_provider: "openai",
      translated_at: "2026-08-17T04:00:00+00:00",
    }], limit: 20 });

    expect((await screen.findAllByRole("heading", { name: "美光（Micron）发布 HBM3E" })).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("dialog", { name: "美光（Micron）发布 HBM3E事件详情" })).toBeInTheDocument();
    expect(screen.getByText("过去 7 天有 1 个事件与你的持仓相关")).toBeInTheDocument();
  });

  it("keeps English originals available without a model and does not call translation", async () => {
    const englishEvent = {
      ...translatedEnglishEvent,
      translated_title_zh: undefined,
      translated_summary_zh: undefined,
      translation_status: undefined,
      translation_provider: undefined,
      translated_at: undefined,
    };
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue(responseFor(queryFor("storage"), englishEvent, "no-model-snapshot"));
    const translate = vi.spyOn(api as any, "marketNewsTranslations").mockResolvedValue({ translations: [], limit: 20 });

    render(<MarketNews />);

    expect(await screen.findByRole("heading", { name: englishEvent.title })).toBeInTheDocument();
    expect(await screen.findByText("中文翻译暂不可用")).toBeInTheDocument();
    expect(translate).not.toHaveBeenCalled();
  });

  it("ignores a delayed translation from an older query snapshot", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-llm", JSON.stringify({
      provider: "openai", baseURL: "https://model.example.test/v1", apiKey: "request-only", model: "test-model",
    }));
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["storage", "robotics"], activeId: "storage" }));
    const storage = { ...translatedEnglishEvent, event_id: "11111111111111111111", title: "Storage source title", translated_title_zh: undefined, translated_summary_zh: undefined, translation_status: undefined };
    const robotics = { ...translatedEnglishEvent, event_id: "22222222222222222222", title: "Robotics source title", translated_title_zh: undefined, translated_summary_zh: undefined, translation_status: undefined };
    vi.spyOn(api, "marketNewsEvents").mockImplementation((query) => Promise.resolve(
      query.tag_ids[0] === "storage"
        ? responseFor(queryFor("storage"), storage, "storage-translation-snapshot")
        : responseFor(queryFor("robotics"), robotics, "robotics-translation-snapshot"),
    ));
    let resolveStorage!: (value: MarketNewsTranslationResponse) => void;
    vi.spyOn(api, "marketNewsTranslations").mockImplementation(({ items }) => {
      if (items[0].event_id === storage.event_id) return new Promise((resolve) => { resolveStorage = resolve; });
      return Promise.resolve<MarketNewsTranslationResponse>({ translations: [{ event_id: robotics.event_id, translated_title_zh: "机器人中文标题", translated_summary_zh: "机器人摘要", translation_status: "translated", translation_provider: "openai", translated_at: "2026-08-17T04:01:00+00:00" }], limit: 20 });
    });

    render(<MarketNews />);
    await screen.findByRole("heading", { name: storage.title });
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(await screen.findByRole("heading", { name: "机器人中文标题" })).toBeInTheDocument();

    resolveStorage({ translations: [{ event_id: storage.event_id, translated_title_zh: "迟到的存储中文标题", translated_summary_zh: "迟到摘要", translation_status: "translated", translation_provider: "openai", translated_at: "2026-08-17T04:02:00+00:00" }], limit: 20 });
    await waitFor(() => expect(screen.queryByRole("heading", { name: "迟到的存储中文标题" })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: "机器人中文标题" })).toBeInTheDocument();
  });

  it("preserves a translated same snapshot across an A to B to A reload", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-llm", JSON.stringify({
      provider: "openai", baseURL: "https://model.example.test/v1", apiKey: "request-only", model: "test-model",
    }));
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["storage", "robotics"], activeId: "storage" }));
    const storage = {
      ...translatedEnglishEvent,
      event_id: "33333333333333333333",
      title: "Storage source title",
      translated_title_zh: undefined,
      translated_summary_zh: undefined,
      translation_status: undefined,
    };
    const robotics = eventFor("44444444444444444444", "机器人原始中文标题", "robotics", "机器人");
    vi.spyOn(api, "marketNewsEvents").mockImplementation((query) => Promise.resolve(
      query.tag_ids[0] === "storage"
        ? responseFor(queryFor("storage"), storage, "stable-storage-snapshot")
        : responseFor(queryFor("robotics"), robotics, "robotics-snapshot"),
    ));
    const translate = vi.spyOn(api, "marketNewsTranslations").mockResolvedValue({
      translations: [{
        event_id: storage.event_id,
        translated_title_zh: "稳定的存储中文标题",
        translated_summary_zh: "稳定的中文摘要",
        translation_status: "translated",
        translation_provider: "openai",
        translated_at: "2026-08-17T04:03:00+00:00",
      }],
      limit: 20,
    });

    render(<MarketNews />);
    expect(await screen.findByRole("heading", { name: "稳定的存储中文标题" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(await screen.findByRole("heading", { name: "机器人原始中文标题" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到存储" }));

    expect(await screen.findByRole("heading", { name: "稳定的存储中文标题" })).toBeInTheDocument();
    expect(translate).toHaveBeenCalledTimes(1);
  });

  it("caps one translation batch at twenty visible English events", async () => {
    localStorage.setItem("vr-llm", JSON.stringify({
      provider: "openai", baseURL: "https://model.example.test/v1", apiKey: "request-only", model: "test-model",
    }));
    const events = Array.from({ length: 22 }, (_, index) => ({
      ...translatedEnglishEvent,
      event_id: index.toString(16).padStart(20, "0"),
      title: `English event ${index}`,
      translated_title_zh: undefined,
      translated_summary_zh: undefined,
      translation_status: undefined,
    }));
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({
      ...marketNewsResponse,
      events,
      focus_events: events.slice(0, 5),
      snapshot_id: "twenty-event-snapshot",
      filters: queryFor("storage"),
    });
    const translate = vi.spyOn(api, "marketNewsTranslations").mockResolvedValue({ translations: [], limit: 20 });

    render(<MarketNews />);

    await waitFor(() => expect(translate).toHaveBeenCalled());
    const payload = translate.mock.calls[0][0];
    expect(payload.items).toHaveLength(20);
    expect(payload.items.map((item) => item.event_id)).toEqual(events.slice(0, 20).map((event) => event.event_id));
  });

  it("bounds the full market-news response cache with LRU eviction", () => {
    const cache = new Map<string, MarketNewsResponse>();
    for (let index = 0; index < 12; index += 1) {
      cacheMarketNewsResponse(cache, `query-${index}`, marketNewsResponse);
    }

    expect(cache).toHaveLength(12);
    expect(readMarketNewsCache(cache, "query-0")).toBe(marketNewsResponse);
    cacheMarketNewsResponse(cache, "query-12", marketNewsResponse);
    expect(cache.has("query-0")).toBe(true);
    expect(cache.has("query-1")).toBe(false);
  });

  it("preserves only the same query's last successful response on refresh failure", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [directEvent] });
    let rejectRefresh!: (reason: Error) => void;
    vi.spyOn(api, "marketNewsRefresh").mockImplementation(() => new Promise((_, reject) => { rejectRefresh = reject; }));
    render(<MarketNews />);
    expect(await screen.findByRole("heading", { name: directEvent.title })).toBeInTheDocument();

    const refresh = screen.getByRole("button", { name: "刷新资讯" });
    await user.click(refresh);
    expect(refresh).toBeDisabled();
    expect(screen.getByText("刷新中")).toBeInTheDocument();
    rejectRefresh(new Error("offline"));

    expect(await screen.findByText("当前筛选加载失败，继续显示该筛选上次成功结果。")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: directEvent.title })).toBeInTheDocument();
  });

  it("does not show a semiconductor response when a new storage query fails", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["semiconductor", "storage"], activeId: "semiconductor" }));
    const semiconductorEvent = eventFor("semi-query-event", "半导体筛选成功事件", "semiconductor", "半导体");
    const semiconductorQuery = queryFor("semiconductor");
    vi.spyOn(api, "marketNewsEvents").mockImplementation((query) => {
      if (query.tag_ids[0] === "semiconductor") {
        return Promise.resolve(responseFor(semiconductorQuery, semiconductorEvent, "semi-snapshot"));
      }
      return Promise.reject(new Error("storage query offline"));
    });

    render(<MarketNews />);
    expect(await screen.findByRole("heading", { name: semiconductorEvent.title })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到存储" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("当前筛选加载失败，请稍后重试。");
    expect(screen.getByText("当前筛选加载失败")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: semiconductorEvent.title })).not.toBeInTheDocument();
  });

  it("rejects a response whose filters do not match the current query", async () => {
    const mismatchedEvent = eventFor("mismatch-event", "错误标签响应事件", "semiconductor", "半导体");
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue(
      responseFor(queryFor("semiconductor"), mismatchedEvent, "mismatch-snapshot"),
    );

    render(<MarketNews />);

    expect(await screen.findByRole("alert")).toHaveTextContent("当前筛选加载失败，请稍后重试。");
    expect(screen.queryByRole("heading", { name: mismatchedEvent.title })).not.toBeInTheDocument();
  });

  it("keeps list sidebar impact and open detail on the same query snapshot", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["storage", "robotics"], activeId: "storage" }));
    const storageEvent = eventFor("storage-snapshot-event", "存储查询事件", "storage", "存储");
    const roboticsEvent = eventFor("robotics-snapshot-event", "机器人查询事件", "robotics", "机器人");
    vi.spyOn(api, "marketNewsEvents").mockImplementation((query) => Promise.resolve(
      query.tag_ids[0] === "storage"
        ? responseFor(queryFor("storage"), storageEvent, "storage-snapshot", 1)
        : responseFor(queryFor("robotics"), roboticsEvent, "robotics-snapshot", 2),
    ));

    render(<MarketNews />);
    expect(await screen.findByRole("heading", { name: storageEvent.title })).toBeInTheDocument();
    expect(screen.getAllByText(storageEvent.title)).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: `查看事件详情 ${storageEvent.title}` }));
    expect(screen.getByRole("dialog", { name: `${storageEvent.title}事件详情` })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换到机器人" }));

    expect(await screen.findByRole("heading", { name: roboticsEvent.title })).toBeInTheDocument();
    expect(screen.getByText("过去 7 天有 2 个事件与你的持仓相关")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: `${storageEvent.title}事件详情` })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: storageEvent.title })).not.toBeInTheDocument();
  });

  it("does not reopen an old detail when returning to a query with a new snapshot", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["storage", "robotics"], activeId: "storage" }));
    const storageOld = eventFor("storage-old-event", "存储旧快照事件", "storage", "存储");
    const storageNew = eventFor("storage-new-event", "存储新快照事件", "storage", "存储");
    const roboticsEvent = eventFor("robotics-between-event", "机器人中间查询", "robotics", "机器人");
    let storageCalls = 0;
    vi.spyOn(api, "marketNewsEvents").mockImplementation((query) => {
      if (query.tag_ids[0] === "robotics") {
        return Promise.resolve(responseFor(queryFor("robotics"), roboticsEvent, "robotics-between-snapshot"));
      }
      storageCalls += 1;
      return Promise.resolve(storageCalls === 1
        ? responseFor(queryFor("storage"), storageOld, "storage-old-snapshot")
        : responseFor(queryFor("storage"), storageNew, "storage-new-snapshot"));
    });

    render(<MarketNews />);
    await screen.findByRole("heading", { name: storageOld.title });
    await user.click(screen.getByRole("button", { name: `查看事件详情 ${storageOld.title}` }));
    expect(screen.getByRole("dialog", { name: `${storageOld.title}事件详情` })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    await screen.findByRole("heading", { name: roboticsEvent.title });
    await user.click(screen.getByRole("button", { name: "切换到存储" }));

    expect(screen.queryByRole("dialog", { name: `${storageOld.title}事件详情` })).not.toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: storageNew.title })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not let an older tag response overwrite the newest tag query", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["storage", "semiconductor", "robotics"], activeId: "storage" }));
    const storageEvent = eventFor("storage-event", "存储初始事件", "storage", "存储");
    const semiconductorEvent = eventFor("semiconductor-event", "迟到的半导体事件", "semiconductor", "半导体");
    const roboticsEvent = eventFor("robotics-event", "最新机器人事件", "robotics", "机器人");
    let resolveSemiconductor!: (value: MarketNewsResponse) => void;
    vi.spyOn(api, "marketNewsEvents").mockImplementation((query) => {
      const tag = query.tag_ids[0];
      if (tag === "storage") return Promise.resolve(responseFor(queryFor("storage"), storageEvent, "storage-snapshot"));
      if (tag === "semiconductor") return new Promise((resolve) => { resolveSemiconductor = resolve; });
      return Promise.resolve(responseFor(queryFor("robotics"), roboticsEvent, "robotics-snapshot"));
    });

    render(<MarketNews />);
    await screen.findByRole("heading", { name: storageEvent.title });
    await user.click(screen.getByRole("button", { name: "切换到半导体" }));
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(await screen.findByRole("heading", { name: roboticsEvent.title })).toBeInTheDocument();

    resolveSemiconductor(responseFor(queryFor("semiconductor"), semiconductorEvent, "semiconductor-snapshot"));
    await waitFor(() => expect(screen.queryByRole("heading", { name: semiconductorEvent.title })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: roboticsEvent.title })).toBeInTheDocument();
  });

  it("does not let a stale refresh overwrite a newer filter response", async () => {
    const user = userEvent.setup();
    const filteredEvent = { ...directEvent, event_id: "bbbbbbbbbbbbbbbbbbbb", title: "全球科技筛选结果" };
    const staleRefreshEvent = { ...directEvent, event_id: "cccccccccccccccccccc", title: "旧筛选刷新结果" };
    vi.spyOn(api, "marketNewsEvents")
      .mockResolvedValueOnce({ ...marketNewsResponse, events: [directEvent] })
      .mockResolvedValueOnce({
        ...marketNewsResponse,
        events: [filteredEvent],
        filters: queryFor("storage", "global_tech"),
      });
    let resolveRefresh!: (value: typeof marketNewsResponse) => void;
    vi.spyOn(api, "marketNewsRefresh").mockImplementation(() => new Promise((resolve) => { resolveRefresh = resolve; }));
    render(<MarketNews />);
    await screen.findByRole("heading", { name: directEvent.title });

    await user.click(screen.getByRole("button", { name: "刷新资讯" }));
    await user.click(screen.getByRole("button", { name: "全球科技" }));
    expect(await screen.findByRole("heading", { name: filteredEvent.title })).toBeInTheDocument();

    resolveRefresh({ ...marketNewsResponse, events: [staleRefreshEvent] });
    await waitFor(() => expect(screen.queryByRole("heading", { name: staleRefreshEvent.title })).not.toBeInTheDocument());
    expect(screen.getByRole("heading", { name: filteredEvent.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新资讯" })).not.toBeDisabled();
  });

  it("disables refresh while a filter GET is still loading", async () => {
    let resolveGet!: (value: typeof marketNewsResponse) => void;
    vi.spyOn(api, "marketNewsEvents").mockImplementation(() => new Promise((resolve) => { resolveGet = resolve; }));
    const refresh = vi.spyOn(api, "marketNewsRefresh").mockResolvedValue(marketNewsResponse);
    render(<MarketNews />);

    expect(screen.getByRole("button", { name: "刷新资讯" })).toBeDisabled();
    expect(refresh).not.toHaveBeenCalled();
    resolveGet(marketNewsResponse);
    expect(await screen.findByRole("heading", { name: directEvent.title })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新资讯" })).not.toBeDisabled();
  });

  it("shows data explanation and all explicit degraded states", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({
      ...marketNewsResponse,
      events: [{ ...directEvent, summary: "AI摘要暂不可用", summary_status: "ai_unavailable", data_status: "stale" }],
      data_status: "stale", ai_status: "unavailable",
      source_summary: { ...marketNewsResponse.source_summary, failed_sources: 2, cache_status: "stale" },
    });
    render(<MarketNews />);
    expect(await screen.findByText("过期缓存")).toBeInTheDocument();
    expect(screen.getByText("2 个来源失败")).toBeInTheDocument();
    expect(screen.getByText("AI摘要暂不可用")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "数据说明" }));
    expect(screen.getByRole("dialog", { name: "市场资讯数据说明" })).toBeInTheDocument();
    expect(screen.getByText(/公开 RSS/)).toBeInTheDocument();
    expect(screen.getByText(/不会输出买入、卖出/)).toBeInTheDocument();
  });

  it("retries one failed source and replaces list, focus and impact with one matching response snapshot", async () => {
    const user = userEvent.setup();
    const sourceId = "0123456789abcdef";
    const initialEvent = eventFor("source-old-event", "来源重试前事件", "storage", "存储");
    const retriedEvent = eventFor("source-new-event", "来源重试后事件", "storage", "存储");
    const initial = responseFor(queryFor("storage"), initialEvent, "source-old-snapshot", 1);
    initial.source_summary = {
      ...initial.source_summary,
      failed_sources: 1,
      source_state: "partial_failure",
      source_statuses: [{
        source_id: sourceId,
        source_name: "存储公开源",
        source_url: "https://feed.example.test/storage.xml",
        status: "failed",
        error_type: "timeout",
        error_reason: "来源请求超时",
        last_success_at: "2026-08-16T10:35:00+08:00",
        used_cached_items: true,
        item_count: 1,
      }],
    };
    const retried = responseFor(queryFor("storage"), retriedEvent, "source-new-snapshot", 2);
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue(initial);
    const retry = vi.spyOn(api, "marketNewsRetrySource").mockResolvedValue(retried);

    render(<MarketNews />);
    expect(await screen.findByRole("heading", { name: initialEvent.title })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "1 个来源失败，查看详情" }));
    await user.click(screen.getByRole("button", { name: "重试来源 存储公开源" }));

    expect(retry).toHaveBeenCalledWith(sourceId, queryFor("storage"));
    expect(await screen.findByRole("heading", { name: retriedEvent.title })).toBeInTheDocument();
    expect(screen.getAllByText(retriedEvent.title)).toHaveLength(2);
    expect(screen.getByText("过去 7 天有 2 个事件与你的持仓相关")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: initialEvent.title })).not.toBeInTheDocument();
  });

  it("rejects a failed-source retry response whose filters do not match the current query", async () => {
    const user = userEvent.setup();
    const sourceId = "fedcba9876543210";
    const initialEvent = eventFor("retry-stable-event", "重试时保留的当前快照", "storage", "存储");
    const wrongEvent = eventFor("retry-wrong-event", "错误标签的重试响应", "robotics", "机器人");
    const initial = responseFor(queryFor("storage"), initialEvent, "retry-stable-snapshot", 1);
    initial.source_summary = {
      ...initial.source_summary,
      failed_sources: 1,
      source_state: "partial_failure",
      source_statuses: [{
        source_id: sourceId,
        source_name: "存储政策源",
        source_url: "https://feed.example.test/policy.xml",
        status: "failed",
        error_type: "http_status",
        error_reason: "来源返回 HTTP 503",
        last_success_at: null,
        used_cached_items: false,
        item_count: 0,
      }],
    };
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue(initial);
    vi.spyOn(api, "marketNewsRetrySource").mockResolvedValue(
      responseFor(queryFor("robotics"), wrongEvent, "retry-wrong-snapshot", 3),
    );

    render(<MarketNews />);
    expect(await screen.findByRole("heading", { name: initialEvent.title })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "1 个来源失败，查看详情" }));
    await user.click(screen.getByRole("button", { name: "重试来源 存储政策源" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该来源重试失败，请稍后再试。");
    expect(screen.getByRole("heading", { name: initialEvent.title })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: wrongEvent.title })).not.toBeInTheDocument();
  });

  it("persists a genuinely empty market-news tag selection and shows guidance", async () => {
    const user = userEvent.setup();
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: [], activeId: "" }));
    const load = vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [], empty_reason: "no_tags" });
    render(<MarketNews />);

    expect(await screen.findByText("还没有选择关注行业")).toBeInTheDocument();
    expect(screen.getByText("添加半导体、存储、机器人、医疗等标签后开始跟踪资讯")).toBeInTheDocument();
    const addButtons = screen.getAllByRole("button", { name: "添加标签" });
    await user.click(addButtons[addButtons.length - 1]);
    expect(screen.getByRole("dialog", { name: "添加投研标签" })).toBeInTheDocument();
    expect(load).not.toHaveBeenCalled();
  });

  it("shows portfolio read failure instead of factual zero impact", async () => {
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({
      ...marketNewsResponse,
      events: [],
      portfolio_status: "error",
      impact_summary: null,
      empty_reason: "portfolio_error",
    });
    render(<MarketNews />);

    expect(await screen.findByText("持仓数据读取失败，暂无法计算关联")).toBeInTheDocument();
    expect(screen.queryByText(/今天有 0 个事件与你的持仓相关/)).not.toBeInTheDocument();
  });

  it("shows no-holdings and no-events responses without fake associations", async () => {
    const user = userEvent.setup();
    const load = vi.spyOn(api, "marketNewsEvents")
      .mockResolvedValueOnce({ ...marketNewsResponse, events: [directEvent] })
      .mockResolvedValueOnce({
        ...marketNewsResponse,
        events: [],
        empty_reason: "no_holdings",
        portfolio_status: "empty",
        filters: queryFor("storage", "my_holdings"),
      })
      .mockResolvedValueOnce({
        ...marketNewsResponse,
        events: [],
        empty_reason: "no_events",
        filters: queryFor("storage", "global_tech"),
      });
    render(<MarketNews />);
    await screen.findByRole("heading", { name: directEvent.title });
    await user.click(screen.getByRole("button", { name: "我的持仓" }));
    expect(await screen.findByText("还没有基金持仓")).toBeInTheDocument();
    expect(screen.getByText("添加持仓后，系统会把基金公开重仓股与资讯关联")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "全球科技" }));
    expect(await screen.findByText("当前筛选暂无可靠资讯")).toBeInTheDocument();
    expect(screen.getByText("可以扩大时间范围、切换标签或刷新公开来源")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("switches the entire continuous industry report with the active top tag", async () => {
    const user = userEvent.setup();
    render(<IndustryResearch />);

    expect(screen.getByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(screen.getByRole("article", { name: "机器人行业研究报告" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
  });
});
