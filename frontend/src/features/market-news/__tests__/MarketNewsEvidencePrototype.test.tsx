import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { EvidenceCenter } from "@/features/evidence-center/EvidenceCenter";
import { directEvent, marketNewsResponse } from "./fixtures";
import { api } from "@/lib/api";
import { MarketNews } from "@/pages/MarketNews";
import { marketNewsDisplayEvents, marketNewsPrototypeEvent } from "@/features/market-news/prototype";
import type { MarketNewsQuery } from "@/features/market-news/types";

function query(overrides: Partial<MarketNewsQuery> = {}): MarketNewsQuery {
  return { mode: "my_focus", tag_ids: ["semiconductor"], category: "all", days: 7, sort: "importance", ...overrides };
}

describe("MarketNews evidence-center prototype entry", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["semiconductor"], activeId: "semiconductor" }));
    vi.restoreAllMocks();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [directEvent], filters: query() });
  });

  it.each([
    ["category", query({ category: "policy" })],
    ["tag", query({ tag_ids: ["storage"] })],
    ["mode", query({ mode: "global_tech" })],
  ])("excludes the display-only prototype for a nonmatching %s query", (_kind, filters) => {
    const backendEvents = [directEvent];

    expect(marketNewsDisplayEvents(backendEvents, filters)).toEqual(backendEvents);
    expect(backendEvents).toEqual([directEvent]);
  });

  it("preserves backend event order when the prototype is excluded", () => {
    const lowerImportance = { ...directEvent, event_id: "lower-importance", importance_score: 1 };

    expect(marketNewsDisplayEvents([lowerImportance, directEvent], query({ category: "policy" })).map((event) => event.event_id))
      .toEqual([lowerImportance.event_id, directEvent.event_id]);
  });

  it("places the matching prototype with the canonical importance, latest, and holding-relevance ordering", () => {
    expect(marketNewsDisplayEvents([directEvent], query({ sort: "importance" })).map((event) => event.event_id))
      .toEqual([directEvent.event_id, marketNewsPrototypeEvent.event_id]);
    expect(marketNewsDisplayEvents([directEvent], query({ sort: "latest" })).map((event) => event.event_id))
      .toEqual([marketNewsPrototypeEvent.event_id, directEvent.event_id]);
    expect(marketNewsDisplayEvents([directEvent], query({ sort: "holding_relevance" })).map((event) => event.event_id))
      .toEqual([directEvent.event_id, marketNewsPrototypeEvent.event_id]);
  });

  it("keeps backend events unverified and routes only the explicit demo fixture to its matching evidence drawer", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/market-news");
    render(<BrowserRouter><Routes><Route path="/market-news" element={<MarketNews />} /><Route path="/evidence-center" element={<EvidenceCenter />} /></Routes></BrowserRouter>);

    const ordinaryCard = await screen.findByRole("article", { name: `${directEvent.title}事件卡` });
    expect(within(ordinaryCard).queryByText("已核验")).not.toBeInTheDocument();
    expect(within(ordinaryCard).queryByRole("button", { name: "查看证据" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看证据" }));
    expect(window.location.pathname).toBe("/evidence-center");
    expect(window.location.search).toBe("?event_id=galaxy-compute-center");
    expect(await screen.findByRole("dialog", { name: "证据详情" })).toHaveTextContent("星河科技发布算力中心建设公告");
  });
});
