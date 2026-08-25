import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { createTagRequestCoordinator, tagRequestKey } from "../requestCoordinator";
import { loadCustomTagCatalog } from "../preferences";
import { TagSelector } from "../TagSelector";
import { usePageTags } from "../usePageTags";
import type { PageKey } from "../types";

interface ReportObject {
  industryId: string;
  snapshotId: string;
  metrics: string[];
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

describe("industry tag state and request isolation", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("reuses the built-in 先进封装 tag, then persists a novel custom tag and active order across remounts", async () => {
    const user = userEvent.setup();

    function Harness() {
      const tags = usePageTags("industry_research");
      return <div>
        <button onClick={() => tags.create("  先进封装  ")}>创建先进封装</button>
        <button onClick={() => tags.create("量子传感")}>创建量子传感</button>
        <output aria-label="标签状态">{JSON.stringify({
          activeId: tags.state.activeId,
          order: tags.state.order,
          names: tags.tags.map((tag) => tag.name),
        })}</output>
      </div>;
    }

    const first = render(<Harness />);
    await user.click(screen.getByRole("button", { name: "创建先进封装" }));
    expect(screen.getByLabelText("标签状态")).toHaveTextContent('"activeId":"advanced-packaging"');
    expect(loadCustomTagCatalog().items).toEqual([]);

    await user.click(screen.getByRole("button", { name: "创建量子传感" }));
    const persistedText = screen.getByLabelText("标签状态").textContent;
    expect(persistedText).toContain('"activeId":"custom-');
    expect(persistedText).toContain('"names":["量子传感"');
    first.unmount();

    render(<Harness />);
    expect(screen.getByLabelText("标签状态").textContent).toBe(persistedText);
  });

  it("keeps the selector open with a visible error when transactional persistence fails", async () => {
    const user = userEvent.setup();
    const originalSet = Storage.prototype.setItem;
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === "vr-page-tags:industry_research") {
        throw new DOMException("quota", "QuotaExceededError");
      }
      return originalSet.call(this, key, value);
    });
    function Harness() {
      const tags = usePageTags("industry_research");
      const [open, setOpen] = useState(true);
      return <TagSelector open={open} selectedIds={tags.state.ids} customTags={tags.customTags}
        onCreate={tags.create} onCancel={() => setOpen(false)} onConfirm={vi.fn()} />;
    }

