import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { MarketNews } from "@/pages/MarketNews";
import { directEvent, marketNewsResponse } from "./fixtures";

const verified = { ...directEvent, event_id: "11111111111111111111", verification_status: "verified" as const, verification_reason: "已有明确官方证据", verified_at: "2026-08-18T08:30:00+00:00", verified_key_fields: [] };
const unverified = { ...directEvent, event_id: "22222222222222222222", title: "未核验金额 12亿元", summary: "未经核验的 12亿元", verification_status: "unverified" as const };

describe("MarketNews trusted evidence admission", () => {
  beforeEach(() => { localStorage.clear(); localStorage.setItem("vr-page-tags:market_news", JSON.stringify({ ids: ["semiconductor"], activeId: "semiconductor" })); vi.restoreAllMocks(); window.history.replaceState({}, "", "/market-news"); });

  it("defensively renders only trusted backend events and links to their evidence", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [verified, unverified], focus_events: [verified], evidence_snapshot_id: "e".repeat(20), filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    render(<MarketNews />);
    const card = await screen.findByRole("article", { name: `${verified.title}事件卡` });
    expect(within(card).getByText("已核验")).toBeInTheDocument(); expect(screen.queryByText("未核验金额 12亿元")).not.toBeInTheDocument(); expect(screen.queryByText(/前端演示 Fixture/)).not.toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "查看证据" }));
    expect(window.location.pathname).toBe("/evidence-center"); expect(window.location.search).toBe("?event_id=11111111111111111111");
  });

  it("uses the backend trusted-empty explanation without fixture fallback", async () => {
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [], focus_events: [], evidence_snapshot_id: "e".repeat(20), empty_reason: "no_events", empty_message: "当前筛选无已核验或多源印证的资讯。", filters: { ...marketNewsResponse.filters, tag_ids: ["semiconductor"] } });
    render(<MarketNews />);
    expect(await screen.findByText("当前筛选无已核验或多源印证的资讯。")).toBeInTheDocument(); expect(screen.queryByText(/星河科技发布算力中心建设公告/)).not.toBeInTheDocument();
  });
});
