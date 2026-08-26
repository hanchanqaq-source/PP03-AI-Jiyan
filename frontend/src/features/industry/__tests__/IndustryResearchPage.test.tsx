import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { IndustryResearch } from "@/pages/IndustryResearch";
import { api } from "@/lib/api";
import { industryResponseWire, jsonResponse } from "./fixtures";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

async function createCustomTag(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name: "添加标签" }));
  const dialog = screen.getByRole("dialog", { name: "添加投研标签" });
  await user.type(within(dialog).getByRole("textbox", { name: "自定义行业名称" }), name);
  await user.click(within(dialog).getByRole("button", { name: "创建并激活" }));
}

function candidateOnlyWire() {
  const wire = industryResponseWire("storage");
  wire.displayed_industry_id = null;
  wire.displayed_trusted_report = null;
  wire.refresh_run.displayed_trusted_snapshot_id = null;
  wire.refresh_run.displayed_raw_snapshot_id = null;
  wire.refresh_run.displayed_evidence_snapshot_id = null;
  wire.candidate_evidence.unverified_events[1].occurred_at = "2026-06-01T00:00:00+00:00";
  return wire;
}

describe("industry research page data boundary", () => {
  beforeEach(() => {
    localStorage.clear();
    history.replaceState(null, "", "/industry-research");
    vi.restoreAllMocks();
  });

  it("preserves a legal initial hash but clears it and scrolls only after a successful user switch", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    history.replaceState(null, "", "/industry-research#metrics");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const industryId = String(input).includes("/robotics?") ? "robotics" : "storage";
      return jsonResponse(industryResponseWire(industryId));
    });
    const { container } = render(<IndustryResearch />);
    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    expect(location.hash).toBe("#metrics");
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(container.querySelector("[data-industry-report-top]")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(await screen.findByRole("article", { name: "机器人行业研究报告" })).toBeInTheDocument();
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(location.hash).toBe("");
    expect(screen.getByRole("link", { name: "总览" })).toHaveAttribute("aria-current", "location");
  });

  it("treats deleting the active tag bar item as user navigation", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    history.replaceState(null, "", "/industry-research#metrics");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const industryId = String(input).includes("/semiconductor?") ? "semiconductor" : "storage";
      return jsonResponse(industryResponseWire(industryId));
    });
    render(<IndustryResearch />);
    await screen.findByRole("article", { name: "存储行业研究报告" });

    await user.click(screen.getByRole("button", { name: "删除存储" }));

    expect(await screen.findByRole("article", { name: "半导体行业研究报告" })).toBeInTheDocument();
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(location.hash).toBe("");
  });

  it("atomically cancels an initial pending report and immediately loads the persisted next active tag", async () => {
    const user = userEvent.setup();
    const storage = deferred<Response>();
    const semiconductor = deferred<Response>();
    let storageSignal: AbortSignal | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (String(input).includes("/storage?")) {
        storageSignal = init?.signal ?? undefined;
        return storage.promise;
      }
      return semiconductor.promise;
    });
    render(<IndustryResearch />);
    expect(await screen.findByText(/正在读取存储/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "删除存储" }));

    expect(storageSignal?.aborted).toBe(true);
    expect(await screen.findByText(/正在读取半导体/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到半导体" })).toHaveAttribute("aria-pressed", "true");
    await act(async () => storage.resolve(jsonResponse(industryResponseWire("storage"))));
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
    await act(async () => semiconductor.resolve(jsonResponse(industryResponseWire("semiconductor"))));
    expect(await screen.findByRole("article", { name: "半导体行业研究报告" })).toBeInTheDocument();
  });

  it("keeps the persisted next active tag but never restores the old report when its GET fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/storage?")) return jsonResponse(industryResponseWire("storage"));
      throw new Error("offline");
    });
    render(<IndustryResearch />);
    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "删除存储" }));

    expect(await screen.findByText(/行业报告读取失败/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到半导体" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
    expect(screen.getByText("暂无可靠数据")).toBeInTheDocument();
    expect(screen.getByText("当前已选择半导体，但暂无可信快照。")).toBeInTheDocument();
  });

  it("treats selector removal of the active tag as user navigation", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    history.replaceState(null, "", "/industry-research#metrics");
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const industryId = String(input).includes("/semiconductor?") ? "semiconductor" : "storage";
      return jsonResponse(industryResponseWire(industryId));
    });
    render(<IndustryResearch />);
    await screen.findByRole("article", { name: "存储行业研究报告" });
    await user.click(screen.getByRole("button", { name: "添加标签" }));
    const dialog = screen.getByRole("dialog", { name: "添加投研标签" });
    await user.click(within(dialog).getByRole("checkbox", { name: "存储" }));
    await user.click(within(dialog).getByRole("button", { name: "确认添加" }));

    expect(await screen.findByRole("article", { name: "半导体行业研究报告" })).toBeInTheDocument();
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(location.hash).toBe("");
  });

  it("atomically applies selector replacement while the initial request is pending and ignores its late success", async () => {
    const user = userEvent.setup();
    const storage = deferred<Response>();
    const semiconductor = deferred<Response>();
    let storageSignal: AbortSignal | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (String(input).includes("/storage?")) {
        storageSignal = init?.signal ?? undefined;
        return storage.promise;
      }
      return semiconductor.promise;
    });
    render(<IndustryResearch />);
    expect(await screen.findByText(/正在读取存储/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "添加标签" }));
    const dialog = screen.getByRole("dialog", { name: "添加投研标签" });
    await user.click(within(dialog).getByRole("checkbox", { name: "存储" }));
    await user.click(within(dialog).getByRole("button", { name: "确认添加" }));

    expect(storageSignal?.aborted).toBe(true);
    expect(await screen.findByText(/正在读取半导体/)).toBeInTheDocument();
    await act(async () => storage.resolve(jsonResponse(industryResponseWire("storage"))));
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
    await act(async () => semiconductor.resolve(jsonResponse(industryResponseWire("semiconductor"))));
    expect(await screen.findByRole("article", { name: "半导体行业研究报告" })).toBeInTheDocument();
  });

  it("cancels the coordinator and clears all report state when the user deletes the final tag", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    localStorage.setItem("vr-page-tags:industry_research", JSON.stringify({ version: 2, ids: ["storage"], activeId: "storage", order: ["storage"] }));
    history.replaceState(null, "", "/industry-research#metrics");
    const pending = deferred<Response>();
    let signal: AbortSignal | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((_input, init) => {
      signal = init?.signal ?? undefined;
      return pending.promise;
    });
    render(<IndustryResearch />);
    expect(await screen.findByText(/正在读取存储/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "删除存储" }));

    expect(signal?.aborted).toBe(true);
    expect(screen.queryByText(/正在读取存储/)).not.toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /行业研究报告/ })).not.toBeInTheDocument();
    expect(screen.getByText("当前未选择行业标签；暂无可显示的可信快照。")).toBeInTheDocument();
    expect(location.hash).toBe("");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
  });

  it("preserves a legal hash and does not scroll for an initial empty selection", () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    localStorage.setItem("vr-page-tags:industry_research", JSON.stringify({ version: 2, ids: [], activeId: "", order: [] }));
    history.replaceState(null, "", "/industry-research#metrics");
    const load = vi.spyOn(globalThis, "fetch");

    render(<IndustryResearch />);

    expect(screen.getByText("当前未选择行业标签；暂无可显示的可信快照。")).toBeInTheDocument();
    expect(load).not.toHaveBeenCalled();
    expect(location.hash).toBe("#metrics");
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("keeps the current report, hash, and scroll position when active-tag deletion persistence fails", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(industryResponseWire()));
    render(<IndustryResearch />);
    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    history.replaceState(null, "", "/industry-research#metrics");
    localStorage.setItem("vr-page-tags:industry_research", "{broken");

    await user.click(screen.getByRole("button", { name: "删除存储" }));

    expect(screen.getByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到存储" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("alert").find((node) => node.textContent?.includes("页面标签存储已损坏，无法安全修改"))).toBeDefined();
    expect(location.hash).toBe("#metrics");
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("still completes a user switch when the host has no scrollIntoView capability", async () => {
    const user = userEvent.setup();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: undefined });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const industryId = String(input).includes("/robotics?") ? "robotics" : "storage";
      return jsonResponse(industryResponseWire(industryId));
    });
    render(<IndustryResearch />);
    await screen.findByRole("article", { name: "存储行业研究报告" });
    history.replaceState(null, "", "/industry-research#metrics");
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    expect(await screen.findByRole("article", { name: "机器人行业研究报告" })).toBeInTheDocument();
    await act(async () => { await new Promise((done) => requestAnimationFrame(done)); });
    expect(location.hash).toBe("");
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
    expect(screen.queryByRole("navigation", { name: "行业报告内部导航" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到存储" })).toHaveAttribute("aria-pressed", "true");

    await act(async () => robotics.resolve(jsonResponse(industryResponseWire("robotics"))));
    expect(await screen.findByRole("article", { name: "机器人行业研究报告" })).toBeInTheDocument();
    expect(screen.queryByText("DRAM 价格")).not.toBeInTheDocument();
  });

  it("renders decoder-validated candidate-only evidence without inventing a trusted report", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(candidateOnlyWire()));
    render(<IndustryResearch />);

    const article = await screen.findByRole("article", { name: "存储候选证据报告" });
    expect(article).toHaveTextContent("暂无可信快照");
    expect(article).toHaveTextContent("待核验候选指标");
    expect(article).toHaveTextContent("storage_conflict · 冲突值并列");
    expect(article).toHaveTextContent("E-CONFLICT-A");
    expect(article).toHaveTextContent("STORAGE-CANDIDATE-RECENT");
    expect(article).toHaveTextContent("STORAGE-CANDIDATE-OLD");
    expect(article).toHaveTextContent("STORAGE-CONFLICT-30");
    expect(article).toHaveTextContent("支持证据 E-CONFLICT-A");
    expect(article).toHaveTextContent("反驳证据 E-CONFLICT-B");
    expect(article).not.toHaveTextContent("行业总览");
    expect(article).not.toHaveTextContent("DRAM 价格");
    expect(screen.getByRole("link", { name: "核心数据" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "新闻与风险" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "周期" })).not.toBeInTheDocument();
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
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const match = String(input).match(/industry-research\/([^?]+)/);
      const industryId = match ? decodeURIComponent(match[1]) : "storage";
      return jsonResponse(industryResponseWire(industryId));
    });
    render(<IndustryResearch />);
    await screen.findByRole("article", { name: "存储行业研究报告" });
    history.replaceState(null, "", "/industry-research#metrics");
    await createCustomTag(user, "先进封装观察");

    expect(await screen.findByText("该行业报告正在建设")).toBeInTheDocument();
    expect(screen.queryByText("DRAM 价格")).not.toBeInTheDocument();
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    expect(location.hash).toBe("");
  });

  it("immediately pre-activates a created custom tag and ignores a successful old pending response", async () => {
    const user = userEvent.setup();
    const storage = deferred<Response>();
    const custom = deferred<Response>();
    let storageSignal: AbortSignal | undefined;
    let customIndustryId = "";
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = String(input);
      if (url.includes("/storage?")) {
        storageSignal = init?.signal ?? undefined;
        return storage.promise;
      }
      customIndustryId = decodeURIComponent(url.match(/industry-research\/([^?]+)/)?.[1] ?? "");
      return custom.promise;
    });
    render(<IndustryResearch />);
    expect(await screen.findByText(/正在读取存储/)).toBeInTheDocument();

    await createCustomTag(user, "量子传感观察");

    expect(storageSignal?.aborted).toBe(true);
    expect(customIndustryId).toMatch(/^custom-/);
    expect(screen.getByRole("button", { name: "切换到量子传感观察" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText(/正在读取量子传感观察/)).toBeInTheDocument();
    await act(async () => storage.resolve(jsonResponse(industryResponseWire("storage"))));
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
    expect(screen.getByText(/正在读取量子传感观察/)).toBeInTheDocument();
    await act(async () => custom.resolve(jsonResponse(industryResponseWire(customIndustryId))));
    expect(await screen.findByText("该行业报告正在建设")).toBeInTheDocument();
  });

  it("keeps a completed custom building state when the cancelled old response arrives late", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    history.replaceState(null, "", "/industry-research#metrics");
    const storage = deferred<Response>();
    const custom = deferred<Response>();
    let customIndustryId = "";
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/storage?")) return storage.promise;
      customIndustryId = decodeURIComponent(url.match(/industry-research\/([^?]+)/)?.[1] ?? "");
      return custom.promise;
    });
    render(<IndustryResearch />);
    expect(await screen.findByText(/正在读取存储/)).toBeInTheDocument();
    await createCustomTag(user, "低空经济观察");

    await act(async () => custom.resolve(jsonResponse(industryResponseWire(customIndustryId))));
    expect(await screen.findByText("该行业报告正在建设")).toBeInTheDocument();
    expect(location.hash).toBe("");
    await waitFor(() => expect(scrollIntoView).toHaveBeenCalledTimes(1));
    await act(async () => storage.resolve(jsonResponse(industryResponseWire("storage"))));
    expect(screen.getByText("该行业报告正在建设")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到低空经济观察" })).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps a failed custom target active and empty while cancelling its initial pending predecessor", async () => {
    const user = userEvent.setup();
    const storage = deferred<Response>();
    let storageSignal: AbortSignal | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (String(input).includes("/storage?")) {
        storageSignal = init?.signal ?? undefined;
        return storage.promise;
      }
      return Promise.reject(new Error("custom unavailable"));
    });
    render(<IndustryResearch />);
    expect(await screen.findByText(/正在读取存储/)).toBeInTheDocument();

    await createCustomTag(user, "工业软件观察");

    expect(storageSignal?.aborted).toBe(true);
    expect(await screen.findByText(/行业报告读取失败/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到工业软件观察" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("当前已选择工业软件观察，但暂无可信快照。")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /行业研究报告/ })).not.toBeInTheDocument();
  });

  it("does not restore an already displayed trusted report when the created custom target fails", async () => {
    const user = userEvent.setup();
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input).includes("/storage?")) return jsonResponse(industryResponseWire("storage"));
      throw new Error("custom unavailable");
    });
    render(<IndustryResearch />);
    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    history.replaceState(null, "", "/industry-research#metrics");

    await createCustomTag(user, "商业航天观察");

    expect(await screen.findByText(/行业报告读取失败/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到商业航天观察" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("article", { name: "存储行业研究报告" })).not.toBeInTheDocument();
    expect(screen.getByText("当前已选择商业航天观察，但暂无可信快照。")).toBeInTheDocument();
    expect(location.hash).toBe("#metrics");
    expect(scrollIntoView).not.toHaveBeenCalled();
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
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scrollIntoView });
    const robotics = deferred<Response>();
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input).includes("/storage?")
      ? jsonResponse(industryResponseWire("storage")) : robotics.promise);
    render(<IndustryResearch />);
    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();

    history.replaceState(null, "", "/industry-research#metrics");
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    localStorage.setItem("vr-page-tags:industry_research", "{broken");
    await act(async () => robotics.resolve(jsonResponse(industryResponseWire("robotics"))));

    expect(await screen.findByRole("article", { name: "存储行业研究报告" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "机器人行业研究报告" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "切换到存储" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("alert").find((node) => node.textContent?.includes("页面标签存储已损坏，无法安全修改"))).toBeDefined();
    expect(location.hash).toBe("#metrics");
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