    render(<Harness />);
    await user.type(screen.getByRole("textbox", { name: "自定义行业名称" }), "量子传感");
    await user.click(screen.getByRole("button", { name: "创建并激活" }));

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("标签创建未能完整保存");
    expect(loadCustomTagCatalog().items).toEqual([]);
    write.mockRestore();
  });

  it("binds semiconductor, storage, robotics and custom reports to independent page+industry keys", async () => {
    const coordinator = createTagRequestCoordinator<ReportObject>();
    const committed = new Map<string, ReportObject>();
    const industries = ["semiconductor", "storage", "robotics", "custom-independent"];

    for (const industryId of industries) {
      await coordinator.run({
        pageKey: "industry_research",
        industryId,
        request: async ({ queryKey }) => ({
          industryId,
          snapshotId: `snapshot-${queryKey}`,
          metrics: [`metric-${industryId}`],
        }),
        commit: (report, context) => committed.set(context.queryKey, report),
      });
    }

    expect([...committed.keys()]).toEqual([
      "17:industry_research13:semiconductor",
      "17:industry_research7:storage",
      "17:industry_research8:robotics",
      "17:industry_research18:custom-independent",
    ]);
    for (const industryId of industries) {
      const report = committed.get(tagRequestKey("industry_research", industryId));
      expect(report).toEqual({
        industryId,
        snapshotId: `snapshot-${tagRequestKey("industry_research", industryId)}`,
        metrics: [`metric-${industryId}`],
      });
    }
    expect(new Set([...committed.values()]).size).toBe(4);
  });

  it("aborts each prior request and ignores late responses across three rapid switches", async () => {
    const coordinator = createTagRequestCoordinator<ReportObject>();
    const ids = ["semiconductor", "storage", "robotics", "custom-independent"];
    const waits = new Map(ids.map((id) => [id, deferred<ReportObject>()]));
    const signals: AbortSignal[] = [];
    const committed: ReportObject[] = [];

    const runs = ids.map((industryId) => coordinator.run({
      pageKey: "industry_research",
      industryId,
      request: ({ signal }) => {
        signals.push(signal);
        return waits.get(industryId)!.promise;
      },
      commit: (report) => committed.push(report),
    }));

    waits.get("custom-independent")!.resolve({
      industryId: "custom-independent", snapshotId: "snapshot-custom", metrics: ["custom-only"],
    });
    waits.get("robotics")!.resolve({ industryId: "robotics", snapshotId: "snapshot-robot", metrics: ["robot-only"] });
    waits.get("storage")!.resolve({ industryId: "storage", snapshotId: "snapshot-storage", metrics: ["storage-only"] });
    waits.get("semiconductor")!.resolve({
      industryId: "semiconductor", snapshotId: "snapshot-semi", metrics: ["semi-only"],
    });
    await Promise.all(runs);

    expect(signals.map((signal) => signal.aborted)).toEqual([true, true, true, false]);
    expect(committed).toEqual([{
      industryId: "custom-independent", snapshotId: "snapshot-custom", metrics: ["custom-only"],
    }]);
    expect(coordinator.current()).toEqual({
      pageKey: "industry_research",
      industryId: "custom-independent",
      queryKey: "17:industry_research18:custom-independent",
      sequence: 4n,
    });
  });

  it("builds a length-prefixed request key with no tuple ambiguity", () => {
    expect(tagRequestKey("market_news", "a_b")).toBe("11:market_news3:a_b");
    expect(tagRequestKey("industry_research", "storage"))
      .toBe("17:industry_research7:storage");
  });

  it.each([
    ["blank industry", "industry_research", ""],
    ["control industry", "industry_research", "storage\n"],
    ["colon industry", "industry_research", "storage:robotics"],
    ["overlong industry", "industry_research", `a${"b".repeat(64)}`],
    ["invalid page", "market:news", "storage"],
    ["blank page", "", "storage"],
  ])("rejects %s before aborting the current request", async (_name, rawPageKey, industryId) => {
    const coordinator = createTagRequestCoordinator<ReportObject>();
    const pending = deferred<ReportObject>();
    let currentSignal!: AbortSignal;
    const first = coordinator.run({
      pageKey: "industry_research",
      industryId: "storage",
      request: ({ signal }) => { currentSignal = signal; return pending.promise; },
      commit: vi.fn(),
    });
    const invalidRequest = vi.fn(async () => ({
      industryId, snapshotId: "invalid", metrics: [],
    }));

    await expect(coordinator.run({
      pageKey: rawPageKey as PageKey,
      industryId,
      request: invalidRequest,
      commit: vi.fn(),
    })).rejects.toThrow("请求标签参数无效");

    expect(invalidRequest).not.toHaveBeenCalled();
    expect(currentSignal.aborted).toBe(false);
    expect(coordinator.current()?.industryId).toBe("storage");
    pending.resolve({ industryId: "storage", snapshotId: "current", metrics: [] });
    await expect(first).resolves.toBe("committed");
  });

  it("uses BigInt sequence and a unique active token beyond Number.MAX_SAFE_INTEGER", async () => {
    const coordinator = createTagRequestCoordinator<ReportObject>({
      initialSequence: 9007199254740991n,
    });
    const stale = deferred<ReportObject>();
    const committed: string[] = [];
    const first = coordinator.run({
      pageKey: "industry_research", industryId: "storage",
      request: () => stale.promise,
      commit: (report) => committed.push(report.snapshotId),
    });
    const second = coordinator.run({
      pageKey: "industry_research", industryId: "robotics",
      request: async () => ({ industryId: "robotics", snapshotId: "latest", metrics: [] }),
      commit: (report) => committed.push(report.snapshotId),
    });
    stale.resolve({ industryId: "storage", snapshotId: "stale", metrics: [] });

    await expect(first).resolves.toBe("ignored");
    await expect(second).resolves.toBe("committed");
    expect(committed).toEqual(["latest"]);
    expect(coordinator.current()?.sequence).toBe(9007199254740993n);
  });

  it("isolates stale rejection and a synchronous request throw", async () => {
    const coordinator = createTagRequestCoordinator<ReportObject>();
    const stale = deferred<ReportObject>();
    const first = coordinator.run({
      pageKey: "industry_research", industryId: "storage",
      request: () => stale.promise,
      commit: vi.fn(),
    });
    const committed: string[] = [];
    const second = coordinator.run({
      pageKey: "industry_research", industryId: "robotics",
      request: async () => ({ industryId: "robotics", snapshotId: "latest", metrics: [] }),
      commit: (report) => committed.push(report.snapshotId),
    });
    stale.reject(new Error("late failure"));
    await expect(first).resolves.toBe("ignored");
    await expect(second).resolves.toBe("committed");
    expect(committed).toEqual(["latest"]);

    await expect(coordinator.run({
      pageKey: "industry_research", industryId: "semiconductor",
      request: () => { throw new Error("sync failure"); },
      commit: vi.fn(),
    })).rejects.toThrow("sync failure");
    expect(coordinator.current()).toBeNull();
  });

  it("disposes pending work and refuses reuse", async () => {
    const coordinator = createTagRequestCoordinator<ReportObject>();
    const pending = deferred<ReportObject>();
    let signal!: AbortSignal;
    const commit = vi.fn();
    const run = coordinator.run({
      pageKey: "industry_research", industryId: "storage",
      request: (context) => { signal = context.signal; return pending.promise; },
      commit,
    });

    coordinator.dispose();
    expect(signal.aborted).toBe(true);
    expect(coordinator.current()).toBeNull();
    pending.resolve({ industryId: "storage", snapshotId: "late", metrics: [] });
    await expect(run).resolves.toBe("ignored");
    expect(commit).not.toHaveBeenCalled();
    await expect(coordinator.run({
      pageKey: "industry_research", industryId: "robotics",
      request: async () => ({ industryId: "robotics", snapshotId: "unused", metrics: [] }),
      commit,
    })).rejects.toThrow("请求协调器已释放");
  });
});
