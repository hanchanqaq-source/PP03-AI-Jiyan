import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { EvidenceCenter } from "@/features/evidence-center/EvidenceCenter";
import { directEvent, marketNewsResponse } from "./fixtures";
import { api } from "@/lib/api";
import { MarketNews } from "@/pages/MarketNews";

describe("MarketNews evidence-center prototype entry", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "marketNewsEvents").mockResolvedValue({ ...marketNewsResponse, events: [directEvent] });
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
