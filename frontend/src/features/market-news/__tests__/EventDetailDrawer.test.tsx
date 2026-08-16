import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { EventDetailDrawer } from "@/features/market-news/EventDetailDrawer";
import { directEvent } from "./fixtures";

function Harness() {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>打开测试事件</button><EventDetailDrawer open={open} event={directEvent} onClose={() => setOpen(false)} /></>;
}

describe("MarketNews EventDetailDrawer", () => {
  it("shows all sources, evidence, impact basis, and missing information", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "打开测试事件" }));

    const dialog = screen.getByRole("dialog", { name: `${directEvent.title}事件详情` });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("来源一")).toBeInTheDocument();
    expect(screen.getByText("来源二")).toBeInTheDocument();
    expect(screen.getByText("北方华创发布报告")).toBeInTheDocument();
    expect(screen.getByText("北方华创半年报披露")).toBeInTheDocument();
    expect(screen.getByText(/最早.*09:00/)).toBeInTheDocument();
    expect(screen.getByText(/最新.*10:35/)).toBeInTheDocument();
    expect(screen.getAllByText("东方人工智能主题混合C（017811）").length).toBeGreaterThan(0);
    expect(screen.getAllByText("北方华创（002371）").length).toBeGreaterThan(0);
    expect(screen.getByText(/披露日期：2026-06-30/)).toBeInTheDocument();
    expect(screen.getByText(/申银万国行业分类标准/)).toBeInTheDocument();
    expect(screen.getByText("新闻直接命中最新公开重仓公司")).toBeInTheDocument();
    expect(screen.getByText("AI摘要暂不可用")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /打开原始来源/ })).toHaveLength(2);
  });

  it("closes on Escape and restores focus to the trigger", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const trigger = screen.getByRole("button", { name: "打开测试事件" });
    await user.click(trigger);
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
