import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { EvidenceCenter } from "./EvidenceCenter";

vi.mock("@/features/source-health/SourceHealthSummary", () => ({
  SourceHealthSummary: ({ onOpenDetails }: { onOpenDetails: () => void }) => <button onClick={onOpenDetails}>A1 健康摘要</button>,
}));
vi.mock("@/features/source-health/SourceHealthDrawer", () => ({
  SourceHealthDrawer: ({ open }: { open: boolean }) => open ? <div>数据源健康详情</div> : null,
}));

describe("EvidenceCenter", () => {
  beforeEach(() => window.history.replaceState({}, "", "/evidence-center"));

  it("renders the explicitly labeled frontend-demo coverage cards", () => {
    render(<EvidenceCenter />);

    expect(screen.getByRole("heading", { name: "证据中心" })).toBeInTheDocument();
    expect(screen.getByText("查看资讯的核验状态、一手证据、独立来源与更正记录")).toBeInTheDocument();
    expect(screen.getByText("73%")).toBeInTheDocument();
    expect(screen.getByText("41")).toBeInTheDocument();
    expect(screen.getByText("22")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText(/完成核验的比例，不表示.*是真的/)).toBeInTheDocument();
    expect(screen.getAllByText("前端演示 Fixture").length).toBeGreaterThan(0);
  });

  it("switches internal tabs and reuses the A1 source-health components", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    await user.click(screen.getByRole("tab", { name: "数据源健康" }));

    expect(screen.getByText(/来源能否访问、能否解析、是否新鲜/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "A1 健康摘要" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "A1 健康摘要" }));
    expect(screen.getByText("数据源健康详情")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "更正记录" }));
    expect(screen.getByText("原始状态")).toBeInTheDocument();
    const record = screen.getAllByRole("button", { name: /查看记录/ })[0];
    await user.click(record);
    expect(screen.getByRole("dialog", { name: "证据详情" })).toHaveTextContent("状态历史");
  });

  it("filters and sorts frontend fixtures only", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    await user.click(screen.getByRole("button", { name: "待核验" }));
    expect(screen.getByText("北辰机器人供应计划仍待核验")).toBeInTheDocument();
    expect(screen.queryByText("星河科技发布算力中心建设公告")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "全部" }));
    await user.selectOptions(screen.getByLabelText("排序方式"), "impact");
    expect(within(screen.getByRole("main")).getAllByRole("article")[0]).toHaveTextContent("星河科技发布算力中心建设公告");
  });

  it("shows only the explicit fictional multi-source fixture when filtering 多源印证", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    await user.click(screen.getByRole("button", { name: "多源印证" }));

    expect(screen.getByText("云岭半导体产能规划获多源印证")).toBeInTheDocument();
    expect(screen.queryByText("星河科技发布算力中心建设公告")).not.toBeInTheDocument();
  });

  it("shows evidence, explains reprints, restores focus on Escape, and keeps source actions as a prototype", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    const trigger = screen.getAllByRole("button", { name: "查看证据" })[0];
    await user.click(trigger);

    expect(screen.getByRole("dialog", { name: "证据详情" })).toHaveTextContent("核心主张");
    expect(screen.getByText("这些来源来自同一原始稿件，不重复计算为独立证据。")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开原始证据" }));
    expect(screen.getByText("当前为页面原型，尚未绑定正式原始文件。")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "证据详情" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("auto-opens a matching event_id and supplies the planned prototype prompts", async () => {
    const user = userEvent.setup();
    window.history.replaceState({}, "", "/evidence-center?event_id=galaxy-compute-center");
    render(<EvidenceCenter />);

    expect(screen.getByRole("dialog", { name: "证据详情" })).toHaveTextContent("星河科技发布算力中心建设公告");
    await user.click(screen.getByRole("button", { name: "关闭证据详情" }));
    await user.click(screen.getByRole("button", { name: "运行核验" }));
    expect(screen.getByText("真实性核验能力将在 A1.1-W1 接入")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "数据说明" }));
    expect(screen.getByText("AI 翻译、AI 摘要和来源数量不会自动提高核验等级。", { exact: false })).toBeInTheDocument();
  });

  it("opens the matching 已核验 to 已更正 correction fixture in the shared evidence drawer", async () => {
    const user = userEvent.setup();
    render(<EvidenceCenter />);
    await user.click(screen.getByRole("tab", { name: "更正记录" }));
    await user.click(screen.getByRole("button", { name: "查看记录 星河科技更正算力中心公告细节" }));

    const drawer = screen.getByRole("dialog", { name: "证据详情" });
    expect(drawer).toHaveTextContent("已更正");
    expect(drawer).toHaveTextContent("原始公告的建设周期说明已被更正");
    expect(drawer).toHaveTextContent("状态历史");
  });
});
