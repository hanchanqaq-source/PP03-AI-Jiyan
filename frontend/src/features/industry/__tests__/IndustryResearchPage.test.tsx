import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { IndustryResearch } from "@/pages/IndustryResearch";
import { api } from "@/lib/api";
import { industryResponseWire, jsonResponse } from "./fixtures";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("industry research page data boundary", () => {
  beforeEach(() => {
    localStorage.clear();
    history.replaceState(null, "", "/industry-research");
    vi.restoreAllMocks();
  });

  it("loads one atomic 90-day report and removes the Radar bypass", async () => {
    const load = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(industryResponseWire()));
    const radar = vi.spyOn(api, "radar");
    render(<IndustryResearch />);

    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    expect(String(load.mock.calls[0][0])).toContain("/industry-research/storage?window_days=90");
    expect(load.mock.calls[0][1]?.signal).toBeInstanceOf(AbortSignal);
    expect(radar).not.toHaveBeenCalled();
    expect(screen.getAllByText("TRUSTED-STORAGE-1").length).toBeGreaterThan(0);
    expect(screen.getByText(/当前显示的旧可信快照/)).toBeInTheDocument();
  });

  it("never relabels a previous report while a later industry request is pending", async () => {
    const user = userEvent.setup();
    const storage = deferred<Response>();
    const robotics = deferred<Response>();
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => String(input).includes("/storage?") ? storage.promise : robotics.promise);
    render(<IndustryResearch />);

    await act(async () => storage.resolve(jsonResponse(industryResponseWire("storage"))));
    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(screen.getByText(/正在读取机器人/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /行业研究报告/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到存储" })).toHaveAttribute("aria-pressed", "true");

    await act(async () => robotics.resolve(jsonResponse(industryResponseWire("robotics"))));
    expect(await screen.findByRole("article", { name: "机器人行业研究报告" })).toBeInTheDocument();
    expect(screen.queryByText("DRAM 价格")).not.toBeInTheDocument();
  });

  it("uses one compound sticky region and updates the current anchor in the URL", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(industryResponseWire()));
    const { container } = render(<IndustryResearch />);
    await screen.findByRole("article", { name: "存储行业研究报告" });

    expect(container.querySelectorAll(".sticky.top-0")).toHaveLength(1);
    const metrics = screen.getByRole("link", { name: "核心数据" });
    await user.click(metrics);
    expect(location.hash).toBe("#metrics");
    expect(metrics).toHaveAttribute("aria-current", "location");
  });

  it("filters trusted and candidate events locally for 7/30/90 days without refresh or extra GET", async () => {
    const user = userEvent.setup();
    const load = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(industryResponseWire()));
    const refresh = vi.spyOn(api, "industryResearchRefresh");
    render(<IndustryResearch />);
    await screen.findByText("STORAGE-NEWS-90");

    await user.click(screen.getByRole("button", { name: "最近 7 天" }));
    expect(screen.getByText("STORAGE-NEWS-7")).toBeInTheDocument();
    expect(screen.queryByText("STORAGE-NEWS-30")).not.toBeInTheDocument();
    expect(screen.queryByText("STORAGE-NEWS-90")).not.toBeInTheDocument();
    expect(screen.getByText("STORAGE-CANDIDATE-RECENT")).toBeInTheDocument();
    expect(screen.queryByText("STORAGE-NEWS-FUTURE")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "最近 30 天" }));
    expect(screen.getByText("STORAGE-NEWS-30")).toBeInTheDocument();
    expect(screen.getByText("STORAGE-CONFLICT-30")).toBeInTheDocument();
    expect(load).toHaveBeenCalledTimes(1);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("keeps unknown custom tags in an explicit building state without fabricated report content", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const match = String(input).match(/industry-research\/([^?]+)/);
      const industryId = match ? decodeURIComponent(match[1]) : "storage";
      return jsonResponse(industryResponseWire(industryId));
    });
    render(<IndustryResearch />);
    await screen.findByRole("article", { name: "存储行业研究报告" });
    await user.click(screen.getByRole("button", { name: "添加标签" }));
    const dialog = screen.getByRole("dialog", { name: "添加投研标签" });
    await user.type(within(dialog).getByRole("textbox", { name: "自定义行业名称" }), "先进封装观察");
    await user.click(within(dialog).getByRole("button", { name: "创建并激活" }));
    await user.click(within(dialog).getByRole("button", { name: "确认添加" }));

    expect(await screen.findByText("标签已保存，报告模板建设中；系统不会根据标签名称自动补造行业数据")).toBeInTheDocument();
    expect(screen.queryByText("DRAM 价格")).not.toBeInTheDocument();
  });

  it("exposes the four truth axes and opens evidence from the keyboard", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(industryResponseWire()));
    render(<IndustryResearch />);
    const button = (await screen.findAllByRole("button", { name: "查看 DRAM 价格证据" }))[0];

    button.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("dialog", { name: "DRAM 价格证据" })).toBeInTheDocument();
    expect(screen.getAllByText("source-official.example").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已核验").length).toBeGreaterThan(0);
    expect(screen.getAllByText("数据可用").length).toBeGreaterThan(0);
    expect(screen.getAllByText("数据新鲜").length).toBeGreaterThan(0);
    expect(screen.getAllByText("来源正常").length).toBeGreaterThan(0);
  });

  it("keeps the old active tag and old report when atomic activation persistence is refused", async () => {
    const user = userEvent.setup();
    const robotics = deferred<Response>();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input).includes("/storage?")
      ? jsonResponse(industryResponseWire("storage")) : robotics.promise);
    render(<IndustryResearch />);
    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    localStorage.setItem("vr-page-tags:industry_research", "{broken");
    await act(async () => robotics.resolve(jsonResponse(industryResponseWire("robotics"))));

    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "机器人行业研究报告" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到存储" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("alert").find((node) => node.textContent?.includes("页面标签存储已损坏，无法安全修改"))).toBeDefined();
  });
});
